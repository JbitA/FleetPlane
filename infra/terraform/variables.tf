variable "deployment_enabled" {
  description = "Whether Azure resources are instantiated by this configuration."
  type        = bool
  default     = false
}

variable "prefix" {
  description = "Short workload prefix used in resource names."
  type        = string
  default     = "fleetplane"
}

variable "location" {
  description = "Primary Azure region."
  type        = string
  default     = "northeurope"
}

variable "static_web_location" {
  description = "Azure Static Web Apps region."
  type        = string
  default     = "westeurope"
}

variable "function_python_version" {
  description = "Python runtime used by the Azure Function application."
  type        = string
  default     = "3.12"
}

variable "cosmos_shared_throughput" {
  description = "Shared database throughput for the showcase database."
  type        = number
  default     = 1000
}

variable "cosmos_free_tier_enabled" {
  description = "Enable the Cosmos DB account free-tier flag when the subscription is eligible."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to Azure resources."
  type        = map(string)
  default     = {
    workload    = "fleetplane"
    environment = "showcase"
    managed_by  = "terraform"
  }
}

variable "device_registry_enabled" {
  description = "Create an Azure Device Registry namespace for the Microsoft IoT management-plane model."
  type        = bool
  default     = true
}

variable "dps_enabled" {
  description = "Create Device Provisioning Service for zero-touch IoT Hub assignment."
  type        = bool
  default     = true
}
