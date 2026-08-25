resource "random_string" "suffix" {
  count   = local.enabled_count
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "main" {
  count    = local.enabled_count
  name     = "rg-${local.base}"
  location = var.location
  tags     = var.tags
}

resource "azurerm_iothub" "main" {
  count                        = local.enabled_count
  name                         = "iot-${local.base}"
  resource_group_name          = azurerm_resource_group.main[0].name
  location                     = azurerm_resource_group.main[0].location
  min_tls_version              = "1.2"
  local_authentication_enabled = true

  sku {
    name     = "F1"
    capacity = 1
  }

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}


resource "azurerm_iothub_shared_access_policy" "dps_link" {
  count               = var.deployment_enabled && var.dps_enabled ? 1 : 0
  name                = "dps-link"
  resource_group_name = azurerm_resource_group.main[0].name
  iothub_name          = azurerm_iothub.main[0].name
  registry_read        = true
  registry_write       = true
  service_connect      = true
}

resource "azurerm_iothub_consumer_group" "control" {
  count                  = local.enabled_count
  name                   = "fleetplane-control"
  iothub_name            = azurerm_iothub.main[0].name
  eventhub_endpoint_name = "events"
  resource_group_name    = azurerm_resource_group.main[0].name
}

resource "azurerm_iothub_shared_access_policy" "function_ingress" {
  count               = local.enabled_count
  name                = "function-ingress"
  resource_group_name = azurerm_resource_group.main[0].name
  iothub_name          = azurerm_iothub.main[0].name
  service_connect      = true
}

resource "azurerm_cosmosdb_account" "main" {
  count                          = local.enabled_count
  name                           = "cosmos-${local.base}"
  location                       = azurerm_resource_group.main[0].location
  resource_group_name            = azurerm_resource_group.main[0].name
  offer_type                     = "Standard"
  kind                           = "GlobalDocumentDB"
  free_tier_enabled              = var.cosmos_free_tier_enabled
  local_authentication_disabled  = true
  public_network_access_enabled  = true

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main[0].location
    failover_priority = 0
  }

  tags = var.tags
}

resource "azurerm_cosmosdb_sql_database" "main" {
  count               = local.enabled_count
  name                = "fleetplane"
  resource_group_name = azurerm_resource_group.main[0].name
  account_name        = azurerm_cosmosdb_account.main[0].name
  throughput          = var.cosmos_shared_throughput
}

resource "azurerm_cosmosdb_sql_container" "control" {
  count                 = local.enabled_count
  name                  = "control"
  resource_group_name   = azurerm_resource_group.main[0].name
  account_name          = azurerm_cosmosdb_account.main[0].name
  database_name         = azurerm_cosmosdb_sql_database.main[0].name
  partition_key_paths   = ["/device_id"]
  partition_key_version = 2

  indexing_policy {
    indexing_mode = "consistent"

    included_path {
      path = "/*"
    }
  }
}

resource "azurerm_cosmosdb_sql_container" "summaries" {
  count                 = local.enabled_count
  name                  = "summaries"
  resource_group_name   = azurerm_resource_group.main[0].name
  account_name          = azurerm_cosmosdb_account.main[0].name
  database_name         = azurerm_cosmosdb_sql_database.main[0].name
  partition_key_paths   = ["/scope_id"]
  partition_key_version = 2

  indexing_policy {
    indexing_mode = "consistent"

    included_path {
      path = "/*"
    }
  }
}

resource "azurerm_cosmosdb_sql_container" "leases" {
  count                 = local.enabled_count
  name                  = "leases"
  resource_group_name   = azurerm_resource_group.main[0].name
  account_name          = azurerm_cosmosdb_account.main[0].name
  database_name         = azurerm_cosmosdb_sql_database.main[0].name
  partition_key_paths   = ["/id"]
  partition_key_version = 2
}

resource "azurerm_storage_account" "functions" {
  count                           = local.enabled_count
  name                            = substr(replace("st${local.base}", "-", ""), 0, 24)
  resource_group_name             = azurerm_resource_group.main[0].name
  location                        = azurerm_resource_group.main[0].location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  tags                            = var.tags
}

resource "azurerm_service_plan" "functions" {
  count               = local.enabled_count
  name                = "plan-${local.base}"
  resource_group_name = azurerm_resource_group.main[0].name
  location            = azurerm_resource_group.main[0].location
  os_type             = "Linux"
  sku_name            = "Y1"
  tags                = var.tags
}

