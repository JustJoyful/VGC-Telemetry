"""Atomic revision pointer/intents, using separate schema-2 keys."""

from botocore.exceptions import ClientError

from core.models.publication import Lease, PromotionResult, RevisionManifest
from db.leases import clock_now, elapsed_clock, lease_guard
from db.schema_v2 import event_key, intent_key, lease_key, population_key, revision_pk
from ingestion.contributions import check_size, query_rows


def get(table, key):
    return table.get_item(Key=key, ConsistentRead=True).get("Item")


def transact(table, actions):
    table.meta.client.transact_write_items(
        TransactItems=[
            {op: {"TableName": table.name, **args}}
            for action in actions
            for op, args in action.items()
        ]
    )


def pending_intents(table, event_id, rid):
    prefix = intent_key(event_id, rid, "")["SK"]
    return [
        row
        for row in query_rows(table, "V2#WORK", prefix, limit=100)["Items"]
        if not row.get("done")
    ]


def promote_revision(
    manifest: RevisionManifest,
    *,
    table,
    lease: Lease,
    expected_revision: str | None,
    now: int | None = None,
    clock=None,
) -> PromotionResult:
    clock = clock or (clock_now if now is None else elapsed_clock(now))
    now = clock_now() if now is None else now
    key = event_key(manifest.event_id)
    if lease.key != lease_key(key):
        raise ValueError("Lease belongs to another event")
    pk = revision_pk(manifest.event_id, manifest.revision_id)
    stored = get(table, {"PK": pk, "SK": "MANIFEST"})
    if not manifest.ready or not stored or not stored.get("ready"):
        raise ValueError("Revision is not ready")
    for field in (
        "event_id",
        "revision_id",
        "populations",
        "row_count",
        "checksum",
        "bucket",
        "object_key",
    ):
        if stored[field] != getattr(manifest, field):
            raise ValueError("Manifest differs from validated ready revision")
    if not get(table, {"PK": pk, "SK": f"OBS#{manifest.order}"}):
        raise ValueError("Order lacks capture evidence")
    transact(table, [lease_guard(lease, clock())])
    current = get(table, key)
    if (current["revision_id"] if current else None) != expected_revision:
        raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PromoteRevision")
    duplicate = bool(current and current["revision_id"] == manifest.revision_id)
    if current and not duplicate and manifest.order <= current["order"]:
        return PromotionResult(
            status="stale" if manifest.order < current["order"] else "pending_order",
            revision_id=current["revision_id"],
        )
    populations = sorted(set(manifest.populations + (current["populations"] if current else [])))
    if len(populations) > 40:
        raise ValueError("Too many affected populations")
    pointer = {
        **key,
        **manifest.model_dump(),
        "order": max(manifest.order, current["order"] if current else 0),
    }
    check_size(pointer)
    put = {"Item": pointer, "ConditionExpression": "attribute_not_exists(PK)"}
    if current:
        put.update(
            ConditionExpression="revision_id = :old AND #order = :order",
            ExpressionAttributeNames={"#order": "order"},
            ExpressionAttributeValues={":old": expected_revision, ":order": current["order"]},
        )
    actions = [lease_guard(lease, clock()), {"Put": put}]
    if not duplicate:
        for pop in populations:
            actions.append(
                {
                    "Put": {
                        "Item": {
                            **intent_key(
                                manifest.event_id, manifest.revision_id, pop, manifest.order
                            ),
                            "event_id": manifest.event_id,
                            "revision_id": manifest.revision_id,
                            "population_id": pop,
                            "intent_order": manifest.order,
                            "done": False,
                        }
                    }
                }
            )
            actions.append(
                {
                    "Update": {
                        "Key": population_key(pop),
                        "UpdateExpression": "ADD epoch :one, pending :one",
                        "ExpressionAttributeValues": {":one": 1},
                    }
                }
            )
    actions[0] = lease_guard(lease, clock())
    transact(table, actions)
    return PromotionResult(
        status="duplicate" if duplicate else "promoted",
        revision_id=manifest.revision_id,
        intents=pending_intents(table, manifest.event_id, manifest.revision_id),
    )
