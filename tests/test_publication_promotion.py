from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from tests.publication_helpers import manifest


@pytest.fixture(autouse=True)
def publication_clock(monkeypatch):
    monkeypatch.setattr("db.publication.clock_now", lambda: 100)


def lease(table, event_id, owner="worker", now=100, ttl=60):
    from db.leases import acquire_lease
    from db.schema_v2 import event_key, lease_key

    return acquire_lease(
        table=table, key=lease_key(event_key(event_id)), owner=owner, now=now, ttl=ttl
    )


def test_owner_expiry_and_reacquisition(dynamodb_table):
    from db.leases import acquire_lease, renew_lease

    key = {"PK": "V2#EVENT#test", "SK": "LEASE"}
    first = acquire_lease(table=dynamodb_table, key=key, owner="a", now=100, ttl=10)
    with pytest.raises(ClientError):
        acquire_lease(table=dynamodb_table, key=key, owner="b", now=101, ttl=10)
    second = acquire_lease(table=dynamodb_table, key=key, owner="a", now=110, ttl=10)
    assert first.token != second.token
    with pytest.raises(ClientError):
        renew_lease(first, table=dynamodb_table, now=111)
    renewed = renew_lease(second, table=dynamodb_table, now=115)
    assert renewed.expires_at == 125


def test_corrections_duplicates_order_conflict_and_durable_intents(s3_bucket, dynamodb_table):
    from db.publication import promote_revision
    from db.schema_v2 import event_key

    first = manifest(s3_bucket, dynamodb_table)
    held = lease(dynamodb_table, first.event_id)
    promoted = promote_revision(first, table=dynamodb_table, lease=held, expected_revision=None)
    assert promoted.status == "promoted" and promoted.intents
    duplicate = promote_revision(
        first, table=dynamodb_table, lease=held, expected_revision=first.revision_id
    )
    assert duplicate.status == "duplicate" and duplicate.intents == promoted.intents
    conflict = manifest(
        s3_bucket, dynamodb_table, mutate=lambda t: setattr(t.metadata, "name", "different")
    )
    assert (
        promote_revision(
            conflict, table=dynamodb_table, lease=held, expected_revision=first.revision_id
        ).status
        == "pending_order"
    )
    newer = manifest(
        s3_bucket,
        dynamodb_table,
        mutate=lambda t: setattr(t, "teams", []),
        fetched_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    assert (
        promote_revision(
            newer, table=dynamodb_table, lease=held, expected_revision=first.revision_id
        ).status
        == "promoted"
    )
    assert (
        promote_revision(
            first, table=dynamodb_table, lease=held, expected_revision=newer.revision_id
        ).status
        == "stale"
    )
    assert (
        dynamodb_table.get_item(Key=event_key(first.event_id))["Item"]["revision_id"]
        == newer.revision_id
    )


def test_same_content_newer_order_prevents_delayed_correction(s3_bucket, dynamodb_table):
    from db.publication import promote_revision

    first = manifest(s3_bucket, dynamodb_table)
    held = lease(dynamodb_table, first.event_id)
    promote_revision(first, table=dynamodb_table, lease=held, expected_revision=None)
    newer = manifest(
        s3_bucket, dynamodb_table, fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc)
    )
    assert newer.revision_id == first.revision_id
    promote_revision(newer, table=dynamodb_table, lease=held, expected_revision=first.revision_id)
    correction = manifest(
        s3_bucket,
        dynamodb_table,
        mutate=lambda t: setattr(t.metadata, "name", "old correction"),
        fetched_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    assert (
        promote_revision(
            correction, table=dynamodb_table, lease=held, expected_revision=first.revision_id
        ).status
        == "stale"
    )


def test_stale_lease_and_expected_pointer_rejected(s3_bucket, dynamodb_table, monkeypatch):
    from db.publication import promote_revision

    first = manifest(s3_bucket, dynamodb_table)
    held = lease(dynamodb_table, first.event_id)
    lease(dynamodb_table, first.event_id, owner="next", now=161)
    with pytest.raises(ClientError):
        promote_revision(first, table=dynamodb_table, lease=held, expected_revision=None)
    held = lease(dynamodb_table, first.event_id, now=222)
    with pytest.raises(ClientError):
        promote_revision(first, table=dynamodb_table, lease=held, expected_revision="wrong")


def test_regulation_reassignment_intents_cover_old_and_new(s3_bucket, dynamodb_table):
    from db.publication import promote_revision

    first = manifest(s3_bucket, dynamodb_table)
    held = lease(dynamodb_table, first.event_id)
    promote_revision(first, table=dynamodb_table, lease=held, expected_revision=None)
    newer = manifest(
        s3_bucket,
        dynamodb_table,
        mutate=lambda t: setattr(t.metadata, "rules_id", "M-B"),
        fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    result = promote_revision(
        newer, table=dynamodb_table, lease=held, expected_revision=first.revision_id
    )
    assert {i["population_id"] for i in result.intents} == set(
        first.populations + newer.populations
    )


def test_promotion_marks_population_dirty_before_recovery(s3_bucket, dynamodb_table):
    from db.publication import promote_revision
    from db.schema_v2 import population_key

    m = manifest(s3_bucket, dynamodb_table)
    held = lease(dynamodb_table, m.event_id)
    promote_revision(m, table=dynamodb_table, lease=held, expected_revision=None)
    state = dynamodb_table.get_item(Key=population_key(m.populations[0])).get("Item")
    assert state and state["pending"] == 1 and state["epoch"] == 1
