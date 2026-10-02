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
