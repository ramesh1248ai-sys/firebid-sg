# Availability and operational alerts (NFR-03 99.5% in Singapore business hours; NFR-14).
# Log-based metrics read the structured JSON logs the platform already writes.

resource "google_monitoring_notification_channel" "email" {
  for_each     = toset(var.alert_emails)
  project      = var.project_id
  display_name = "FireBid ${var.environment}: ${each.value}"
  type         = "email"
  labels = {
    email_address = each.value
  }
}

locals {
  channels = [for channel in google_monitoring_notification_channel.email : channel.id]
}

resource "google_monitoring_uptime_check_config" "health" {
  project      = var.project_id
  display_name = "FireBid ${var.environment} /health"
  timeout      = "10s"
  period       = "60s"
  http_check {
    path         = "/api/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.hostname
    }
  }
  checker_type     = "STATIC_IP_CHECKERS"
  selected_regions = ["ASIA_PACIFIC", "USA_OREGON", "EUROPE"]
}

resource "google_monitoring_alert_policy" "down" {
  project      = var.project_id
  display_name = "FireBid ${var.environment}: unavailable"
  combiner     = "OR"
  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.health.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
    }
  }
  notification_channels = local.channels
}

# --- Log-based metrics: things the platform logs that someone must act on --------------------

resource "google_logging_metric" "events" {
  for_each = {
    audit_chain_broken     = "jsonPayload.event=\"audit_chain_broken\""
    audit_partition_failed = "jsonPayload.event=\"audit_partition_failed\" OR (jsonPayload.logger=\"firebid.jobs\" AND jsonPayload.event=~\"partition\" AND severity>=ERROR)"
    parse_failed           = "jsonPayload.event=\"parse_failed\""
    server_errors          = "httpRequest.status>=500"
  }
  project = var.project_id
  name    = "${local.name}-${each.key}"
  filter  = each.value
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "events" {
  for_each = {
    audit_chain_broken     = { threshold = 0, window = "300s", label = "An audit chain failed verification" }
    audit_partition_failed = { threshold = 0, window = "3600s", label = "An audit partition could not be created" }
    server_errors          = { threshold = 20, window = "300s", label = "More than 20 server errors in 5 minutes" }
  }
  project      = var.project_id
  display_name = "FireBid ${var.environment}: ${each.value.label}"
  combiner     = "OR"
  conditions {
    display_name = each.value.label
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.events[each.key].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = each.value.threshold
      duration        = "0s"
      aggregations {
        alignment_period   = each.value.window
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = local.channels
}

# Job-queue depth: the worker logs `queue_depth` each minute (system.heartbeat).
resource "google_logging_metric" "queue_depth" {
  project = var.project_id
  name    = "${local.name}-queue-depth"
  filter  = "jsonPayload.event=\"queue_depth\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    labels {
      key        = "queue"
      value_type = "STRING"
    }
  }
  value_extractor = "EXTRACT(jsonPayload.todo)"
  label_extractors = {
    queue = "EXTRACT(jsonPayload.queue)"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 20
      growth_factor      = 2
      scale              = 1
    }
  }
}

resource "google_monitoring_alert_policy" "queue_depth" {
  project      = var.project_id
  display_name = "FireBid ${var.environment}: job queue backing up"
  combiner     = "OR"
  conditions {
    display_name = "More than ${var.queue_depth_alert} jobs waiting for 15 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.queue_depth.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.queue_depth_alert
      duration        = "900s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }
  notification_channels = local.channels
}

resource "google_monitoring_alert_policy" "database" {
  project      = var.project_id
  display_name = "FireBid ${var.environment}: database under strain"
  combiner     = "OR"
  conditions {
    display_name = "Cloud SQL CPU above 85% for 15 minutes"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "900s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
  conditions {
    display_name = "Cloud SQL disk above 85%"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/disk/utilization\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "900s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
  notification_channels = local.channels
}
