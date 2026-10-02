variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}

variable "image_uri" {
  description = "ECR container image URI for the API Lambda"
  type        = string
}

variable "role_arn" {
  description = "IAM Role ARN for the API Lambda"
  type        = string
}

variable "table_name" {
  description = "DynamoDB table name"
  type        = string
}

variable "reserved_concurrency" {
  description = "Reserved concurrency ceiling for public endpoint"
  type        = number
  default     = 5
}
