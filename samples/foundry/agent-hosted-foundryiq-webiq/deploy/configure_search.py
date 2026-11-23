import argparse
import getpass
import json
import os
import subprocess
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential, get_bearer_token_provider


SEARCH_API = "2026-11-01-preview"
CONNECTION_API = "2025-10-01-preview"
SOURCE_NAME = "documents-blob"


def terraform_configuration():
    result = subprocess.run(
        ["terraform", "output", "-json", "configuration"],
        check=True, capture_output=True, text=True, cwd=Path(__file__).parent,
    )
    return json.loads(result.stdout)


def knowledge_definitions(config):
    embedding = {
        "kind": "azureOpenAI",
        "azureOpenAIParameters": {
            "resourceUri": config["model_endpoint"],
            "deploymentId": config["embedding_deployment"],
            "modelName": config["embedding_model"],
        },
    }
    source = {
        "name": SOURCE_NAME,
        "kind": "azureBlob",
        "description": "Documents uploaded for the technical demo.",
        "azureBlobParameters": {
            "connectionString": f"ResourceId={config['storage_id']}",
            "containerName": config["container_name"],
            "ingestionParameters": {
                "networkAccessMode": "public",
                "contentExtractionMode": "minimal",
                "disableImageVerbalization": True,
                "embeddingModel": embedding,
                "ingestionSchedule": {"interval": "PT5M"},
            },
        },
    }
    knowledge_base = {
        "name": config["knowledge_base"],
        "knowledgeSources": [{"name": SOURCE_NAME}],
        "models": [{
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": config["model_endpoint"],
                "deploymentId": config["chat_deployment"],
                "modelName": config["chat_model"],
            },
        }],
        "outputMode": "extractiveData",
        "retrievalReasoningEffort": {"kind": "low"},
        "retrieveDefaults": {
            "maxOutputDocuments": 5,
            "maxOutputSizeInTokens": 5000,
        },
    }
    return source, knowledge_base


def send(session, token, method, url, body=None):
    response = session.request(
        method, url, headers={"Authorization": f"Bearer {token()}"},
        json=body, timeout=120,
    )
    response.raise_for_status()
    return response.json() if response.content else None


def configure_knowledge(config, session, token, reindex):
    source, base = knowledge_definitions(config)
    endpoint = config["search_endpoint"]
    created = send(session, token, "PUT",
                   f"{endpoint}/knowledgesources/{SOURCE_NAME}?api-version={SEARCH_API}", source)
    send(session, token, "PUT",
         f"{endpoint}/knowledgebases/{base['name']}?api-version={SEARCH_API}", base)
    indexer = created["azureBlobParameters"]["createdResources"]["indexer"]
    if reindex:
        send(session, token, "POST",
             f"{endpoint}/indexers/{indexer}/run?api-version={SEARCH_API}")
    print(f"Configured Foundry IQ. Generated indexer: {indexer}")


def configure_connections(config, session, token):
    key = os.environ.get("WEBIQ_API_KEY") or getpass.getpass("Web IQ API key: ")
    if not key.strip():
        raise ValueError("A Web IQ API key is required; activate its AppID in the Developer Portal.")
    connections = {
        "foundry-iq": {
            "authType": "ProjectManagedIdentity",
            "audience": "https://search.azure.com/",
            "target": (
                f"{config['search_endpoint']}/knowledgebases/"
                f"{config['knowledge_base']}/mcp?api-version={SEARCH_API}"
            ),
        },
        "web-iq": {
            "authType": "CustomKeys",
            "target": "https://api.microsoft.ai/v3/mcp",
            "credentials": {"keys": {"x-apikey": key}},
        },
    }
    for name, properties in connections.items():
        properties.update(category="RemoteTool", isSharedToAll=True, metadata={"ApiType": "Azure"})
        url = (
            f"https://management.azure.com{config['project_id']}"
            f"/connections/{name}?api-version={CONNECTION_API}"
        )
        send(session, token, "PUT", url, {"properties": properties})
        print(f"Configured connection: {name}")


def main():
    parser = argparse.ArgumentParser(description="Configure Search data-plane objects and MCP connections.")
    parser.add_argument("--connections", action="store_true", help="Also configure both project connections.")
    parser.add_argument("--reindex", action="store_true", help="Run the generated indexer after configuration.")
    args = parser.parse_args()
    config = terraform_configuration()
    with DefaultAzureCredential() as credential, requests.Session() as session:
        search_token = get_bearer_token_provider(credential, "https://search.azure.com/.default")
        configure_knowledge(config, session, search_token, args.reindex)
        if args.connections:
            arm_token = get_bearer_token_provider(credential, "https://management.azure.com/.default")
            configure_connections(config, session, arm_token)


if __name__ == "__main__":
    main()
