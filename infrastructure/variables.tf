variable "location" {
  type        = string
  description = "Azure region for all resources"
  default     = "canadacentral"
}

variable "project" {
  type        = string
  description = "Azure region for all resources"
  default     = "canadacentral"
}

variable "db_admin_username" {
  type    = string
  default = "whadmin"
}

variable "db_admin_password" {
  type        = string
  description = "Postgres admin password - supplied via TF_VAR_db_admin_password"
  sensitive   = true
}

variable "my_ip_address" {
  type        = string
  description = "Dev machine IP allowed through the database firewall"
  default     = "24.57.214.253"
}

