variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}

variable "image_uri" {
  description = "ECR container image URI for the Scraper Lambda"
  type        = string
}

variable "role_arn" {
  description = "IAM Role ARN for the Scraper Lambda"
  type        = string
}

variable "bucket_name" {
  description = "S3 raw bucket name"
  type        = string
}

variable "table_name" {
  type = string
}

variable "ingestion_function_name" {
  type = string
}

variable "collection_page_size" {
  type    = number
  default = 50
  validation {
    condition     = var.collection_page_size >= 1 && var.collection_page_size <= 100 && var.collection_page_size == floor(var.collection_page_size)
    error_message = "Collection page size must be 1–100."
  }
}

variable "collection_batch_size" {
  type    = number
  default = 10
  validation {
    condition     = var.collection_batch_size >= 1 && var.collection_batch_size <= 100 && var.collection_batch_size == floor(var.collection_batch_size)
    error_message = "Collection batch size must be 1–100."
  }
}

variable "collection_max_pages" {
  type    = number
  default = 10
  validation {
    condition     = var.collection_max_pages >= 1 && var.collection_max_pages <= 100 && var.collection_max_pages == floor(var.collection_max_pages)
    error_message = "Collection max pages must be 1–100."
  }
}

variable "collection_interval_hours" {
  type    = number
  default = 6
  validation {
    condition     = var.collection_interval_hours >= 2 && var.collection_interval_hours <= 24 && var.collection_interval_hours == floor(var.collection_interval_hours)
    error_message = "Collection interval must be 2–24 hours."
  }
}

variable "recent_days" {
  type    = number
  default = 7
  validation {
    condition     = var.recent_days >= 1 && var.recent_days <= 7 && var.recent_days == floor(var.recent_days)
    error_message = "Recent window must be 1–7 days; use explicit backfill for older events."
  }
}

variable "reconcile_days" {
  type    = number
  default = 90
  validation {
    condition     = var.reconcile_days >= 1 && var.reconcile_days <= 90 && var.reconcile_days == floor(var.reconcile_days)
    error_message = "Reconciliation window must be 1–90 days; use explicit backfill for older events."
  }
}
