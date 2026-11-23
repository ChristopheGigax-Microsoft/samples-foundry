output "configuration" {
  description = "Non-secret values consumed by the Search setup script and azd."
  value = {
    subscription_id      = var.subscription_id
    tenant_id            = data.azurerm_client_config.current.tenant_id
    resource_group       = data.azurerm_resource_group.demo.name
    project_id           = azapi_resource.project.id
    project_endpoint     = azapi_resource.project.output.properties.endpoints["AI Foundry API"]
    model_endpoint       = "https://${azapi_resource.foundry.name}.openai.azure.com/"
    chat_deployment      = azapi_resource.models["chat"].name
    chat_model           = var.chat_model.name
    embedding_deployment = azapi_resource.models["embedding"].name
    embedding_model      = var.embedding_model.name
    search_endpoint      = "https://${azurerm_search_service.knowledge.name}.search.windows.net"
    storage_id           = azurerm_storage_account.documents.id
    storage_name         = azurerm_storage_account.documents.name
    container_name       = azurerm_storage_container.documents.name
    knowledge_base       = "documents"
    webiq_id             = azapi_resource.webiq.id
  }
}
