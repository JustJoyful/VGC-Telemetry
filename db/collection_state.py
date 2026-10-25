"""Lease-fenced checkpoints and durable source-scoped event work."""

from datetime import datetime, timezone

from botocore.exceptions import ClientError

from core.models.collection import CollectionCheckpoint, EventWorkItem
from core.models.publication import Lease
from db.leases import lease_guard
from db.publication import transact
from db.schema_collection import event_work_key
from db.schema_v2 import native
from ingestion.contributions import query_rows


def get_checkpoint(table, key):
    row = table.get_item(Key=key, ConsistentRead=True).get("Item")
    if not row:
        return None
    return CollectionCheckpoint.model_validate(
        native({k: v for k, v in row.items() if k not in key})
    )


def advance_page(
    table,
    key,
    lease: Lease,
    *,
    expected_page: int,
    page: int,
    now: int,
    terminal=False,
    cursor=None,
):
    if lease.key["PK"] != key["PK"] or lease.key["SK"] != "LEASE" or page <= expected_page:
        raise ValueError("Invalid collection checkpoint transition")
    previous = get_checkpoint(table, key)
    checkpoint = CollectionCheckpoint(
        run_id=lease.token,
        page=page,
        cursor=page if cursor is None and not terminal else cursor,
        status="completed" if terminal else "running",
        last_successful_check=(
            datetime.fromtimestamp(now, timezone.utc)
            if terminal
            else previous.last_successful_check
            if previous
            else None
        ),
    )
    condition = (
        "attribute_not_exists(PK) OR #page = :expected"
        if expected_page == 0
        else "#page = :expected"
    )
    put = {
        "Item": {**key, **checkpoint.model_dump(mode="json", exclude_none=True)},
        "ConditionExpression": condition,
    }
    put["ExpressionAttributeNames"] = {"#page": "page"}
    put["ExpressionAttributeValues"] = {":expected": expected_page}
    transact(table, [lease_guard(lease, now), {"Put": put}])


def note_failure(table, key, lease: Lease, *, expected_page: int, now: int, error: str):
    if lease.key["PK"] != key["PK"]:
        raise ValueError("Lease belongs to another collection")
    transact(
        table,
        [
            lease_guard(lease, now),
            {
                "Update": {
                    "Key": key,
                    "UpdateExpression": (
                        "SET #page = if_not_exists(#page, :expected), run_id = :run, "
                        "#status = :failed, #error = :error"
                    ),
                    "ConditionExpression": "attribute_not_exists(PK) OR #page = :expected",
                    "ExpressionAttributeNames": {
                        "#page": "page",
                        "#status": "status",
                        "#error": "error",
                    },
                    "ExpressionAttributeValues": {
                        ":expected": expected_page,
                        ":run": lease.token,
                        ":failed": "failed",
                        ":error": error[:512],
                    },
                }
            },
        ],
    )


def restart_completed(table, key, lease: Lease, now: int):
    state = get_checkpoint(table, key)
    if not state or state.status != "completed":
        return state
    transact(
        table,
        [
            lease_guard(lease, now),
            {
                "Update": {
                    "Key": key,
                    "UpdateExpression": (
                        "SET #page = :zero, #status = :running, run_id = :run REMOVE #cursor"
                    ),
                    "ConditionExpression": "#page = :old AND #status = :completed",
                    # CURSOR is a DynamoDB reserved word; it must use a name placeholder.
                    "ExpressionAttributeNames": {
                        "#page": "page",
                        "#status": "status",
                        "#cursor": "cursor",
                    },
                    "ExpressionAttributeValues": {
                        ":zero": 0,
                        ":running": "running",
                        ":run": lease.token,
                        ":old": state.page,
                        ":completed": "completed",
                    },
                }
            },
        ],
    )
    return None


def put_event_work(table, item: EventWorkItem):
    key = event_work_key(item.source, item.event_id, item.kind)
    try:
        table.put_item(
            Item={**key, **item.model_dump(mode="json", exclude_none=True)},
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    return key


def request_backfill_work(table, source: str, event_id: str, order: int):
    if type(order) is not int or order < 0:
        raise ValueError("Backfill order must be nonnegative")
    key = put_event_work(table, EventWorkItem(source=source, event_id=event_id, kind="backfill"))
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET requested_order = :order, #status = :pending REMOVE staged_ref",
            ConditionExpression="attribute_not_exists(requested_order) OR requested_order < :order",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":order": order, ":pending": "pending"},
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    return key


def event_work_page(table, source: str, kind: str, *, cursor=None, limit=100):
    return query_rows(
        table,
        f"V2#COLLECTION_WORK#{source}",
        f"{kind.upper()}#",
        {"PK": f"V2#COLLECTION_WORK#{source}", "SK": cursor} if cursor else None,
        limit,
    )
