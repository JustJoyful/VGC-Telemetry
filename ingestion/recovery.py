"""Bounded intent reconciliation against current committed event pointers."""

from botocore.exceptions import ClientError

from core.models.publication import ManifestPage, RecoveryReport
from db.leases import acquire_lease, elapsed_clock, lease_guard, release_lease
from db.publication import get, transact
from db.schema_v2 import dirty_key, event_key, lease_key, member_key, population_key
from ingestion.contributions import query_rows


def conditional_failure(error):
    code = error.response["Error"]["Code"]
    if code == "ConditionalCheckFailedException":
        return True
    if code == "TransactionCanceledException":
        reasons = error.response.get("CancellationReasons", [])
        return bool(reasons) and all(
            r.get("Code") in ("None", "ConditionalCheckFailed") for r in reasons
        )
    return False


def enumerate_manifest(
    *, table, population_id: str, cursor: dict | None, limit: int
) -> ManifestPage:
    if not 1 <= limit <= 100:
        raise ValueError("Manifest page size must be 1–100")
    state = get(table, population_key(population_id)) or {}
    page = query_rows(table, population_key(population_id)["PK"], "MEMBER#", cursor, limit)
    return ManifestPage(
        population_id=population_id,
        epoch=int(state.get("epoch", 0)),
        members=page["Items"],
        cursor=page.get("LastEvaluatedKey"),
    )


def reconcile_member(intent, *, table, lease, now, clock=None):
    clock = clock or elapsed_clock(now)
    pop = intent["population_id"]
    pointer_key = event_key(intent["event_id"])
    pointer = get(table, pointer_key)
    if not pointer:
        raise ValueError("Committed intent has no event pointer")
    key = member_key(pop, intent["event_id"])
    old = get(table, key)
    new = None
    if pop in pointer["populations"]:
        new = {
            **key,
            "event_id": intent["event_id"],
            "revision_id": pointer["revision_id"],
            "checksum": pointer["checksum"],
            "row_count": pointer["row_count"],
        }
    actions = [
        lease_guard(lease, now),
        {
            "ConditionCheck": {
                "Key": pointer_key,
                "ConditionExpression": "revision_id = :rid AND #order = :order",
                "ExpressionAttributeNames": {"#order": "order"},
                "ExpressionAttributeValues": {
                    ":rid": pointer["revision_id"],
                    ":order": pointer["order"],
                },
            }
        },
    ]
    if new != old:
        actions.append({"Put": {"Item": new}} if new else {"Delete": {"Key": key}})
        actions.append(
            {
                "Update": {
                    "Key": population_key(pop),
                    "UpdateExpression": "ADD epoch :one",
                    "ExpressionAttributeValues": {":one": 1},
                }
            }
        )
    actions.append({"Put": {"Item": {**dirty_key(pop), "population_id": pop}}})
    actions[0] = lease_guard(lease, clock())
    transact(table, actions)


def acknowledge_intent(intent, *, table, lease, now):
    transact(
        table,
        [
            lease_guard(lease, now),
            {
                "Update": {
                    "Key": {"PK": intent["PK"], "SK": intent["SK"]},
                    "UpdateExpression": "SET done = :done",
                    "ConditionExpression": "intent_order = :order AND done = :false",
                    "ExpressionAttributeValues": {
                        ":done": True,
                        ":false": False,
                        ":order": intent["intent_order"],
                    },
                }
            },
            {
                "Update": {
                    "Key": population_key(intent["population_id"]),
                    "UpdateExpression": "ADD pending :minus",
                    "ConditionExpression": "pending > :zero",
                    "ExpressionAttributeValues": {":minus": -1, ":zero": 0},
                }
            },
        ],
    )


def drain_intents(*, table, owner: str, now: int, limit: int, clock=None) -> RecoveryReport:
    if not 1 <= limit <= 100:
        raise ValueError("Recovery batch size must be 1–100")
    clock = clock or elapsed_clock(now)
    checkpoint_key = {"PK": "V2#RECOVERY", "SK": owner}
    checkpoint = get(table, checkpoint_key) or {}
    page = query_rows(table, "V2#WORK", "INTENT#", checkpoint.get("cursor"), limit)
    report = RecoveryReport(cursor=page.get("LastEvaluatedKey"))
    # Stable population order prevents conflicting multi-population recovery lock ordering.
    work = sorted((i for i in page["Items"] if not i.get("done")), key=lambda i: i["population_id"])
    for intent in work:
        held = None
        try:
            held = acquire_lease(
                table=table,
                key=lease_key(population_key(intent["population_id"])),
                owner=owner,
                now=clock(),
                ttl=60,
            )
            reconcile_member(intent, table=table, lease=held, now=clock(), clock=clock)
            acknowledge_intent(intent, table=table, lease=held, now=clock())
            report.acknowledged += 1
            report.populations.append(intent["population_id"])
        except ClientError as error:
            if not conditional_failure(error):
                raise
            report.deferred += 1
        finally:
            if held:
                try:
                    release_lease(held, table=table)
                except ClientError as error:
                    if not conditional_failure(error):
                        raise
    table.put_item(Item={**checkpoint_key, "cursor": report.cursor})
    report.populations = sorted(set(report.populations))
    return report
