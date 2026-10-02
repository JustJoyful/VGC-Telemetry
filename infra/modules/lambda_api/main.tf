resource "aws_lambda_function" "api" {
  function_name = "vgc-telemetry-api-${var.environment}"
  role          = var.role_arn
  package_type  = "Image"
  image_uri     = var.image_uri

  timeout     = 30
  memory_size = 512

  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = var.table_name
      PORT                = "8080"
      AWS_LWA_PORT        = "8080"
    }
  }

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
    ManagedBy   = "terraform"
  }
}

resource "aws_lambda_function_url" "api_url" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"

  cors {
    allow_credentials = false
    allow_headers     = ["*"]
    allow_methods     = ["GET"]
    allow_origins     = ["*"]
    max_age           = 86400
  }
}
