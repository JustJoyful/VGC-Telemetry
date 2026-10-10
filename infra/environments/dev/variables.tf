variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
}

variable "ecr_repository_url" {
  description = "Base URL of the ECR repository"
  type        = string
}

variable "image_tag" {
  description = "Git commit SHA or image tag"
  type        = string
  default     = "latest"
}

variable "notification_email" {
  description = "Email for budget tripwire alert"
  type        = string
  default     = "alerts@example.com"
}

variable "source_admission_verified" {
  type    = bool
  default = false
}

variable "discovery_hours" {
  type    = number
  default = 6
}

variable "reference_hours" {
  type    = number
  default = 24
}

variable "recovery_minutes" {
  type    = number
  default = 30
}

variable "reconcile_expression" {
  type    = string
  default = "cron(0 6 ? * MON *)"
}

variable "collection_page_size" {
  type    = number
  default = 50
}

variable "collection_batch_size" {
  type    = number
  default = 10
}

variable "collection_max_pages" {
  type    = number
  default = 10
}

variable "recent_days" {
  type    = number
  default = 7
}

variable "reconcile_days" {
  type    = number
  default = 90
}
variable "catalog_runtime_enabled" {
  description = "Enable retained runtime catalog reads after guarded activation"
  type        = bool
  default     = false
}
