output "resource_group_name" {
  value = var.deployment_enabled ? azurerm_resource_group.main[0].name : null
}

output "iothub_hostname" {
  value = var.deployment_enabled ? azurerm_iothub.main[0].hostname : null
}

output "cosmos_endpoint" {
  value = var.deployment_enabled ? azurerm_cosmosdb_account.main[0].endpoint : null
}

output "function_hostname" {
  value = var.deployment_enabled ? azurerm_linux_function_app.main[0].default_hostname : null
}

output "dashboard_hostname" {
  value = var.deployment_enabled ? azurerm_static_web_app.dashboard[0].default_host_name : null
}

output "device_registry_namespace" {
  value = var.deployment_enabled && var.device_registry_enabled ? azapi_resource.device_registry_namespace[0].name : null
}

output "dps_id_scope" {
  value = var.deployment_enabled && var.dps_enabled ? azurerm_iothub_dps.main[0].id_scope : null
}
