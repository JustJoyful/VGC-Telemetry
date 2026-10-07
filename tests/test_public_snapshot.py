from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

from db.leases import acquire_lease
from db.publication import get
from tests.phase2_helpers import publish_input


def held(table):
    from db.schema_public import public_lease_key

    return acquire_lease(table=table, key=public_lease_key(), owner="builder", now=100, ttl=60)


def finish(table, s3, lease, max_steps=100):
    from ingestion.public_snapshot import build_public_snapshot, publish_public_snapshot

    for _ in range(100):
        result = build_public_snapshot(
            table=table, s3_client=s3, lease=lease, clock=lambda: 100, max_steps=max_steps
        )
        if result.status == "complete":
            assert publish_public_snapshot(result, table=table, lease=lease, clock=lambda: 100)
            return result
    pytest.fail("Build did not converge")


def test_empty_snapshot_requires_validated_complete_marker(dynamodb_table):
    import boto3

    from db.schema_public import public_state_key, snapshot_row_key

    result = finish(dynamodb_table, boto3.client("s3"), held(dynamodb_table), max_steps=1)
    complete = get(dynamodb_table, snapshot_row_key(result.snapshot_id, "META", "COMPLETE"))
    assert complete["row_count"] == 0
    assert get(dynamodb_table, public_state_key())["snapshot_id"] == result.snapshot_id


def test_real_snapshot_checkpoint_correction_and_retained_rollback(dynamodb_table, s3_bucket):
    from db.schema_public import public_state_key, snapshot_row_key
    from ingestion.public_snapshot import rollback_public_snapshot

    table = dynamodb_table
    value, s3 = publish_input(table, s3_bucket)
    lease = held(table)
    first = finish(table, s3, lease, max_steps=1)
    event = get(table, snapshot_row_key(first.snapshot_id, "EVENT#" + value.event_id, "DETAIL"))[
        "data"
    ]
    assert event["coverage"]["standings_entries"] == 6
    assert event["coverage"]["eligible_complete_rosters"] == 2
    publish_input(
        table,
        s3_bucket,
        mutate=lambda t: setattr(t, "teams", []),
        captured_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    second = finish(table, s3, lease)
    assert second.snapshot_id != first.snapshot_id
    assert (
        get(table, snapshot_row_key(first.snapshot_id, "EVENT#" + value.event_id, "DETAIL"))["data"]
        == event
    )
    assert rollback_public_snapshot(
        first.snapshot_id,
        table=table,
        lease=lease,
        expected_snapshot_id=second.snapshot_id,
        clock=lambda: 100,
    )
    assert get(table, public_state_key())["dirty"] is True
    assert get(table, public_state_key())["epoch"] == 2


@pytest.mark.parametrize("kind", ["input", "team", "event", "contribution", "complete"])
def test_crash_after_immutable_write_resumes_without_partial_publication(
    kind, dynamodb_table, s3_bucket, monkeypatch
):
    import ingestion.public_snapshot as snapshots
    from db.schema_public import public_state_key

    table = dynamodb_table
    _, s3 = publish_input(table, s3_bucket)
    lease = held(table)
    original = snapshots.immutable_row
    seen = False

    def interrupted(table, row):
        nonlocal seen
        original(table, row)
        if row.get("kind") == kind and not seen:
            seen = True
            raise RuntimeError("crash after durable write")

    monkeypatch.setattr(snapshots, "immutable_row", interrupted)
    with pytest.raises(RuntimeError, match="crash"):
        snapshots.build_public_snapshot(table=table, s3_client=s3, lease=lease, clock=lambda: 100)
    assert get(table, public_state_key()).get("snapshot_id") is None
    monkeypatch.setattr(snapshots, "immutable_row", original)
    result = finish(table, s3, lease)
    assert result.checkpoint.input_count == 1


def test_changed_epoch_or_stolen_expired_lease_cannot_publish(dynamodb_table, s3_bucket):
    from db.schema_public import public_state_key
    from ingestion.public_snapshot import build_public_snapshot, publish_public_snapshot

    table = dynamodb_table
    _, s3 = publish_input(table, s3_bucket)
    lease = held(table)
    result = build_public_snapshot(table=table, s3_client=s3, lease=lease, clock=lambda: 100)
    assert result.status == "complete"
    with pytest.raises(ClientError):
        publish_public_snapshot(result, table=table, lease=lease, clock=lambda: 161)
    table.update_item(
        Key=public_state_key(),
        UpdateExpression="ADD epoch :one",
        ExpressionAttributeValues={":one": 1},
    )
    assert not publish_public_snapshot(result, table=table, lease=lease, clock=lambda: 100)
    assert get(table, public_state_key()).get("snapshot_id") is None


def test_two_real_events_have_one_compatible_snapshot(dynamodb_table, s3_bucket):
    from db.schema_public import snapshot_pk
    from ingestion.contributions import query_rows

    first, s3 = publish_input(dynamodb_table, s3_bucket, second=False)
    second, _ = publish_input(dynamodb_table, s3_bucket, second=True)
    result = finish(dynamodb_table, s3, held(dynamodb_table))
    events = query_rows(dynamodb_table, snapshot_pk(result.snapshot_id, "EVENTS"))["Items"]
    assert {r["data"]["event_id"] for r in events} == {first.event_id, second.event_id}
    assert sum(r["data"]["coverage"]["standings_entries"] for r in events) == 37
    assert sum(r["data"]["coverage"]["eligible_complete_rosters"] for r in events) == 10
    assert sum(r["data"]["coverage"]["unknown_stat_points"] for r in events) == 60


def test_epoch_changes_after_directory_copy_fence_builder(dynamodb_table, s3_bucket):
    from db.schema_public import public_state_key
    from ingestion.public_snapshot import build_public_snapshot

    _, s3 = publish_input(dynamodb_table, s3_bucket)
    lease = held(dynamodb_table)
    first = build_public_snapshot(
        table=dynamodb_table, s3_client=s3, lease=lease, clock=lambda: 100, max_steps=1
    )
    assert first.status == "pending"
    dynamodb_table.update_item(
        Key=public_state_key(),
        UpdateExpression="ADD epoch :one",
        ExpressionAttributeValues={":one": 1},
    )
    second = build_public_snapshot(
        table=dynamodb_table, s3_client=s3, lease=lease, clock=lambda: 100, max_steps=1
    )
    assert second.snapshot_id != first.snapshot_id
    assert get(dynamodb_table, public_state_key()).get("snapshot_id") is None
