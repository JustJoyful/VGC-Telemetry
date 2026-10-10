data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_caller_identity" "current" {}

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
        Sid      = "S3VerifiedCatalogRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.bucket_arn}/staging/v2/catalog/*"
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
        Sid      = "AutomationMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "VGCTelemetry/Automation" }
        }
      },
      {
        Sid    = "DynamoDBIngestionScoped"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:BatchWriteItem",
          "dynamodb:ConditionCheckItem"
        ]
        Resource = var.table_arn
        Condition = {
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = [
              "TOURNAMENT#*", "REGULATION#*", "V2#EVENT#*", "V2#REV#*",
              "V2#POP#*", "V2#GEN#*", "V2#WORK", "V2#DIRTY",
              "V2#PUBLIC*", "V2#RECOVERY", "V2#BUILD*"
            ]
          }
        }
      },
      {
        Sid      = "CatalogMetadataRead"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = var.table_arn
        Condition = {
          "ForAllValues:StringEquals" = {
            "dynamodb:LeadingKeys" = ["V2#CATALOG"]
          }
        }
      },
      {
        Sid    = "S3RawRead"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion"
        ]
        Resource = "${var.bucket_arn}/raw/*"
      },
      {
        Sid    = "S3NativeRead"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = [
          "${var.bucket_arn}/staging/v2/captures/*",
          "${var.bucket_arn}/staging/v2/normalized/*",
          "${var.bucket_arn}/staging/v2/contributions/*",
          "${var.bucket_arn}/staging/v2/catalog/*",
          "${var.bucket_arn}/staging/v2/builds/*"
        ]
      },
      {
        Sid      = "S3BuildEvidenceWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${var.bucket_arn}/staging/v2/builds/*"
      },
      {
        Sid      = "S3NativeContributionsWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${var.bucket_arn}/staging/v2/contributions/*"
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
# 3. Scraper Lambda IAM Role
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
        Sid      = "AutomationMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "VGCTelemetry/Automation" }
        }
      },
      {
        Sid    = "S3StagingWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = [
          "${var.bucket_arn}/staging/v2/captures/*",
          "${var.bucket_arn}/staging/v2/normalized/*",
          "${var.bucket_arn}/staging/v2/catalog/*"
        ]
      },
      {
        Sid    = "CollectionState"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:DeleteItem", "dynamodb:Query",
          "dynamodb:ConditionCheckItem"
        ]
        Resource = var.table_arn
        Condition = {
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = ["V2#COLLECT#*", "V2#COLLECTION_WORK#limitless", "V2#CATALOG"]
          }
        }
      },
      {
        Sid      = "NativeIngestionHandoff"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.ingestion_lambda_name}"
      },
      {
        Sid      = "CollectionContinuation"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:vgc-telemetry-scraper-${var.environment}"
      }
    ]
  })
}
