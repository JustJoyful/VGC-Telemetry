variable "bucket_name" {
  description = "Name of the S3 raw/data lake bucket"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}

variable "ingestion_lambda_arn" {
  description = "ARN of the Ingestion ETL Lambda to trigger on raw/*.json"
  type        = string
}
