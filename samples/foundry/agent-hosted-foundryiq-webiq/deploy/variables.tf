variable "subscription_id" {
  description = "Azure subscription containing the existing resource group."
  type        = string
}

variable "resource_group_name" {
  type    = string
  default = "rg-demo-lnca-maia-foundry-cgigax"
}

variable "location" {
  type    = string
  default = "francecentral"
}

variable "chat_model" {
  description = "A tool-calling model also supported by Foundry IQ."
  type = object({
    name     = string
    version  = string
    sku      = string
    capacity = number
  })
  default = {
    name     = "gpt-4o-mini"
    version  = "2024-07-18"
    sku      = "DataZoneStandard"
    capacity = 50
  }
}

variable "embedding_model" {
  type = object({
    name     = string
    version  = string
    sku      = string
    capacity = number
  })
  default = {
    name     = "text-embedding-3-large"
    version  = "1"
    sku      = "Standard"
    capacity = 50
  }
}
