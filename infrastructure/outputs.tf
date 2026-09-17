output "acr_login_server" {
  description = "Registry hostname to tag and push images against"
  value       = azurerm_container_registry.main.login_server
}

output "postgres_fqdn" {
  description = "Hostname to connect to the database"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

