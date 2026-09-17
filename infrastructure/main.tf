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

resource "azurerm_container_registry" "main" {
  name                = "acrwhdelivery4821"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                = "psql-whdelivery-4821"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  version                = "16"
  administrator_login    = var.db_admin_username
  administrator_password = var.db_admin_password

  sku_name   = "B_Standard_B1ms" # burstable, 1 vCore - the free offer tier
  storage_mb = 32768             # 32 GB
  zone       = "1"

  backup_retention_days          = 7
  public_network_access_enabled = true

}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "webhooks"
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "utf8"

}

# lets you run alembic from your laptop

resource "azurerm_postgresql_flexible_server_firewall_rule" "dev_machine" {
  name               = "allow-dev-machine"
  server_id          = azurerm_postgresql_flexible_server.main.id
  start_ip_address = var.my_ip_address
  end_ip_address     = var.my_ip_address
}

# lets Container Apps reach it on Day 21

resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
