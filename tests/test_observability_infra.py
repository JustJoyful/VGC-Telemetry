from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "infra"


def test_alarm_contract_and_email_wiring():
    hcl = (ROOT / "modules/observability/main.tf").read_text()
    assert 'resource "aws_sns_topic" "operations"' in hcl
    assert 'protocol  = "email"' in hcl
    assert "endpoint  = var.notification_email" in hcl
    for metric in (
        "Quarantines",
        "RetryExhaustions",
        "WorkerErrors",
        "TournamentCheckAge",
        "ReferenceCheckAge",
        "PublicationLag",
        "CatalogPending",
    ):
        assert metric in hcl
    for threshold in ("86400", "172800", "3600"):
        assert threshold in hcl
    assert 'treat_missing_data  = "breaching"' in hcl
    assert 'treat_missing_data  = "notBreaching"' in hcl
    assert hcl.count("alarm_actions = [aws_sns_topic.operations.arn]") == 3
    assert '"aws:SourceAccount"' in hcl
    assert "event_id" not in hcl and "player_id" not in hcl
    for env in ("dev", "prod"):
        main = (ROOT / f"environments/{env}/main.tf").read_text()
        assert 'module "observability"' in main
        assert "notification_email = var.notification_email" in main


def test_metric_permissions_are_namespace_scoped_and_workers_cannot_publish_sns():
    hcl = (ROOT / "modules/iam/main.tf").read_text()
    assert hcl.count('Action   = ["cloudwatch:PutMetricData"]') == 2
    assert hcl.count('"cloudwatch:namespace" = "VGCTelemetry/Automation"') == 2
    assert '"sns:Publish"' not in hcl
    for worker in ("ingestion", "scraper"):
        env = (ROOT / f"modules/lambda_{worker}/main.tf").read_text()
        assert "ENVIRONMENT" in env
    scraper = (ROOT / "modules/lambda_scraper/main.tf").read_text()
    assert "SCRAPER_FUNCTION_NAME" in scraper
    assert (
        "CollectionContinuation" in hcl
        or "CollectionContinuation" in (ROOT / "modules/iam/main.tf").read_text()
    )
