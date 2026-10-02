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
