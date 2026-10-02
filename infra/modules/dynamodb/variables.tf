variable "table_name" {
  description = "Name of the DynamoDB table"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}
