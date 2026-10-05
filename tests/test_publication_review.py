"""Regression cases found during the whole-phase publication review."""

import boto3
import pytest
from botocore.exceptions import ClientError

from db.publication import get
from db.schema_v2 import dirty_key, lease_key, population_key
from ingestion.native_handler import handle_native_event
from tests.publication_helpers import promote


@pytest.mark.parametrize("body", [b"null", b"[]", b"42", b'{"schema_version":2}'])
def test_native_malformed_container_is_quarantined(s3_bucket, dynamodb_table, body):
    s3 = boto3.client("s3")
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    key = "staging/v2/normalized/malformed.json"
    version = s3.put_object(Bucket=s3_bucket.name, Key=key, Body=body)["VersionId"]
    result = handle_native_event(
        {"normalized": [{"bucket": s3_bucket.name, "key": key, "version_id": version}]},
        table=dynamodb_table,
        s3_client=s3,
        now=100,
    )
    assert result["results"][0]["status"] == "quarantined"
    assert dynamodb_table.scan()["Count"] == 0


def test_native_programming_keyerror_propagates_without_quarantine(
    s3_bucket, dynamodb_table, monkeypatch
):
    import ingestion.native_handler as native

    s3 = boto3.client("s3")
    key = "staging/v2/normalized/test.json"
    s3.put_object(Bucket=s3_bucket.name, Key=key, Body=b"{}")

    def broken(**kwargs):
        raise KeyError("programming fault")

    monkeypatch.setattr(native, "read_staged_result", broken)
    with pytest.raises(KeyError, match="programming fault"):
        native.handle_native_event(
            {"normalized": [{"bucket": s3_bucket.name, "key": key}]},
            table=dynamodb_table,
            s3_client=s3,
            now=100,
        )
    assert list(s3_bucket.objects.filter(Prefix="quarantine/")) == []


def seed_empty(table, pop):
    table.put_item(Item={**population_key(pop), "epoch": 1, "pending": 0})
    table.put_item(Item={**dirty_key(pop), "population_id": pop})


def test_recovery_pages_dirty_work_and_does_not_claim_early_completion(dynamodb_table, monkeypatch):
    import ingestion.native_handler as native

    for pop in ["a", "b", "c"]:
        seed_empty(dynamodb_table, pop)
    original = native.query_rows

    def small(table, pk, *args, **kwargs):
        if pk == "V2#DIRTY":
            kwargs["limit"] = 1
        return original(table, pk, *args, **kwargs)

    monkeypatch.setattr(native, "query_rows", small)
    first = native.handle_native_event(
        {"recover": True}, table=dynamodb_table, s3_client=boto3.client("s3"), now=100
    )
    assert first["results"][0]["status"] == "pending"
    for _ in range(3):
        final = native.handle_native_event(
            {"recover": True}, table=dynamodb_table, s3_client=boto3.client("s3"), now=100
        )
    assert final["results"][0]["status"] == "published"
    assert all(get(dynamodb_table, population_key(p)).get("publication") for p in ["a", "b", "c"])


def test_busy_population_does_not_starve_later_recovery_work(dynamodb_table):
    from db.leases import acquire_lease

    for pop in ["a", "b"]:
        seed_empty(dynamodb_table, pop)
    acquire_lease(
        table=dynamodb_table, key=lease_key(population_key("a")), owner="other", now=100, ttl=60
    )
    result = handle_native_event(
        {"recover": True}, table=dynamodb_table, s3_client=boto3.client("s3"), now=100
    )
    assert result["results"][0]["status"] == "pending"
    assert get(dynamodb_table, population_key("b")).get("publication")
    assert get(dynamodb_table, dirty_key("a"))


def test_generation_lease_expiring_during_copy_cannot_save_checkpoint(
    s3_bucket, dynamodb_table, monkeypatch
):
    import db.leases as leases
    import ingestion.generations as generations
    from ingestion.recovery import drain_intents

    m = promote(s3_bucket, dynamodb_table)
    drain_intents(table=dynamodb_table, owner="recover", now=100, limit=100)
    ticks = [0.0]
    monkeypatch.setattr(leases.time, "monotonic", lambda: ticks[0])
    original = generations._copy

    def slow(*args, **kwargs):
        original(*args, **kwargs)
        ticks[0] = 61.0

    monkeypatch.setattr(generations, "_copy", slow)
    with pytest.raises(ClientError):
        generations.build_generation(
            table=dynamodb_table,
            population_id=m.populations[0],
            owner="slow",
            now=100,
            checkpoint=None,
        )
    assert get(dynamodb_table, generations.build_key(m.populations[0])) is None


