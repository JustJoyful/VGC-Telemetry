import json

import boto3
import pytest
from botocore.exceptions import ClientError

from tests.publication_helpers import staged


def legacy_payload(tid="valid"):
    return {
        "metadata": {
            "tid": tid,
            "name": "Test",
            "date": "2026-10-05",
            "regulation": "M-C",
            "tier": "local",
        },
        "teams": [],
        "matches": [],
    }


def records(bucket, keys):
    return {
        "Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}} for key in keys]
    }


def test_mixed_batch_preserves_valid_quarantine_and_raises_retry_failure(
    s3_bucket, dynamodb_table, monkeypatch
):
    import ingestion.handler as handler
    from db.schema import status_sk, tournament_pk

    s3_bucket.Object("raw/bad.json").put(Body=b"{")
    s3_bucket.Object("raw/good.json").put(Body=json.dumps(legacy_payload()))
    s3 = boto3.client("s3")
    original = s3.get_object

    def fail(**kwargs):
        if kwargs["Key"] == "raw/transient.json":
            raise ClientError({"Error": {"Code": "SlowDown"}}, "GetObject")
        return original(**kwargs)

    monkeypatch.setattr(s3, "get_object", fail)
    monkeypatch.setattr(handler.boto3, "client", lambda _: s3)
    waits = []
    with pytest.raises(RuntimeError) as caught:
        handler.handle_s3_event(
            records(s3_bucket.name, ["raw/bad.json", "raw/transient.json", "raw/good.json"]),
            sleep=waits.append,
            random=lambda: 0,
        )
    assert [r["status"] for r in caught.value.results] == ["quarantined", "error", "processed"]
    assert len(waits) == 2 and waits[1] > waits[0]
    assert (
        dynamodb_table.get_item(Key={"PK": tournament_pk("valid"), "SK": status_sk()})["Item"][
            "status"
        ]
        == "PROCESSED"
    )


def test_legacy_exact_s3_version(s3_bucket, dynamodb_table):
    from ingestion.handler import handle_s3_event

    s3 = boto3.client("s3")
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    version = s3.put_object(
        Bucket=s3_bucket.name, Key="raw/event.json", Body=json.dumps(legacy_payload())
    )["VersionId"]
    s3.put_object(Bucket=s3_bucket.name, Key="raw/event.json", Body=b"bad current version")
    event = records(s3_bucket.name, ["raw/event.json"])
    event["Records"][0]["s3"]["object"]["versionId"] = version
    assert handle_s3_event(event)["results"][0]["status"] == "processed"


def test_native_exact_version_publication_and_namespace_isolation(s3_bucket, dynamodb_table):
    from ingestion.native_handler import handle_native_event

    s3 = boto3.client("s3")
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    s3, key = staged(
        s3_bucket, mutate=lambda t: (setattr(t, "teams", t.teams[:1]), setattr(t, "matches", []))
    )
    version = s3.head_object(Bucket=s3_bucket.name, Key=key)["VersionId"]
    s3.put_object(Bucket=s3_bucket.name, Key=key, Body=b"{}")
    event = {"normalized": [{"bucket": s3_bucket.name, "key": key, "version_id": version}]}
    first = handle_native_event(event, table=dynamodb_table, s3_client=s3, now=100)
    assert first["results"][0]["status"] == "published"
    duplicate = handle_native_event(event, table=dynamodb_table, s3_client=s3, now=100)
    assert duplicate["results"][0]["status"] == "published"
    assert all(row["PK"].startswith("V2#") for row in dynamodb_table.scan()["Items"])


@pytest.mark.parametrize("body", [b"bad json", b'{"schema_version":2,"result":{}}'])
def test_native_invalid_json_schema_quarantined(s3_bucket, dynamodb_table, body):
    from ingestion.native_handler import handle_native_event

    s3 = boto3.client("s3")
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    v = s3.put_object(Bucket=s3_bucket.name, Key="staging/v2/normalized/invalid.json", Body=body)[
        "VersionId"
    ]
    result = handle_native_event(
        {
            "normalized": [
                {
                    "bucket": s3_bucket.name,
                    "key": "staging/v2/normalized/invalid.json",
                    "version_id": v,
                }
            ]
        },
        table=dynamodb_table,
        s3_client=s3,
        now=100,
    )
    assert result["results"][0]["status"] == "quarantined"
    assert dynamodb_table.scan()["Count"] == 0


def test_transient_ddb_writer_retries_but_programming_errors_are_not_quarantine(
    s3_bucket, dynamodb_table, monkeypatch
):
    import ingestion.handler as handler

    s3_bucket.Object("raw/event.json").put(Body=json.dumps(legacy_payload()))
    original = handler.write_tournament_data
    calls = 0

    def flaky(*args):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem"
            )
        return original(*args)

    monkeypatch.setattr(handler, "write_tournament_data", flaky)
    result = handler.handle_s3_event(
        records(s3_bucket.name, ["raw/event.json"]), sleep=lambda _: None
    )
    assert result["results"][0]["status"] == "processed"
    monkeypatch.setattr(
        handler,
        "parse_tournament_export",
        lambda _: (_ for _ in ()).throw(TypeError("programming fault")),
    )
    with pytest.raises(TypeError):
        handler.handle_s3_event(records(s3_bucket.name, ["raw/event.json"]))
    assert list(s3_bucket.objects.filter(Prefix="quarantine/")) == []


def test_batch_unprocessed_exhaustion_has_bounded_injected_backoff(dynamodb_table, monkeypatch):
    from db.client import batch_write_with_retry

    client = dynamodb_table.meta.client
    monkeypatch.setattr(
        client, "batch_write_item", lambda **kw: {"UnprocessedItems": kw["RequestItems"]}
    )
    waits = []
    with pytest.raises(RuntimeError):
        batch_write_with_retry(
            dynamodb_table,
            [{"PK": "test", "SK": "row"}],
            max_retries=3,
            sleep=waits.append,
            random=lambda: 0,
        )
    assert len(waits) == 2 and waits[1] > waits[0]


def test_native_operational_failure_preserves_independent_success(
    s3_bucket, dynamodb_table, monkeypatch
):
    from ingestion.native_handler import handle_native_event
    from ingestion.retry import BatchFailure

    s3, key = staged(
        s3_bucket, mutate=lambda t: (setattr(t, "teams", t.teams[:1]), setattr(t, "matches", []))
    )
    original = s3.get_object

    def transient(**kwargs):
        if kwargs["Key"].endswith("failed.json"):
            raise ClientError({"Error": {"Code": "SlowDown"}}, "GetObject")
        return original(**kwargs)

    monkeypatch.setattr(s3, "get_object", transient)
    waits = []
    with pytest.raises(BatchFailure) as caught:
        handle_native_event(
            {
                "normalized": [
                    {
                        "bucket": s3_bucket.name,
                        "key": "staging/v2/normalized/failed.json",
                        "version_id": "exact",
                    },
                    {"bucket": s3_bucket.name, "key": key},
                ]
            },
            table=dynamodb_table,
            s3_client=s3,
            now=100,
            sleep=waits.append,
            random=lambda: 0,
        )
    assert [r["status"] for r in caught.value.results] == ["error", "published"]
    assert len(waits) == 2
