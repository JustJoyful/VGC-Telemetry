output "function_name" {
  description = "Ingestion Lambda function name"
  value       = aws_lambda_function.ingestion.function_name
}

output "function_arn" {
  description = "Ingestion Lambda function ARN"
  value       = aws_lambda_function.ingestion.arn
}
