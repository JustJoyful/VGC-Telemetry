data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# =============================================================
# 1. API Lambda IAM Role (Read-only + LeadingKeys Rate Limiter)
# =============================================================
resource "aws_iam_role" "api_role" {
  name               = "vgc-telemetry-api-role-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
  }
}

resource "aws_iam_role_policy" "api_policy" {
  name = "vgc-telemetry-api-policy-${var.environment}"
  role = aws_iam_role.api_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "DynamoDBReadOnly"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          var.table_arn,
          "${var.table_arn}/index/*"
        ]
      },
      {
        Sid    = "DynamoDBRateLimitScopedUpdate"
        Effect = "Allow"
        Action = [
          "dynamodb:UpdateItem"
        ]
        Resource = var.table_arn
        Condition = {
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = ["RATELIMIT#*"]
          }
        }
      }
    ]
  })
}

# =============================================================
# 2. Ingestion ETL Lambda IAM Role (DynamoDB RW + S3 Read/Put)
# =============================================================
resource "aws_iam_role" "ingestion_role" {
  name               = "vgc-telemetry-ingestion-role-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
  }
}

resource "aws_iam_role_policy" "ingestion_policy" {
  name = "vgc-telemetry-ingestion-policy-${var.environment}"
  role = aws_iam_role.ingestion_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "DynamoDBReadWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:BatchWriteItem"
        ]
        Resource = [
          var.table_arn,
          "${var.table_arn}/index/*"
        ]
      },
      {
        Sid    = "S3RawRead"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${var.bucket_arn}/raw/*"
      },
      {
        Sid    = "S3QuarantineWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = "${var.bucket_arn}/quarantine/*"
      }
    ]
  })
}

# =============================================================
# 3. Scraper Lambda IAM Role (S3 Write only — Zero DynamoDB)
# =============================================================
resource "aws_iam_role" "scraper_role" {
  name               = "vgc-telemetry-scraper-role-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
  }
}

resource "aws_iam_role_policy" "scraper_policy" {
  name = "vgc-telemetry-scraper-policy-${var.environment}"
  role = aws_iam_role.scraper_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "S3RawWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = "${var.bucket_arn}/raw/*"
      }
    ]
  })
}
