resource "aws_lambda_function" "ingestion" {
  function_name = "vgc-telemetry-ingestion-${var.environment}"
  role          = var.role_arn
  package_type  = "Image"
  image_uri     = var.image_uri

  timeout     = 300
  memory_size = 512

  environment {
    variables = {
      DYNAMODB_TABLE_NAME  = var.table_name
      S3_RAW_BUCKET_NAME   = var.bucket_name
      S3_QUARANTINE_PREFIX = "quarantine/"
    }
  }

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
    ManagedBy   = "terraform"
  }
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = var.bucket_arn
}
