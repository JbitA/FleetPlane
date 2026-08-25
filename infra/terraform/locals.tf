locals {
  enabled_count = var.deployment_enabled ? 1 : 0
  base          = var.deployment_enabled ? "${var.prefix}-${random_string.suffix[0].result}" : "${var.prefix}-disabled"
}
