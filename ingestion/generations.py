"""Checkpointed copy/reduce builds and guarded immutable generation publication."""

from decimal import Decimal
from uuid import uuid4

from botocore.exceptions import ClientError

from core.models.publication import BuildCheckpoint, BuildResult, Lease
from db.leases import acquire_lease, clock_now, elapsed_clock, lease_guard, release_lease
from db.publication import get, transact
from db.schema_v2 import (
    digest,
    dirty_key,
    event_key,
    generation_pk,
    lease_key,
    population_key,
    revision_pk,
)
from ingestion.contributions import immutable_row, query_rows
from ingestion.recovery import conditional_failure, enumerate_manifest

PAGE_SIZE = 40


def metric_percentage(count: int, denominator: int):
    return Decimal(count) * 100 / denominator if denominator else None


def build_key(pop):
    return {"PK": population_key(pop)["PK"], "SK": "BUILD"}


def save_checkpoint(cp, *, table, lease, now):
    transact(
        table,
        [
            lease_guard(lease, now),
            {
                "Put": {
                    "Item": {
                        **build_key(cp.population_id),
                        "checkpoint": cp.model_dump(mode="python"),
                    }
                }
            },
        ],
    )


def _emit(cp, row, *, table):
    if row is None:
        return
    row = dict(row)
    row["PK"] = generation_pk(cp.population_id, cp.generation_id)
    row["SK"] = "METRIC#" + digest([row["day"], row["metric"]])
    immutable_row(table, row)
    cp.row_count += 1
    cp.checksum = digest([cp.checksum, row])


def _copy(cp, *, table):
    if cp.member is None:
        page = enumerate_manifest(
            table=table, population_id=cp.population_id, cursor=cp.cursor, limit=1
        )
        if not page.members:
            cp.phase = "reduce"
            cp.cursor = None
            return
        cp.member = page.members[0]
        cp.cursor = page.cursor
    member = cp.member
    pointer = get(table, event_key(member["event_id"]))
    if (
        not pointer
        or pointer["revision_id"] != member["revision_id"]
        or cp.population_id not in pointer["populations"]
    ):
        cp.phase = "invalid"
        return
    pk = revision_pk(member["event_id"], member["revision_id"])
    prefix = f"ROW#METRIC#{cp.population_id}#"
    page = query_rows(table, pk, prefix, cp.row_cursor, PAGE_SIZE)
    target = generation_pk(cp.population_id, cp.generation_id)
    for source in page["Items"]:
        for day in (None, source["day"] or "unknown"):
            identity = digest([day, source["metric"]])
            row = {
                "PK": target,
                "SK": f"INPUT#{identity}#{digest(member['event_id'])}",
                "day": day,
                "metric": source["metric"],
                "scope": "observed_entries",
                "count": source["count"],
                "known": source["known"],
                "unknown": source["unknown"],
            }
            immutable_row(table, row)
    cp.row_cursor = page.get("LastEvaluatedKey")
    if not cp.row_cursor:
        cp.member = None
        if not cp.cursor:
            cp.phase = "reduce"


def _reduce(cp, *, table):
    page = query_rows(
        table, generation_pk(cp.population_id, cp.generation_id), "INPUT#", cp.cursor, PAGE_SIZE
    )
    carry = cp.carry
    for row in page["Items"]:
        identity = [row["day"], row["metric"]]
        if carry is not None and [carry["day"], carry["metric"]] != identity:
            _emit(cp, carry, table=table)
            carry = None
        if carry is None:
            carry = {k: row[k] for k in ("day", "metric", "scope", "count", "known", "unknown")}
        else:
            for field in ("count", "known", "unknown"):
                carry[field] += row[field]
    cp.cursor = page.get("LastEvaluatedKey")
    cp.carry = carry
    if not cp.cursor:
        _emit(cp, carry, table=table)
        cp.carry = None
        cp.phase = "validate"
        cp.cursor = None
        # Validation computes a second streaming count/checksum, independently of writes.
        cp.member = {"count": 0, "checksum": ""}


def _validate(cp, *, table):
    page = query_rows(
        table, generation_pk(cp.population_id, cp.generation_id), "METRIC#", cp.cursor, PAGE_SIZE
    )
    for row in page["Items"]:
        if any(row[field] < 0 for field in ("count", "known", "unknown")):
            raise ValueError("Negative generation metric")
        cp.member["count"] += 1
        cp.member["checksum"] = digest([cp.member["checksum"], row])
    cp.cursor = page.get("LastEvaluatedKey")
    if not cp.cursor:
        if cp.member["count"] != cp.row_count or cp.member["checksum"] != cp.checksum:
            raise ValueError("Generation read-back count/checksum mismatch")
        cp.member = None
        cp.phase = "complete"
        immutable_row(
            table,
            {
                "PK": generation_pk(cp.population_id, cp.generation_id),
                "SK": "COMPLETE",
                "epoch": cp.epoch,
                "row_count": cp.row_count,
                "checksum": cp.checksum,
            },
        )


