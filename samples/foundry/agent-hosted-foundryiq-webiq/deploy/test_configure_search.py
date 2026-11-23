import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from configure_search import configure_connections, knowledge_definitions, send


CONFIG = {
    "storage_id": "/subscriptions/demo/resourceGroups/demo/providers/Microsoft.Storage/storageAccounts/demo",
    "container_name": "documents",
    "model_endpoint": "https://demo.openai.azure.com/",
    "embedding_deployment": "embedding",
    "embedding_model": "text-embedding-3-large",
    "chat_deployment": "chat",
    "chat_model": "gpt-4o-mini",
    "knowledge_base": "documents",
    "search_endpoint": "https://demo.search.windows.net",
    "project_id": "/subscriptions/demo/resourceGroups/demo/providers/Microsoft.CognitiveServices/accounts/demo/projects/demo",
}


class SearchSetupTests(unittest.TestCase):
    def test_blob_ingestion_uses_identity_and_incremental_schedule(self):
        source, base = knowledge_definitions(CONFIG)
        blob = source["azureBlobParameters"]
        self.assertEqual(blob["connectionString"], f"ResourceId={CONFIG['storage_id']}")
        self.assertEqual(blob["ingestionParameters"]["ingestionSchedule"]["interval"], "PT5M")
        self.assertEqual(base["knowledgeSources"], [{"name": source["name"]}])
        self.assertEqual(base["outputMode"], "extractiveData")
        self.assertEqual(base["retrieveDefaults"], {
            "maxOutputDocuments": 5, "maxOutputSizeInTokens": 5000,
        })

    def test_web_secret_only_goes_to_managed_connection(self):
        with patch.dict(os.environ, {"WEBIQ_API_KEY": "test-key"}), patch("configure_search.send") as call:
            configure_connections(CONFIG, MagicMock(), MagicMock())
        connection = call.call_args_list[1].args
        self.assertEqual(connection[4]["properties"]["credentials"], {"keys": {"x-apikey": "test-key"}})
        self.assertNotIn("test-key", connection[3])
        self.assertEqual(call.call_args_list[0].args[4]["properties"]["authType"], "ProjectManagedIdentity")

    def test_empty_web_key_and_http_errors_stop_configuration(self):
        with patch.dict(os.environ, {"WEBIQ_API_KEY": ""}), patch("getpass.getpass", return_value=""):
            with self.assertRaisesRegex(ValueError, "Web IQ API key"):
                configure_connections(CONFIG, MagicMock(), MagicMock())
        session = MagicMock()
        session.request.return_value.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        with self.assertRaisesRegex(requests.HTTPError, "403"):
            send(session, lambda: "token", "PUT", "https://demo.search.windows.net")


if __name__ == "__main__":
    unittest.main()
