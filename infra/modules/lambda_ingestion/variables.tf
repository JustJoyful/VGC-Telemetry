variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}

variable "image_uri" {
  description = "ECR container image URI for the Ingestion Lambda"
  type        = string
}

variable "role_arn" {
  description = "IAM Role ARN for the Ingestion Lambda"
  type        = string
}

variable "table_name" {
  description = "DynamoDB table name"
  type        = string
}

variable "bucket_name" {
  description = "S3 raw bucket name"
  type        = string
}

variable "bucket_arn" {
  description = "S3 raw bucket ARN"
  type        = string
}
