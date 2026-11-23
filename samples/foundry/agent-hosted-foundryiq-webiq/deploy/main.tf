data "azurerm_resource_group" "demo" {
  name = var.resource_group_name
}

data "azurerm_client_config" "current" {}

locals {
  suffix = substr(sha256(data.azurerm_resource_group.demo.id), 0, 8)
  tags   = merge(data.azurerm_resource_group.demo.tags, { purpose = "foundry-iq-webiq-demo" })
}

resource "azapi_resource" "foundry" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = "foundry-iq-${local.suffix}"
  parent_id = data.azurerm_resource_group.demo.id
  location  = var.location
  tags      = local.tags
  identity { type = "SystemAssigned" }
  body = {
    kind = "AIServices"
    sku  = { name = "S0" }
    properties = {
      allowProjectManagement = true
      customSubDomainName    = "foundry-iq-${local.suffix}"
      disableLocalAuth       = true
      publicNetworkAccess    = "Enabled"
    }
  }
  response_export_values = ["identity.principalId"]
}

resource "azapi_resource" "project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name      = "iq-demo"
  parent_id = azapi_resource.foundry.id
  location  = var.location
  tags      = data.azurerm_resource_group.demo.tags
  # Foundry rejects concurrent writes to children of the same account.
  locks = [azapi_resource.foundry.id]
  identity { type = "SystemAssigned" }
  body = {
    properties = {
      displayName = "Foundry IQ and Web IQ demo"
      description = "Technical LangChain hosted agent demo."
    }
  }
  response_export_values = ["identity.principalId", "properties.endpoints"]
}

resource "azapi_resource" "models" {
  for_each  = { chat = var.chat_model, embedding = var.embedding_model }
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = each.key
  parent_id = azapi_resource.foundry.id
  locks     = [azapi_resource.foundry.id]
  body = {
    sku = {
      name     = each.value.sku
      capacity = each.value.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = each.value.name
        version = each.value.version
      }
      versionUpgradeOption = "NoAutoUpgrade"
    }
  }
}

resource "azurerm_search_service" "knowledge" {
  name                         = "search-iq-${local.suffix}"
  resource_group_name          = data.azurerm_resource_group.demo.name
  location                     = var.location
  sku                          = "standard"
  partition_count              = 1
  replica_count                = 1
  semantic_search_sku          = "standard"
  local_authentication_enabled = false
  identity { type = "SystemAssigned" }
  tags = local.tags
}

resource "azurerm_storage_account" "documents" {
  name                            = "stiq${local.suffix}"
  resource_group_name             = data.azurerm_resource_group.demo.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  blob_properties {
    delete_retention_policy { days = 7 }
  }
  tags = local.tags
}

resource "azurerm_role_assignment" "uploader" {
  scope                = azurerm_storage_account.documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_storage_container" "documents" {
  name                  = "documents"
  storage_account_id    = azurerm_storage_account.documents.id
  container_access_type = "private"
  depends_on            = [azurerm_role_assignment.uploader]
}

resource "azurerm_role_assignment" "search_storage" {
  scope                = azurerm_storage_account.documents.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_search_service.knowledge.identity[0].principal_id
}

resource "azurerm_role_assignment" "search_models" {
  for_each             = toset(["Cognitive Services OpenAI User", "Cognitive Services User"])
  scope                = azapi_resource.foundry.id
  role_definition_name = each.value
  principal_id         = azurerm_search_service.knowledge.identity[0].principal_id
}

resource "azurerm_role_assignment" "project_search" {
  scope                = azurerm_search_service.knowledge.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azapi_resource.project.output.identity.principalId
}

resource "azurerm_role_assignment" "search_configurator" {
  for_each             = toset(["Search Service Contributor", "Search Index Data Contributor"])
  scope                = azurerm_search_service.knowledge.id
  role_definition_name = each.value
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "project_models" {
  scope                = azapi_resource.foundry.id
  role_definition_name = "Foundry User"
  principal_id         = azapi_resource.project.output.identity.principalId
}

resource "azurerm_role_assignment" "project_foundry" {
  scope                = azapi_resource.project.id
  role_definition_name = "Foundry User"
  principal_id         = azapi_resource.project.output.identity.principalId
}

resource "azurerm_role_assignment" "developer_foundry" {
  scope                = azapi_resource.foundry.id
  role_definition_name = "Foundry User"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_log_analytics_workspace" "telemetry" {
  name                = "log-iq-${local.suffix}"
  resource_group_name = data.azurerm_resource_group.demo.name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1
  tags                = local.tags
}

resource "azurerm_application_insights" "agent" {
  name                = "appi-iq-${local.suffix}"
  resource_group_name = data.azurerm_resource_group.demo.name
  location            = var.location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.telemetry.id
  retention_in_days   = 30
  tags                = local.tags
}

resource "azapi_resource" "project_telemetry" {
  type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name      = "application-insights"
  parent_id = azapi_resource.project.id
  locks     = [azapi_resource.foundry.id]
  body = {
    properties = {
      category      = "AppInsights"
      authType      = "ApiKey"
      target        = azurerm_application_insights.agent.id
      isSharedToAll = false
      credentials   = { key = azurerm_application_insights.agent.connection_string }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.agent.id
      }
    }
  }
}

resource "azapi_resource" "webiq" {
  # Web IQ is not yet in AzAPI's embedded schema; Azure validates its public API.
  schema_validation_enabled = false
  type                      = "Microsoft.WebIQ/accounts@2026-06-01-preview"
  name                      = "webiq-demo-${local.suffix}"
  parent_id                 = data.azurerm_resource_group.demo.id
  location                  = "global"
  # Web IQ lowercases the first character of tag keys in its API response.
  tags = {
    for key, value in local.tags : "${lower(substr(key, 0, 1))}${substr(key, 1, -1)}" => value
  }
  body = {
    kind       = "WebIQ.Grounding"
    sku        = { name = "G3" }
    properties = { statisticsEnabled = false }
  }
}
