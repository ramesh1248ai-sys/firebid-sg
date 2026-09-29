variable "project_id" {
  description = "The Google Cloud project for this environment."
  type        = string
}

variable "environment" {
  description = "staging or production."
  type        = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment is staging or production."
  }
}

variable "region" {
  description = "Singapore (NFR-05). Changing it needs the sponsor's approval."
  type        = string
  default     = "asia-southeast1"
  validation {
    condition     = var.region == "asia-southeast1"
    error_message = "FireBid data stays in Singapore (NFR-05): the region is asia-southeast1."
  }
}

variable "hostname" {
  description = "The public hostname, for the certificate and the uptime check."
  type        = string
}

variable "admin_cidrs" {
  description = "Networks allowed to reach the Kubernetes control plane."
  type        = list(string)
  default     = []
}

variable "database_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-2-7680"
}

variable "database_max_connections" {
  description = "Cloud SQL max_connections; PgBouncer's pool sizes must fit inside it."
  type        = number
  default     = 200
}

variable "snapshot_retention_days" {
  description = "How long a submission snapshot is immutable (NFR-04). Default 7 years."
  type        = number
  default     = 2557
}

variable "secret_names" {
  description = "Secrets the workloads read, by short name."
  type        = list(string)
  default = [
    "database-owner-password",
    "database-app-password",
    "database-service-password",
    "storage-hmac-key",
    "storage-hmac-secret",
    "anthropic-api-key",
    "openai-api-key",
    "gemini-api-key",
    "oidc-client",
  ]
}

variable "alert_emails" {
  description = "Who is told when an alert fires."
  type        = list(string)
  default     = []
}

variable "queue_depth_alert" {
  description = "Jobs waiting, per queue, that is worth an alert after 15 minutes."
  type        = number
  default     = 200
}
