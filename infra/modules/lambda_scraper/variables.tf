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
