import asyncio
import os
from pathlib import Path

from azure.ai.agentserver.langgraph import from_langgraph
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from agent import Configuration, build_agent


async def main():
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").strip():
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT is required by the hosting SDK.")
    with DefaultAzureCredential() as credential:
        graph = await build_agent(Configuration.from_environment(), credential)
        await from_langgraph(graph, credentials=credential).run_async()


if __name__ == "__main__":
    asyncio.run(main())
