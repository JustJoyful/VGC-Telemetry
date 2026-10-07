from db.publication import get
from tests.phase2_helpers import publish_input


def test_inventory_seed_and_invalidation_are_explicit(dynamodb_table, s3_bucket):
    from db.schema_public import public_member_key, public_state_key
    from ingestion.public_replay import inventory
    from ingestion.public_snapshot import invalidate_public_inputs, seed_public_inputs

    table = dynamodb_table
    value, s3 = publish_input(table, s3_bucket)
    assert inventory(table)["event_ids"] == [value.event_id]
    table.delete_item(Key=public_member_key(value.event_id))
    result = seed_public_inputs([value.event_id], table=table, s3_client=s3, max_steps=1)
    assert result["status"] == "complete"
    assert get(table, public_member_key(value.event_id))["capture_hash"] == value.capture_hash
    before = get(table, public_state_key())["epoch"]
    invalidate_public_inputs(table=table, policy_version="limitless-single-bracket-v1")
    assert get(table, public_state_key())["epoch"] == before + 1
    assert get(table, public_state_key())["dirty"]
    missing = seed_public_inputs(["limitless:missing"], table=table, s3_client=s3)
    assert missing["gaps"]
    assert missing["status"] == "incomplete"


def test_recovery_without_delivery_publishes_dirty_public_work(dynamodb_table, s3_bucket):
    from db.schema_public import public_state_key
    from ingestion.native_handler import handle_native_event

    value, s3 = publish_input(dynamodb_table, s3_bucket)
    result = handle_native_event(
        {"recover": True}, table=dynamodb_table, s3_client=s3, now=100, max_steps=100
    )
    assert get(dynamodb_table, public_state_key()).get("snapshot_id")
    assert any(r.get("public_status") == "published" for r in result["results"])


def test_bounded_recovery_reports_pending_and_retains_dirty_work(dynamodb_table, s3_bucket):
    from db.schema_public import public_state_key
    from ingestion.native_handler import handle_native_event

    _, s3 = publish_input(dynamodb_table, s3_bucket)
    result = handle_native_event(
        {"recover": True}, table=dynamodb_table, s3_client=s3, now=100, max_steps=1
    )
    assert result["results"][0]["status"] == "pending"
    assert result["status"] == "pending"
    assert result["results"][0]["public_status"] == "pending"
    assert get(dynamodb_table, public_state_key()).get("snapshot_id") is None
    assert get(dynamodb_table, public_state_key())["dirty"] is True