resource "azurerm_linux_function_app" "main" {
  count               = local.enabled_count
  name                = "func-${local.base}"
  resource_group_name = azurerm_resource_group.main[0].name
  location            = azurerm_resource_group.main[0].location
  service_plan_id     = azurerm_service_plan.functions[0].id

  storage_account_name       = azurerm_storage_account.functions[0].name
  storage_account_access_key = azurerm_storage_account.functions[0].primary_access_key
  https_only                 = true

  identity {
    type = "SystemAssigned"
  }

  site_config {
    minimum_tls_version = "1.2"
    ftps_state          = "Disabled"

    application_stack {
      python_version = var.function_python_version
    }
  }

  app_settings = {
    FUNCTIONS_WORKER_RUNTIME         = "python"
    SCM_DO_BUILD_DURING_DEPLOYMENT   = "true"
    ENABLE_ORYX_BUILD                = "true"
    FLEETPLANE_MODE                  = "azure"
    FLEETPLANE_IOTHUB_HOST_NAME      = azurerm_iothub.main[0].hostname
    FLEETPLANE_COSMOS_ENDPOINT       = azurerm_cosmosdb_account.main[0].endpoint
    FLEETPLANE_COSMOS_DATABASE          = azurerm_cosmosdb_sql_database.main[0].name
    FLEETPLANE_COSMOS_CONTAINER         = azurerm_cosmosdb_sql_container.control[0].name
    FLEETPLANE_COSMOS_SUMMARY_CONTAINER = azurerm_cosmosdb_sql_container.summaries[0].name
    FLEETPLANE_COSMOS_LEASE_CONTAINER   = azurerm_cosmosdb_sql_container.leases[0].name
    FLEETPLANE_COSMOS_CONNECTION__accountEndpoint = azurerm_cosmosdb_account.main[0].endpoint
    FLEETPLANE_IOTHUB_CONSUMER_GROUP = azurerm_iothub_consumer_group.control[0].name
    FLEETPLANE_DEVICE_REGISTRY_NAMESPACE = var.device_registry_enabled ? azapi_resource.device_registry_namespace[0].name : "fleetplane-prod"
    FLEETPLANE_DPS_ID_SCOPE = var.dps_enabled ? azurerm_iothub_dps.main[0].id_scope : ""
    FLEETPLANE_AZURE_RESOURCE_GROUP = azurerm_resource_group.main[0].name
    FLEETPLANE_AZURE_LOCATION = azurerm_resource_group.main[0].location
    FLEETPLANE_IOTHUB_EVENTHUB_NAME  = azurerm_iothub.main[0].event_hub_events_path
    FLEETPLANE_IOTHUB_EVENTHUB = format(
      "Endpoint=%s;SharedAccessKeyName=%s;SharedAccessKey=%s;EntityPath=%s",
      azurerm_iothub.main[0].event_hub_events_endpoint,
      azurerm_iothub_shared_access_policy.function_ingress[0].name,
      azurerm_iothub_shared_access_policy.function_ingress[0].primary_key,
      azurerm_iothub.main[0].event_hub_events_path,
    )
  }

  tags = var.tags
}

resource "azurerm_role_assignment" "function_iothub_data" {
  count                = local.enabled_count
  scope                = azurerm_iothub.main[0].id
  role_definition_name = "IoT Hub Data Contributor"
  principal_id         = azurerm_linux_function_app.main[0].identity[0].principal_id
}

resource "azurerm_cosmosdb_sql_role_assignment" "function_data" {
  count               = local.enabled_count
  resource_group_name = azurerm_resource_group.main[0].name
  account_name        = azurerm_cosmosdb_account.main[0].name
  principal_id        = azurerm_linux_function_app.main[0].identity[0].principal_id
  role_definition_id  = "${azurerm_cosmosdb_account.main[0].id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  scope               = azurerm_cosmosdb_account.main[0].id
}

resource "azurerm_static_web_app" "dashboard" {
  count               = local.enabled_count
  name                = "web-${local.base}"
  resource_group_name = azurerm_resource_group.main[0].name
  location            = var.static_web_location
  sku_tier            = "Free"
  sku_size            = "Free"
  tags                = var.tags
}

# Microsoft IoT platform management plane. Azure Device Registry is GA with Azure IoT Operations;
# its IoT Hub integration remains preview, so the namespace can exist independently of that link.
resource "azapi_resource" "device_registry_namespace" {
  count     = var.deployment_enabled && var.device_registry_enabled ? 1 : 0
  type      = "Microsoft.DeviceRegistry/namespaces@2026-04-01"
  name      = "adr-${local.base}"
  parent_id = azurerm_resource_group.main[0].id
  location  = azurerm_resource_group.main[0].location
  tags      = var.tags

  body = {
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      messaging = {
        endpoints = {}
      }
    }
  }
}

# Zero-touch provisioning substrate for direct-to-cloud intelligent devices.
resource "azurerm_iothub_dps" "main" {
  count                 = var.deployment_enabled && var.dps_enabled ? 1 : 0
  name                  = "dps-${local.base}"
  resource_group_name   = azurerm_resource_group.main[0].name
  location              = azurerm_resource_group.main[0].location
  allocation_policy     = "Hashed"
  public_network_access_enabled = true

  sku {
    name     = "S1"
    capacity = 1
  }

  linked_hub {
    connection_string       = azurerm_iothub_shared_access_policy.dps_link[0].primary_connection_string
    location                = azurerm_iothub.main[0].location
    apply_allocation_policy = true
    allocation_weight       = 1
  }

  tags = var.tags
}
