resource "aws_cloudwatch_event_rule" "weekly_scraper" {
  name                = "vgc-telemetry-weekly-scraper-${var.environment}"
  description         = "Trigger Scraper Lambda every Monday at 06:00 UTC"
  schedule_expression = "cron(0 6 ? * MON *)"

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "scraper_target" {
  rule      = aws_cloudwatch_event_rule.weekly_scraper.name
  target_id = "ScraperLambdaTarget"
  arn       = var.scraper_lambda_arn
}

resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = var.scraper_lambda_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_scraper.arn
}
