# which terraform, which providers

terraform {
  required_version = ">=1.9" # refuse to run on an older CLI 

  required_providers {
    azurerm = {                     # azurerm is the local name for this provider
      source  = "hashicorp/azurerm" # where to download it from (the registry)
      version = "~> 4.0"            # allow 4.x, refuse 5.0
    }
  }

  # NEW: state lived in an Azure blob instead of a local file
  backend "azurerm" {
    resource_group_name  = "rg-terraform-state"
    storage_account_name = "stwhdeliverytf4821"
    container_name       = "tfstate"
    key                  = "webhook-delivery.tfstate"
  }
}

# configure that provider

provider "azurerm" {
  features {} # mandatory, usually empty - an azurerm quirk
}

# declare what should exist

resource "azurerm_resource_group" "main" {
  name     = "rg-webhook-delivery-dev"
  location = "canadacentral"
}