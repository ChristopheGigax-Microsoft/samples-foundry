import asyncio
import os
from pathlib import Path

from azure.ai.agentserver.langgraph.langgraph import LangGraphAdapter
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer

from agent import Configuration, build_agent


class MetadataTracer(AzureAIOpenTelemetryTracer):
    def on_tool_start(self, serialized, input_str, **kwargs):
        # The bundled tracer records tool arguments even with content recording off.
        kwargs.pop("inputs", None)
        return super().on_tool_start(serialized, "", inputs=None, **kwargs)


class MetadataOnlyAdapter(LangGraphAdapter):
    def init_tracing_internal(self, exporter_endpoint=None, app_insights_conn_str=None):
        # Reuse the host's exporter instead of enabling content-recording LangSmith traces.
        self.azure_ai_tracer = MetadataTracer(
            connection_string="",
            enable_content_recording=False,
            name=self.get_agent_identifier(),
        )


async def main():
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").strip():
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT is required by the hosting SDK.")
    with DefaultAzureCredential() as credential:
        graph = await build_agent(Configuration.from_environment(), credential)
        await MetadataOnlyAdapter(graph, credentials=credential).run_async()


if __name__ == "__main__":
    asyncio.run(main())
