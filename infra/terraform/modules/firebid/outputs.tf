output "cluster" {
  value = google_container_cluster.firebid.name
}

output "database_connection_name" {
  value = google_sql_database_instance.firebid.connection_name
}

output "database_private_ip" {
  value = google_sql_database_instance.firebid.private_ip_address
}

output "documents_bucket" {
  value = google_storage_bucket.documents.name
}

output "snapshots_bucket" {
  value = google_storage_bucket.snapshots.name
}

output "edge_address" {
  value = google_compute_global_address.edge.address
}

output "ssl_policy" {
  value = google_compute_ssl_policy.tls12.name
}

output "security_policy" {
  value = google_compute_security_policy.edge.name
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "workload_service_accounts" {
  value = { for name, account in google_service_account.workloads : name => account.email }
}
