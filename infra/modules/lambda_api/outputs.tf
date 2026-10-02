output "function_name" {
  description = "API Lambda function name"
  value       = aws_lambda_function.api.function_name
}

output "function_arn" {
  description = "API Lambda function ARN"
  value       = aws_lambda_function.api.arn
}

output "function_url" {
  description = "Public Lambda Function URL"
  value       = aws_lambda_function_url.api_url.function_url
}
