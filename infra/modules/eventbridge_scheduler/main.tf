locals {
  scraper_schedules = {
    discover  = { expression = "rate(${var.discovery_hours} hours)", payload = { mode = "discover" } }
    recent    = { expression = "rate(${var.discovery_hours} hours)", payload = { mode = "recent" } }
    reference = { expression = "rate(${var.reference_hours} hours)", payload = { mode = "reference" } }
    reconcile = { expression = var.reconcile_expression, payload = { mode = "reconcile" } }
  }
}

resource "aws_cloudwatch_event_rule" "scraper" {
  for_each            = local.scraper_schedules
  name                = "vgc-telemetry-${each.key}-${var.environment}"
  schedule_expression = each.value.expression
  is_enabled          = var.source_admission_verified
}

resource "aws_cloudwatch_event_target" "scraper" {
  for_each  = local.scraper_schedules
  rule      = aws_cloudwatch_event_rule.scraper[each.key].name
  target_id = "ScraperLambdaTarget"
  arn       = var.scraper_lambda_arn
  input     = jsonencode(each.value.payload)
}

resource "aws_lambda_permission" "scraper" {
  for_each      = local.scraper_schedules
  statement_id  = "AllowEventBridge${title(each.key)}"
  action        = "lambda:InvokeFunction"
  function_name = var.scraper_lambda_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scraper[each.key].arn
}

resource "aws_cloudwatch_event_rule" "recovery" {
  name                = "vgc-telemetry-recovery-${var.environment}"
  schedule_expression = "rate(${var.recovery_minutes} minutes)"
  is_enabled          = var.source_admission_verified
}

resource "aws_cloudwatch_event_target" "recovery" {
  rule      = aws_cloudwatch_event_rule.recovery.name
  target_id = "IngestionRecoveryTarget"
  arn       = var.ingestion_lambda_arn
  input     = jsonencode({ mode = "native", recover = true })
}

resource "aws_lambda_permission" "recovery" {
  statement_id  = "AllowEventBridgeRecovery"
  action        = "lambda:InvokeFunction"
  function_name = var.ingestion_lambda_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.recovery.arn
}
