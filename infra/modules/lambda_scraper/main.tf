resource "aws_lambda_function" "scraper" {
  function_name = "vgc-telemetry-scraper-${var.environment}"
  role          = var.role_arn
  package_type  = "Image"
  image_uri     = var.image_uri

  timeout     = 900
  memory_size = 512

  environment {
    variables = {
      S3_RAW_BUCKET_NAME        = var.bucket_name
      DYNAMODB_TABLE_NAME       = var.table_name
      INGESTION_FUNCTION_NAME   = var.ingestion_function_name
      SCRAPER_FUNCTION_NAME     = "vgc-telemetry-scraper-${var.environment}"
      COLLECTION_PAGE_SIZE      = tostring(var.collection_page_size)
      COLLECTION_BATCH_SIZE     = tostring(var.collection_batch_size)
      COLLECTION_MAX_PAGES      = tostring(var.collection_max_pages)
      COLLECTION_INTERVAL_HOURS = tostring(var.collection_interval_hours)
      RECENT_DAYS               = tostring(var.recent_days)
      RECONCILE_DAYS            = tostring(var.reconcile_days)
      ENVIRONMENT               = var.environment
    }
  }

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
    ManagedBy   = "terraform"
  }
}
