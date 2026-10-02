import json

import boto3

from ingestion.handler import handle_s3_event
from ingestion.quarantine import quarantine_payload


def test_quarantine_payload_writes_to_quarantine_prefix(s3_bucket):
    raw_content = b'{"invalid": "data", "missing": "all_required_fields"}'
    quarantine_key = quarantine_payload(
        s3_client=boto3.client("s3", region_name="us-east-1"),
        bucket_name=s3_bucket.name,
        source_key="raw/invalid_tournament.json",
        raw_content=raw_content,
        error_message="ContractValidationError: Missing metadata",
    )

    assert quarantine_key.startswith("quarantine/")
    assert "invalid_tournament.json" in quarantine_key

    # Verify object in S3
    obj = s3_bucket.Object(quarantine_key).get()
    quarantined_data = json.loads(obj["Body"].read().decode("utf-8"))
    assert quarantined_data["error"] == "ContractValidationError: Missing metadata"
    assert quarantined_data["source_key"] == "raw/invalid_tournament.json"
    assert "timestamp" in quarantined_data


def test_handler_ignores_quarantine_prefix_events(s3_bucket, dynamodb_table):
    """Defense-in-depth: Even if S3 prefix filter misconfigured, handler MUST ignore quarantine/."""
    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": s3_bucket.name},
                    "object": {"key": "quarantine/20260920_bad_file.json"},
                }
            }
        ]
    }

    result = handle_s3_event(event)
    assert result["status"] == "skipped"
    assert result["reason"] == "quarantine_prefix_ignored"

    # DynamoDB should have zero items
    assert dynamodb_table.scan()["Count"] == 0
