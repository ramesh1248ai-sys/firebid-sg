# FireBid SG, production (ADR-008). Not applied: decision D2 first.
#
#   terraform init -backend-config="bucket=<state bucket>"
#   terraform plan -var project_id=<project> -var hostname=<host>

terraform {
  required_version = ">= 1.9"
  backend "gcs" {
    prefix = "firebid/production"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = "asia-southeast1"
}

variable "project_id" {
  type = string
}

variable "hostname" {
  type = string
}

variable "alert_emails" {
  type    = list(string)
  default = []
}

variable "admin_cidrs" {
  type    = list(string)
  default = []
}

module "firebid" {
  source        = "../../modules/firebid"
  project_id    = var.project_id
  environment   = "production"
  hostname      = var.hostname
  alert_emails  = var.alert_emails
  admin_cidrs   = var.admin_cidrs
  database_tier = "db-custom-4-15360"
}

output "firebid" {
  value = module.firebid
}
