variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}

variable "table_arn" {
  description = "ARN of the DynamoDB single table"
  type        = string
}

variable "bucket_arn" {
  description = "ARN of the S3 raw bucket"
  type        = string
}

variable "ingestion_lambda_name" {
  type = string
}

variable "aws_region" {
  type = string
}
