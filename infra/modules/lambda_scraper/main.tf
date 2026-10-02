resource "aws_lambda_function" "scraper" {
  function_name = "vgc-telemetry-scraper-${var.environment}"
  role          = var.role_arn
  package_type  = "Image"
  image_uri     = var.image_uri

  timeout     = 900
  memory_size = 512

  environment {
    variables = {
      S3_RAW_BUCKET_NAME = var.bucket_name
    }
  }

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
    ManagedBy   = "terraform"
  }
}
