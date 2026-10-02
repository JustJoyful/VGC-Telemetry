variable "notification_email" {
  description = "Email address for $1 budget alert tripwire"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev/prod)"
  type        = string
}
