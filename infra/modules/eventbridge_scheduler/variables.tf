variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}

variable "scraper_lambda_arn" {
  description = "Scraper Lambda function ARN"
  type        = string
}

variable "scraper_lambda_name" {
  description = "Scraper Lambda function name"
  type        = string
}

variable "ingestion_lambda_arn" {
  type = string
}

variable "ingestion_lambda_name" {
  type = string
}

variable "source_admission_verified" {
  type    = bool
  default = false
}

variable "discovery_hours" {
  type    = number
  default = 6
  validation {
    condition     = var.discovery_hours >= 2 && var.discovery_hours <= 24 && var.discovery_hours == floor(var.discovery_hours)
    error_message = "Discovery cadence must be 2–24 hours."
  }
}

variable "reference_hours" {
  type    = number
  default = 24
  validation {
    condition     = var.reference_hours >= 2 && var.reference_hours <= 168 && var.reference_hours == floor(var.reference_hours)
    error_message = "Reference cadence must be 2–168 hours."
  }
}

variable "reconcile_expression" {
  type    = string
  default = "cron(0 6 ? * MON *)"
  validation {
    condition     = startswith(var.reconcile_expression, "cron(")
    error_message = "Reconciliation requires a cron expression."
  }
}

variable "recovery_minutes" {
  type    = number
  default = 30
  validation {
    condition     = var.recovery_minutes >= 2 && var.recovery_minutes <= 60 && var.recovery_minutes == floor(var.recovery_minutes)
    error_message = "Recovery cadence must be 2–60 minutes."
  }
}
