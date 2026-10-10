from types import SimpleNamespace

import pytest

from tests.test_build_links import build, event, source


def test_explicit_build_handler_uses_pinned_native_inputs(dynamodb_table, monkeypatch):
    import ingestion.build_handler as handler
    from db.schema_v2 import event_key

    tournament = event()
    record = build()
    observed = SimpleNamespace(
        result=SimpleNamespace(tournament=tournament), event_id="limitless:event-1"
    )
    monkeypatch.setattr(handler, "read_staged_result", lambda **kwargs: observed)
    monkeypatch.setattr(handler, "revision_id", lambda value: "observed-revision")
    dynamodb_table.put_item(
        Item={**event_key(observed.event_id), "revision_id": "observed-revision"}
    )
    import boto3

    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="handler-builds")
    result = handler.handle_build_event(
        {
            "build": {
                "document": record.document.model_dump(),
                "source": source().model_dump(),
                "captured_at": record.evidence.captured_at,
            },
            "normalized": [{"bucket": "handler-builds", "key": "staging/v2/normalized/exact.json"}],
        },
        table=dynamodb_table,
        s3_client=s3,
        bucket="handler-builds",
    )
    assert result["status"] == "linked" and result["event_revision"] == "observed-revision"
    assert (
        dynamodb_table.get_item(Key=event_key(observed.event_id))["Item"]["revision_id"]
        == "observed-revision"
    )


def test_lambda_routes_explicit_build_mode(monkeypatch):
    from ingestion import build_handler, handler

    monkeypatch.setattr(
        build_handler, "handle_build_event", lambda event, context: {"status": "unlinked"}
    )
    assert handler.lambda_handler({"mode": "build"}) == {"status": "unlinked"}


def test_malformed_build_error_does_not_expose_document(dynamodb_table, s3_bucket, caplog):
    from ingestion.build_handler import handle_build_event

    secret = "private team sheet and player name"
    with pytest.raises(ValueError, match="Invalid public build evidence") as caught:
        handle_build_event(
            {
                "normalized": [{"bucket": s3_bucket.name, "key": "staging/v2/normalized/x.json"}],
                "build": {"document": {"body": secret}, "source": {}, "captured_at": "bad"},
            },
            table=dynamodb_table,
            s3_client=None,
            bucket=s3_bucket.name,
        )
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert secret not in caplog.text
