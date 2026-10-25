from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from core.models.collection import CollectionCheckpoint, EventWorkItem
from db.collection_state import advance_page, get_checkpoint, put_event_work, request_backfill_work
from db.leases import acquire_lease
from db.schema_collection import collection_lease_key, collection_state_key, event_work_key


def test_collection_keys_are_stable_and_collision_safe():
    assert event_work_key("limitless", "a#b", "capture") != event_work_key(
        "limitless", "a", "b#capture"
    )
    assert collection_state_key("limitless", "discover", "x") == collection_state_key(
        "limitless", "discover", "x"
    )


def test_collection_models_reject_bad_bounds_and_keep_numeric_cursor():
    state = CollectionCheckpoint(run_id="run", page=2, cursor=2)
    assert state.cursor == 2
    with pytest.raises(ValidationError):
        CollectionCheckpoint(run_id="run", page=-1)
    with pytest.raises(ValidationError):
        EventWorkItem(source="limitless", event_id="x", listing={"huge": "x" * 20000})


def test_page_advances_only_for_current_lease_and_expected_page(dynamodb_table):
    key = collection_state_key("limitless", "discover", "format")
    lease_key = collection_lease_key("limitless", "discover", "format")
    first = acquire_lease(table=dynamodb_table, key=lease_key, owner="one", now=10, ttl=10)
    with pytest.raises(ClientError):
        acquire_lease(table=dynamodb_table, key=lease_key, owner="two", now=11, ttl=10)
    advance_page(dynamodb_table, key, first, expected_page=0, page=1, now=11)
    assert get_checkpoint(dynamodb_table, key).page == 1
    with pytest.raises(ClientError):
        advance_page(dynamodb_table, key, first, expected_page=0, page=2, now=12)
    second = acquire_lease(table=dynamodb_table, key=lease_key, owner="two", now=20, ttl=10)
    with pytest.raises(ClientError):
        advance_page(dynamodb_table, key, first, expected_page=1, page=2, now=21)
    advance_page(dynamodb_table, key, second, expected_page=1, page=2, now=21)
    assert get_checkpoint(dynamodb_table, key).page == 2


def test_replayed_work_does_not_reset_terminal_status(dynamodb_table):
    item = EventWorkItem(source="limitless", event_id="a", kind="capture", listing={"players": 3})
    put_event_work(dynamodb_table, item)
    key = event_work_key("limitless", "a", "capture")
    dynamodb_table.update_item(
        Key=key,
        UpdateExpression="SET #status = :done, observed_order = :order",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":done": "completed", ":order": Decimal(3)},
    )
    put_event_work(dynamodb_table, item)
    got = dynamodb_table.get_item(Key=key)["Item"]
    assert got["status"] == "completed" and got["observed_order"] == 3


def test_newer_explicit_backfill_reopens_work_but_old_retry_does_not(dynamodb_table):
    key = event_work_key("limitless", "event", "backfill")
    request_backfill_work(dynamodb_table, "limitless", "event", 10)
    dynamodb_table.update_item(
        Key=key,
        UpdateExpression="SET #status = :done",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":done": "handed_off"},
    )
    request_backfill_work(dynamodb_table, "limitless", "event", 10)
    assert dynamodb_table.get_item(Key=key)["Item"]["status"] == "handed_off"
    request_backfill_work(dynamodb_table, "limitless", "event", 11)
    request_backfill_work(dynamodb_table, "limitless", "event", 10)
    assert dynamodb_table.get_item(Key=key)["Item"]["status"] == "pending"


def test_completed_discovery_restarts_from_the_first_page(dynamodb_table):
    from scraper.dispatcher import _durable_pages

    class Listing:
        def get_tournaments(self, game, format, limit, page):
            return [{"id": "newest"}] if page == 1 else []

    for _ in range(2):
        pages = _durable_pages(dynamodb_table, Listing(), "limitless", "M-C", "VGC", 50, 10)
        assert [page for page, _ in pages] == [1, 2]
    state = get_checkpoint(dynamodb_table, collection_state_key("limitless", "discover", "M-C"))
    assert state.status == "completed" and state.cursor is None
