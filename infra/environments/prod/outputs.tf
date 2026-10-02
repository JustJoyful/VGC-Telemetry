output "function_url" {
  description = "Public Lambda Function URL"
  value       = module.lambda_api.function_url
}

output "dynamodb_table_name" {
  description = "DynamoDB Table Name"
  value       = module.dynamodb.table_name
}

output "s3_bucket_name" {
  description = "S3 Bucket Name"
  value       = module.s3.bucket_name
}
