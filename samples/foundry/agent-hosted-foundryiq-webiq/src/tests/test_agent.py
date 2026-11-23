import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent import Configuration, ToolboxAuthentication, build_agent, expose_evidence
from main import MetadataOnlyAdapter, MetadataTracer


CONFIG = Configuration(
    "https://demo.services.ai.azure.com/api/projects/demo/openai/v1/",
    "chat",
    "https://demo.services.ai.azure.com/api/projects/demo/toolboxes/iq-tools/mcp?api-version=v1",
)


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def test_telemetry_reuses_host_exporter_without_recording_content(self):
        adapter = MagicMock()
        adapter.get_agent_identifier.return_value = "iq-agent:7"
        with patch("main.MetadataTracer") as tracer:
            MetadataOnlyAdapter.init_tracing_internal(
                adapter, app_insights_conn_str="must-not-create-a-second-exporter"
            )
        tracer.assert_called_once_with(
            connection_string="", enable_content_recording=False, name="iq-agent:7"
        )

    def test_tool_queries_are_not_recorded_in_telemetry(self):
        tracer = MetadataTracer(connection_string="", enable_content_recording=False)
        with patch("main.AzureAIOpenTelemetryTracer.on_tool_start") as start:
            tracer.on_tool_start(
                {"name": "foundryiq"}, "private question",
                inputs={"query_variants": ["private question"]}, run_id="run",
            )
        start.assert_called_once_with({"name": "foundryiq"}, "", inputs=None, run_id="run")

    def test_exported_spans_keep_operations_but_redact_private_content(self):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        with patch(
            "langchain_azure_ai.callbacks.tracers.inference_tracing.otel_trace.get_tracer",
            return_value=provider.get_tracer("privacy-test"),
        ):
            tracer = MetadataTracer(connection_string="", enable_content_recording=False)
        model_run, tool_run = uuid4(), uuid4()
        tracer.on_chat_model_start(
            {"name": "ChatOpenAI"}, [[HumanMessage(content="PRIVATE_PROMPT")]],
            run_id=model_run, metadata={"ls_model_name": "chat"},
        )
        tracer.on_llm_end(
            LLMResult(generations=[[ChatGeneration(message=AIMessage(content="PRIVATE_RESPONSE"))]]),
            run_id=model_run,
        )
        tracer.on_tool_start(
            {"name": "foundryiq___knowledge_base_retrieve"}, "PRIVATE_QUERY",
            inputs={"query_variants": ["PRIVATE_QUERY"]}, run_id=tool_run,
        )
        tracer.on_tool_end("PRIVATE_DOCUMENT", run_id=tool_run)
        spans = exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        attributes = json.dumps([dict(span.attributes) for span in spans])
        self.assertIn("gen_ai.operation.name", attributes)
        self.assertIn("foundryiq___knowledge_base_retrieve", attributes)
        self.assertNotIn("PRIVATE_", attributes)
        provider.shutdown()

    async def test_chat_mode_needs_no_toolbox_and_makes_no_mcp_calls(self):
        config = Configuration.from_environment({
            "MODEL_ENDPOINT": CONFIG.model_endpoint,
            "CHAT_DEPLOYMENT": "chat",
            "AGENT_MODE": "chat",
        })
        model = ToolCallingModel(responses=[AIMessage(content="Chat is available")])
        with (
            patch("agent.MultiServerMCPClient") as mcp,
            patch("agent.ChatOpenAI", return_value=model),
        ):
            graph = await build_agent(config, MagicMock())
            result = await graph.ainvoke(
                {"messages": [("user", "Hello")]},
                {"configurable": {"thread_id": "chat-only"}},
            )
        mcp.assert_not_called()
        self.assertEqual(result["messages"][-1].content, "Chat is available")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "AGENT_MODE"):
            Configuration.from_environment({"AGENT_MODE": "typo"})

    def test_incomplete_configuration_fails(self):
        with self.assertRaisesRegex(ValueError, "CHAT_DEPLOYMENT, TOOLBOX_ENDPOINT"):
            Configuration.from_environment({"MODEL_ENDPOINT": CONFIG.model_endpoint})
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            Configuration.from_environment({
                "MODEL_ENDPOINT": "http://demo",
                "CHAT_DEPLOYMENT": "chat",
                "TOOLBOX_ENDPOINT": CONFIG.toolbox_endpoint,
            })

    async def test_structured_citations_are_model_visible(self):
        evidence = {"documents": [{"title": "Demo note", "url": "https://demo/doc.txt"}]}
        result = CallToolResult(
            content=[TextContent(type="text", text="Found a document")],
            structuredContent=evidence,
        )
        converted = await expose_evidence(MagicMock(), AsyncMock(return_value=result))
        self.assertEqual(converted.content[0].text, "Found a document")
        self.assertEqual(json.loads(converted.content[1].text), evidence)
        self.assertEqual(converted.structuredContent, evidence)

    async def test_tool_errors_are_not_masked(self):
        handler = AsyncMock(side_effect=RuntimeError("Search unavailable"))
        with self.assertRaisesRegex(RuntimeError, "Search unavailable"):
            await expose_evidence(MagicMock(), handler)
        error = CallToolResult(
            isError=True,
            content=[TextContent(type="text", text="Web IQ forbidden")],
        )
        self.assertTrue((await expose_evidence(MagicMock(), AsyncMock(return_value=error))).isError)

    async def test_web_passages_are_bounded_without_changing_query_or_filters(self):
        for supplied, expected in [
            ({}, {"maxResults": 2, "maxLength": 1200}),
            ({"maxResults": 50, "maxLength": 500000, "contentFormat": "html"},
             {"maxResults": 2, "maxLength": 1200}),
            ({"maxResults": 1, "maxLength": 500}, {"maxResults": 1, "maxLength": 500}),
        ]:
            request = MCPToolCallRequest(
                name="webiq___web", server_name="iq",
                args={"query": "Azure AI Search", "includeSites": ["learn.microsoft.com"], **supplied},
            )
            handler = AsyncMock(return_value=CallToolResult(content=[]))
            await expose_evidence(request, handler)
            actual = handler.call_args.args[0]
            self.assertEqual(actual.args["query"], request.args["query"])
            self.assertEqual(actual.args["includeSites"], ["learn.microsoft.com"])
            self.assertEqual(actual.args["contentFormat"], "passage")
            for name, value in expected.items():
                self.assertEqual(actual.args[name], value)
            self.assertEqual(request.args, {"query": "Azure AI Search", "includeSites": ["learn.microsoft.com"], **supplied})

    async def test_document_requests_are_not_changed(self):
        request = MCPToolCallRequest(
            name="foundryiq___knowledge_base_retrieve", server_name="iq",
            args={"query_variants": ["What do the documents say about deployment?"]},
        )
        handler = AsyncMock(return_value=CallToolResult(content=[]))
        await expose_evidence(request, handler)
        self.assertIs(handler.call_args.args[0], request)

    async def test_tokens_are_resolved_for_each_request(self):
        with patch("agent.get_bearer_token_provider", return_value=MagicMock(side_effect=["first", "second"])):
            auth = ToolboxAuthentication(MagicMock())
            for expected in ("first", "second"):
                request = httpx.Request("POST", CONFIG.toolbox_endpoint)
                async for outgoing in auth.async_auth_flow(request):
                    self.assertEqual(outgoing.headers["Authorization"], f"Bearer {expected}")

    async def test_missing_tool_stops_startup(self):
        client = MagicMock()
        client.get_tools = AsyncMock(return_value=[])
        with patch("agent.MultiServerMCPClient", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "missing required tools"):
                await build_agent(CONFIG, MagicMock())

    async def test_both_sources_reach_model_and_sessions_are_separate(self):
        async def documents(query: str):
            return "Demo note: document evidence, reference doc-1"

        async def web(query: str):
            return "Public evidence: https://example.org/reference"

        tools = [
            StructuredTool.from_function(
                coroutine=documents, name="foundryiq___knowledge_base_retrieve",
                description="Retrieve document evidence",
            ),
            StructuredTool.from_function(
                coroutine=web, name="webiq___web", description="Retrieve public evidence",
            ),
        ]
        model = ToolCallingModel(responses=[
            AIMessage(content="", tool_calls=[
                {"name": tools[0].name, "args": {"query": "demo"}, "id": "doc-call"},
                {"name": tools[1].name, "args": {"query": "demo"}, "id": "web-call"},
            ]),
            AIMessage(content="Document: doc-1; Web: https://example.org/reference"),
            AIMessage(content="The document reference was doc-1."),
        ])
        client = MagicMock()
        client.get_tools = AsyncMock(return_value=tools)
        with (
            patch("agent.MultiServerMCPClient", return_value=client),
            patch("agent.ChatOpenAI", return_value=model),
        ):
            graph = await build_agent(CONFIG, MagicMock())
        thread = {"configurable": {"thread_id": "conversation-a"}}
        result = await graph.ainvoke({"messages": [("user", "Use both sources")]}, thread)
        evidence = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        self.assertEqual(len(evidence), 2)
        self.assertIn("doc-1", evidence[0].content)
        self.assertIn("https://example.org/reference", evidence[1].content)
        followup = await graph.ainvoke({"messages": [("user", "What was the document reference?")]}, thread)
        self.assertGreater(len(followup["messages"]), len(result["messages"]))
        other = await graph.aget_state({"configurable": {"thread_id": "conversation-b"}})
        self.assertFalse(other.values)

    async def test_hosted_responses_protocol_returns_a_completed_response(self):
        async def lookup(query: str):
            return "Demo evidence"

        tools = [
            StructuredTool.from_function(coroutine=lookup, name=name, description="Demo lookup")
            for name in ("foundryiq___knowledge_base_retrieve", "webiq___web")
        ]
        client = MagicMock()
        client.get_tools = AsyncMock(return_value=tools)
        model = ToolCallingModel(responses=[AIMessage(content="Hosted adapter is working")])
        with (
            patch("agent.MultiServerMCPClient", return_value=client),
            patch("agent.ChatOpenAI", return_value=model),
        ):
            graph = await build_agent(CONFIG, MagicMock())
        with patch.dict("os.environ", {"AZURE_AI_PROJECT_ENDPOINT": "https://demo.services.ai.azure.com/api/projects/demo"}):
            host = MetadataOnlyAdapter(graph, credentials=MagicMock())
            host.init_tracing()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=host.app), base_url="http://agent.local"
            ) as client:
                response = await client.post("/responses", json={"input": "Hello", "stream": False})
        response.raise_for_status()
        self.assertEqual(response.json()["object"], "response")
        self.assertEqual(response.json()["output"][0]["status"], "completed")
        self.assertEqual(response.json()["output"][0]["content"][0]["text"], "Hosted adapter is working")


if __name__ == "__main__":
    unittest.main()
