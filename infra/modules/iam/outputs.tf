output "api_role_arn" {
  description = "IAM Role ARN for public API Lambda"
  value       = aws_iam_role.api_role.arn
}

output "ingestion_role_arn" {
  description = "IAM Role ARN for Ingestion ETL Lambda"
  value       = aws_iam_role.ingestion_role.arn
}

output "scraper_role_arn" {
  description = "IAM Role ARN for Scraper Lambda"
  value       = aws_iam_role.scraper_role.arn
}
