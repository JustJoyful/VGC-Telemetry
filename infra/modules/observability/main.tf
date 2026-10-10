resource "aws_sns_topic" "operations" {
  name = "vgc-telemetry-operations-${var.environment}"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_sns_topic_policy" "operations" {
  arn = aws_sns_topic.operations.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "SNS:*"
        Resource  = aws_sns_topic.operations.arn
      },
      {
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.operations.arn
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:cloudwatch:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:alarm:vgc-telemetry-${var.environment}-*"
          }
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.operations.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

locals {
  namespace = "VGCTelemetry/Automation"
  counters = {
    quarantine_native = { metric = "Quarantines", worker = "native", threshold = 1 }
    quarantine_legacy = { metric = "Quarantines", worker = "legacy", threshold = 1 }
    exhausted_native  = { metric = "RetryExhaustions", worker = "native", threshold = 1 }
    exhausted_legacy  = { metric = "RetryExhaustions", worker = "legacy", threshold = 1 }
    catalog_pending   = { metric = "CatalogPending", worker = "reference", threshold = 1 }
    errors_scraper    = { metric = "WorkerErrors", worker = "scraper", threshold = 3 }
    errors_reference  = { metric = "WorkerErrors", worker = "reference", threshold = 3 }
    errors_native     = { metric = "WorkerErrors", worker = "native", threshold = 3 }
    errors_legacy     = { metric = "WorkerErrors", worker = "legacy", threshold = 3 }
    errors_build      = { metric = "WorkerErrors", worker = "build", threshold = 3 }
  }
  freshness = {
    tournament = { metric = "TournamentCheckAge", source = "limitless", worker = "scraper", threshold = 86400, period = 86400 }
    reference  = { metric = "ReferenceCheckAge", source = "official", worker = "reference", threshold = 172800, period = 172800 }
  }
}

resource "aws_cloudwatch_metric_alarm" "counter" {
  for_each            = local.counters
  alarm_name          = "vgc-telemetry-${var.environment}-${each.key}"
  alarm_description   = "${each.value.metric} reached ${each.value.threshold}; runbook: SOURCE_UPDATE.md#phase-3-alarms"
  namespace           = local.namespace
  metric_name         = each.value.metric
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    Environment = var.environment
    Source      = each.value.worker == "reference" ? "official" : "limitless"
    Worker      = each.value.worker
  }
  alarm_actions = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "freshness" {
  for_each            = local.freshness
  alarm_name          = "vgc-telemetry-${var.environment}-${each.key}-check-age"
  alarm_description   = "${each.value.metric} exceeds ${each.value.threshold}s or the scheduled check stopped; runbook: SOURCE_UPDATE.md#phase-3-alarms"
  namespace           = local.namespace
  metric_name         = each.value.metric
  statistic           = "Maximum"
  period              = each.value.period
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  dimensions = {
    Environment = var.environment
    Source      = each.value.source
    Worker      = each.value.worker
  }
  alarm_actions = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "publication_lag" {
  alarm_name          = "vgc-telemetry-${var.environment}-publication-lag"
  alarm_description   = "Validated input took over one hour to publish; runbook: SOURCE_UPDATE.md#phase-3-alarms"
  namespace           = local.namespace
  metric_name         = "PublicationLag"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 3600
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  dimensions = {
    Environment = var.environment
    Source      = "limitless"
    Worker      = "native"
  }
  alarm_actions = [aws_sns_topic.operations.arn]
}
