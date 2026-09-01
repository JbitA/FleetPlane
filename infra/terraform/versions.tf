terraform {
  required_version = ">= 1.8.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.77.0, < 5.3.1"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.7.0, < 4.0.0"
    }
    azapi = {
      source  = "Azure/azapi"
      version = ">= 2.4.0, < 3.0.0"
    }
  }
}

provider "azurerm" {
  features {}
}
