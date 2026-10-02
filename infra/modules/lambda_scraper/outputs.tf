output "function_name" {
  description = "Scraper Lambda function name"
  value       = aws_lambda_function.scraper.function_name
}

output "function_arn" {
  description = "Scraper Lambda function ARN"
  value       = aws_lambda_function.scraper.arn
}