def build_generation(
    *,
    table,
    population_id: str,
    owner: str,
    now: int,
    checkpoint: BuildCheckpoint | None,
    clock=None,
) -> BuildResult:
    clock = clock or elapsed_clock(now)
    held = acquire_lease(
        table=table, key=lease_key(population_key(population_id)), owner=owner, now=clock(), ttl=60
    )
    try:
        state = get(table, population_key(population_id)) or {"epoch": 0, "pending": 0}
        persisted = get(table, build_key(population_id))
        cp = BuildCheckpoint.model_validate(persisted["checkpoint"]) if persisted else None
        # An explicit checkpoint pins its population/generation; durable progress wins on replay.
        if checkpoint and (
            checkpoint.population_id != population_id
            or (cp and cp.generation_id != checkpoint.generation_id)
        ):
            raise ValueError("Checkpoint does not belong to the current build")
        if cp is None or cp.epoch != state["epoch"] or cp.phase == "invalid":
            cp = BuildCheckpoint(
                population_id=population_id, generation_id=uuid4().hex, epoch=int(state["epoch"])
            )
        if state.get("pending", 0):
            return BuildResult(
                population_id=population_id,
                generation_id=cp.generation_id,
                epoch=cp.epoch,
                complete=False,
                checkpoint=cp,
            )
        if cp.phase == "copy":
            _copy(cp, table=table)
        elif cp.phase == "reduce":
            _reduce(cp, table=table)
        elif cp.phase == "validate":
            _validate(cp, table=table)
        save_checkpoint(cp, table=table, lease=held, now=clock())
        return BuildResult(
            population_id=population_id,
            generation_id=cp.generation_id,
            epoch=cp.epoch,
            complete=cp.phase == "complete",
            checkpoint=cp,
            row_count=cp.row_count,
            checksum=cp.checksum,
        )
    finally:
        release_lease(held, table=table)


def publish_generation(
    result: BuildResult,
    *,
    table,
    lease: Lease,
    expected_publication: str | None,
    now: int | None = None,
    clock=None,
) -> bool:
    clock = clock or (clock_now if now is None else elapsed_clock(now))
    now = clock_now() if now is None else now
    if not result.complete or lease.key != lease_key(population_key(result.population_id)):
        return False
    complete_key = {
        "PK": generation_pk(result.population_id, result.generation_id),
        "SK": "COMPLETE",
    }
    completed = get(table, complete_key)
    if not completed or any(
        completed[f] != getattr(result, f) for f in ("epoch", "row_count", "checksum")
    ):
        return False
    condition = "epoch = :epoch AND pending = :zero AND "
    condition += (
        "publication = :expected" if expected_publication else "attribute_not_exists(publication)"
    )
    values = {":epoch": result.epoch, ":zero": 0, ":generation": result.generation_id}
    if expected_publication:
        values[":expected"] = expected_publication
    try:
        transact(
            table,
            [
                lease_guard(lease, clock()),
                {
                    "ConditionCheck": {
                        "Key": complete_key,
                        "ConditionExpression": "checksum = :checksum AND row_count = :count",
                        "ExpressionAttributeValues": {
                            ":checksum": result.checksum,
                            ":count": result.row_count,
                        },
                    }
                },
                {
                    "Update": {
                        "Key": population_key(result.population_id),
                        "UpdateExpression": "SET publication = :generation",
                        "ConditionExpression": condition,
                        "ExpressionAttributeValues": values,
                    }
                },
                {"Delete": {"Key": dirty_key(result.population_id)}},
            ],
        )
    except ClientError as error:
        if conditional_failure(error):
            return False
        raise
    return True


def read_generation(*, table, population_id: str, generation_id: str):
    pk = generation_pk(population_id, generation_id)
    if not get(table, {"PK": pk, "SK": "COMPLETE"}):
        raise ValueError("Generation is incomplete")
    rows, cursor = [], None
    while True:
        page = query_rows(table, pk, "METRIC#", cursor, PAGE_SIZE)
        rows.extend(page["Items"])
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return rows
