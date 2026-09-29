# FireBid SG on Google Cloud, asia-southeast1 (ADR-008). One environment per module call.
#
# Not applied yet: decision D2 (hosting and provider terms) must be taken first. Checked in
# CI by `terraform validate`, tflint and Trivy.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

locals {
  name   = "firebid-${var.environment}"
  labels = { app = "firebid", environment = var.environment }
}

# --- Services -------------------------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudkms.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "artifactregistry.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Encryption keys (CMEK) -----------------------------------------------------------------

resource "google_kms_key_ring" "firebid" {
  project  = var.project_id
  name     = local.name
  location = var.region
}

resource "google_kms_crypto_key" "data" {
  name            = "${local.name}-data"
  key_ring        = google_kms_key_ring.firebid.id
  rotation_period = "7776000s" # 90 days
  lifecycle {
    prevent_destroy = true
  }
}

# --- Network --------------------------------------------------------------------------------

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = local.name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "gke" {
  project                  = var.project_id
  name                     = "${local.name}-gke"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
  log_config {
    aggregation_interval = "INTERVAL_5_MIN"
  }
}

# Outbound for the API and workers (LLM providers, the IdP). The sandbox is kept off it by
# its egress network policy (infra/k8s/base/network-policies.yaml).
resource "google_compute_router" "nat" {
  project = var.project_id
  name    = "${local.name}-nat"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "${local.name}-nat"
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

resource "google_compute_global_address" "private_services" {
  project       = var.project_id
  name          = "${local.name}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

# --- Kubernetes: GKE Autopilot, private, with GKE Sandbox for the parser pool ---------------

resource "google_container_cluster" "firebid" {
  project             = var.project_id
  name                = local.name
  location            = var.region
  enable_autopilot    = true
  network             = google_compute_network.vpc.id
  subnetwork          = google_compute_subnetwork.gke.id
  deletion_protection = var.environment == "production"
  resource_labels     = local.labels

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.admin_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "admin"
      }
    }
  }

  # Dataplane V2: network policies, including FQDN policies for the sandbox's egress.
  datapath_provider = "ADVANCED_DATAPATH"

  database_encryption {
    state    = "ENCRYPTED"
    key_name = google_kms_crypto_key.data.id
  }

  release_channel {
    channel = "REGULAR"
  }

  # Upgrades only in a weekend window; the deployment guard covers releases (NFR-03).
  maintenance_policy {
    recurring_window {
      start_time = "2026-01-03T18:00:00Z"
      end_time   = "2026-01-03T22:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SA"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = local.name
  format        = "DOCKER"
  labels        = local.labels
}

# --- PostgreSQL 17 (Cloud SQL), private IP, PITR, HA in production -------------------------

resource "google_sql_database_instance" "firebid" {
  project             = var.project_id
  name                = local.name
  region              = var.region
  database_version    = "POSTGRES_17"
  encryption_key_name = google_kms_crypto_key.data.id
  deletion_protection = var.environment == "production"

  settings {
    tier              = var.database_tier
    availability_type = var.environment == "production" ? "REGIONAL" : "ZONAL"
    disk_autoresize   = true
    disk_type         = "PD_SSD"
    user_labels       = local.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    # NFR-04: point-in-time recovery comfortably beats the 24 h RPO.
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "18:00" # 02:00 Singapore time
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 30
      }
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 18
      update_track = "stable"
    }

    database_flags {
      name  = "max_connections"
      value = tostring(var.database_max_connections)
    }
    database_flags {
      name  = "log_min_duration_statement"
      value = "2000"
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    insights_config {
      query_insights_enabled = true
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "firebid" {
  project  = var.project_id
  name     = "firebid"
  instance = google_sql_database_instance.firebid.name
}

# --- Object storage -------------------------------------------------------------------------

resource "google_storage_bucket" "documents" {
  project                     = var.project_id
  name                        = "${var.project_id}-documents"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels
  versioning {
    enabled = true
  }
  encryption {
    default_kms_key_name = google_kms_crypto_key.data.id
  }
}

# Submission snapshots: immutable for their retention period (NFR-04). The lock cannot be
# undone once set, so it is locked only in production.
resource "google_storage_bucket" "snapshots" {
  project                     = var.project_id
  name                        = "${var.project_id}-snapshots"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels
  retention_policy {
    retention_period = var.snapshot_retention_days * 86400
    is_locked        = var.environment == "production"
  }
  encryption {
    default_kms_key_name = google_kms_crypto_key.data.id
  }
}

# --- Secrets and workload identities --------------------------------------------------------

resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(var.secret_names)
  project   = var.project_id
  secret_id = "${local.name}-${each.value}"
  labels    = local.labels
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_service_account" "workloads" {
  for_each     = toset(["api", "worker", "sandbox"])
  project      = var.project_id
  account_id   = "${local.name}-${each.value}"
  display_name = "FireBid ${each.value} (${var.environment})"
}

# Every workload reads the secrets it needs; the sandbox reads documents, never snapshots.
resource "google_secret_manager_secret_iam_member" "readers" {
  for_each  = { for pair in setproduct(["api", "worker", "sandbox"], var.secret_names) : "${pair[0]}-${pair[1]}" => pair }
  project   = var.project_id
  secret_id = google_secret_manager_secret.secrets[each.value[1]].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.workloads[each.value[0]].email}"
}

resource "google_storage_bucket_iam_member" "documents" {
  for_each = toset(["api", "worker", "sandbox"])
  bucket   = google_storage_bucket.documents.name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.workloads[each.value].email}"
}

resource "google_storage_bucket_iam_member" "snapshots" {
  for_each = toset(["api", "worker"])
  bucket   = google_storage_bucket.snapshots.name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${google_service_account.workloads[each.value].email}"
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each           = toset(["api", "worker", "sandbox"])
  service_account_id = google_service_account.workloads[each.value].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[firebid/firebid-${each.value}]"
}

# --- Edge: TLS 1.2+, Cloud Armor ------------------------------------------------------------

resource "google_compute_ssl_policy" "tls12" {
  project         = var.project_id
  name            = "${local.name}-tls12"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_security_policy" "edge" {
  project = var.project_id
  name    = "${local.name}-edge"

  rule {
    action   = "deny(403)"
    priority = 1000
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable') || evaluatePreconfiguredWaf('xss-v33-stable')"
      }
    }
    description = "OWASP SQL injection and XSS"
  }

  rule {
    action   = "throttle"
    priority = 2000
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 600
        interval_sec = 60
      }
    }
    description = "Per-client rate limit ahead of the API's own"
  }

  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default"
  }
}

resource "google_compute_global_address" "edge" {
  project = var.project_id
  name    = "${local.name}-edge"
}