def test_recovery_lease_expiring_after_membership_cannot_ack(
    s3_bucket, dynamodb_table, monkeypatch
):
    import db.leases as leases
    import ingestion.recovery as recovery
    from ingestion.contributions import query_rows

    m = promote(s3_bucket, dynamodb_table)
    ticks = [0.0]
    monkeypatch.setattr(leases.time, "monotonic", lambda: ticks[0])
    original = recovery.reconcile_member

    def slow(*args, **kwargs):
        original(*args, **kwargs)
        ticks[0] = 61.0

    monkeypatch.setattr(recovery, "reconcile_member", slow)
    report = recovery.drain_intents(table=dynamodb_table, owner="slow", now=100, limit=100)
    assert report.deferred == 1 and report.acknowledged == 0
    assert get(dynamodb_table, population_key(m.populations[0]))["pending"] == 1
    assert not query_rows(dynamodb_table, "V2#WORK", "INTENT#")["Items"][0]["done"]


def test_deferred_dirty_page_prevents_completion_after_later_page(dynamodb_table, monkeypatch):
    import ingestion.native_handler as native
    from db.leases import acquire_lease

    for pop in ["a", "b", "c"]:
        seed_empty(dynamodb_table, pop)
    acquire_lease(
        table=dynamodb_table, key=lease_key(population_key("a")), owner="other", now=100, ttl=60
    )
    original = native.query_rows

    def small(table, pk, *args, **kwargs):
        if pk == "V2#DIRTY":
            kwargs["limit"] = 1
        return original(table, pk, *args, **kwargs)

    monkeypatch.setattr(native, "query_rows", small)
    for _ in range(3):
        outcome = native.handle_native_event(
            {"recover": True}, table=dynamodb_table, s3_client=boto3.client("s3"), now=100
        )
        assert outcome["results"][0]["status"] == "pending"
    assert get(dynamodb_table, population_key("b")).get("publication")
    assert get(dynamodb_table, population_key("c")).get("publication")


def test_malformed_native_record_does_not_drop_later_valid_delivery(s3_bucket, dynamodb_table):
    from tests.publication_helpers import staged

    s3, valid = staged(
        s3_bucket, mutate=lambda t: (setattr(t, "teams", t.teams[:1]), setattr(t, "matches", []))
    )
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    malformed = "staging/v2/normalized/bad-container.json"
    version = s3.put_object(Bucket=s3_bucket.name, Key=malformed, Body=b"[]")["VersionId"]
    result = handle_native_event(
        {
            "normalized": [
                {"bucket": s3_bucket.name, "key": malformed, "version_id": version},
                {"bucket": s3_bucket.name, "key": valid},
            ]
        },
        table=dynamodb_table,
        s3_client=s3,
        now=100,
    )
    assert [r["status"] for r in result["results"]] == ["quarantined", "published"]


@pytest.mark.parametrize("bad_key", [None, 17, [], {}])
def test_wrongly_typed_capture_reference_is_quarantined(s3_bucket, dynamodb_table, bad_key):
    import json

    from tests.publication_helpers import staged

    s3, valid = staged(s3_bucket)
    payload = json.loads(s3.get_object(Bucket=s3_bucket.name, Key=valid)["Body"].read())
    payload["capture_key"] = bad_key
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    malformed = "staging/v2/normalized/bad-reference.json"
    version = s3.put_object(Bucket=s3_bucket.name, Key=malformed, Body=json.dumps(payload))[
        "VersionId"
    ]
    result = handle_native_event(
        {"normalized": [{"bucket": s3_bucket.name, "key": malformed, "version_id": version}]},
        table=dynamodb_table,
        s3_client=s3,
        now=100,
    )
    assert result["results"][0]["status"] == "quarantined"
    assert dynamodb_table.scan()["Count"] == 0
