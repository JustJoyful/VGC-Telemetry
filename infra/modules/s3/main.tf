resource "aws_s3_bucket" "raw" {
  bucket = var.bucket_name

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_crypto" {
  bucket = aws_s3_bucket.raw.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "raw_versioning" {
  bucket = aws_s3_bucket.raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Prefix-scoped notification preventing recursive trigger loops
resource "aws_s3_bucket_notification" "raw_trigger" {
  bucket = aws_s3_bucket.raw.id

  lambda_function {
    lambda_function_arn = var.ingestion_lambda_arn
    events              = ["s3:ObjectCreated:Put"]
    filter_prefix       = "raw/"
    filter_suffix       = ".json"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_lifecycle" {
  bucket = aws_s3_bucket.raw.id

  rule {
    id     = "archive-raw"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }
  }

  rule {
    id     = "clean-quarantine"
    status = "Enabled"

    filter {
      prefix = "quarantine/"
    }

    expiration {
      days = 30
    }
  }
}
