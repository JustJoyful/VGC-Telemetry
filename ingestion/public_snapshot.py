"""Manual checkpointed immutable public builds. No scans, schedules, or cleanup."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from botocore.exceptions import ClientError

from app.models.v2 import Coverage
from core.catalog import load_catalog
from core.models.champions import SourceCapture
from core.models.public_read import PUBLIC_SCHEMA, PublicBuildResult, PublicCheckpoint, PublicInput
from core.models.publication import Lease
from core.top_cut import POLICY_VERSION
from db.leases import lease_guard, renew_lease
from db.public_inputs import observation_from_metadata, public_input, public_registration_actions
from db.publication import get, transact
from db.schema_public import (
    public_lease_key,
    public_member_key,
    public_state_key,
    snapshot_pk,
    snapshot_row_key,
)
from db.schema_v2 import digest, event_key, native, revision_pk
from ingestion.contributions import immutable_row, query_rows
from ingestion.public_contributions import derive_public_event
from ingestion.recovery import conditional_failure
from ingestion.revisions import _read, read_staged_result

BUILD_KEY = {"PK": "V2#PUBLIC", "SK": "BUILD"}


def _public_input(row):
    return PublicInput(**native({k: v for k, v in row.items() if k not in ("PK", "SK", "kind")}))


def _epoch_guard(epoch):
    return {
        "ConditionCheck": {
            "Key": public_state_key(),
            "ConditionExpression": "epoch = :epoch",
            "ExpressionAttributeValues": {":epoch": epoch},
        }
    }


def _save(table, cp, lease, clock):
    transact(
        table,
        [
            lease_guard(lease, clock()),
            _epoch_guard(cp.epoch),
            {"Put": {"Item": {**BUILD_KEY, "checkpoint": cp.model_dump(mode="json")}}},
        ],
    )


def _chain(checksum, row):
    # XOR of full row digests is order-independent and resumable with exact count.
    return f"{int(checksum or '0', 16) ^ int(digest(row), 16):064x}"


def _validated_input(member, *, table, s3_client):
    value = _public_input(member)
    pointer = get(table, event_key(value.event_id))
    if not pointer or (pointer["revision_id"], pointer["order"]) != (
        value.revision_id,
        value.order,
    ):
        raise ValueError("Public selection differs from committed event pointer")
    observation = read_staged_result(
        s3_client=s3_client,
        bucket=value.bucket,
        key=value.normalized_key,
        version_id=value.normalized_version_id,
    )
    if public_input(observation) != value:
        raise ValueError("Exact staged input differs from pinned directory metadata")
    raw, _ = _read(
        s3_client=s3_client,
        bucket=value.bucket,
        key=value.capture_key,
        version_id=value.capture_version_id,
        prefix="staging/v2/captures/",
    )
    return observation, SourceCapture.model_validate(raw)


def _write_output(table, cp, row):
    def dynamo_value(value):
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, dict):
            return {key: dynamo_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [dynamo_value(item) for item in value]
        return value

    stored = {
        **snapshot_row_key(cp.snapshot_id, row["section"], row["ordinal"]),
        "kind": row["kind"],
        "data": dynamo_value(row["data"]),
    }
    if row["kind"] == "population":
        stored["event_day"] = row["event_day"]
    immutable_row(table, stored)
    reference = {
        **snapshot_row_key(cp.snapshot_id, "ROWS", digest([row["section"], row["ordinal"]])),
        "target": {"PK": stored["PK"], "SK": stored["SK"]},
        "hash": digest(stored),
    }
    immutable_row(table, reference)
    cp.row_count += 1
    cp.checksum = _chain(cp.checksum, reference)


def build_public_snapshot(
    *, table, s3_client, lease: Lease, clock: Callable[[], int], max_steps: int = 100
) -> PublicBuildResult:
    if lease.key != public_lease_key() or max_steps < 1:
        raise ValueError("Expected public lease and positive step budget")
    transact(table, [lease_guard(lease, clock())])
    state = get(table, public_state_key())
    if state is None:
        transact(
            table,
            [
                lease_guard(lease, clock()),
                {
                    "Put": {
                        "Item": {**public_state_key(), "epoch": 0, "dirty": True},
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
            ],
        )
        state = get(table, public_state_key())
    policy = state.get("policy_version", POLICY_VERSION)
    if policy != POLICY_VERSION:
        raise ValueError("Unsupported cut policy version")
    saved = get(table, BUILD_KEY)
    cp = PublicCheckpoint(**native(saved["checkpoint"])) if saved else None
    if (
        cp is None
        or cp.epoch != state["epoch"]
        or cp.schema_version != PUBLIC_SCHEMA
        or cp.policy_version != policy
        or state.get("snapshot_id") not in (cp.expected_snapshot_id, cp.snapshot_id)
    ):
        cp = PublicCheckpoint(
            snapshot_id=uuid4().hex,
            epoch=int(state["epoch"]),
            expected_snapshot_id=state.get("snapshot_id"),
            policy_version=policy,
        )
        _save(table, cp, lease, clock)
    for _ in range(max_steps):
        lease = renew_lease(lease, table=table, now=clock())
        if cp.phase == "copy":
            page = query_rows(
                table, public_member_key("")["PK"], cursor=cp.directory_cursor, limit=100
            )
            for member in page["Items"]:
                value = _public_input(member)
                immutable_row(
                    table,
                    {
                        **snapshot_row_key(cp.snapshot_id, "INPUTS", member["SK"]),
                        "kind": "input",
                        **value.model_dump(mode="json"),
                    },
                )
                cp.input_count += 1
            cp.directory_cursor = page.get("LastEvaluatedKey")
            if cp.directory_cursor is None:
                cp.phase = "derive"
        elif cp.phase == "derive":
            page = query_rows(
                table, snapshot_pk(cp.snapshot_id, "INPUTS"), cursor=cp.event_cursor, limit=1
            )
            if not page["Items"]:
                cp.phase, cp.row_cursor = "validate", None
            else:
                member = page["Items"][0]
                observation, capture = _validated_input(member, table=table, s3_client=s3_client)
                catalog = load_catalog(
                    observation.catalog_revision, index_path=Path("core/regulations/v2/index.json")
                )
                bundle = derive_public_event(
                    observation, capture, catalog=catalog, policy_version=cp.policy_version
                )
                offset = int((cp.row_cursor or {}).get("offset", 0))
                for row in bundle.rows[offset : offset + 100]:
                    _write_output(table, cp, row)
                offset += min(100, len(bundle.rows) - offset)
                if offset == len(bundle.rows):
                    cp.event_cursor = {"PK": member["PK"], "SK": member["SK"]}
                    cp.row_cursor = None
                else:
                    cp.row_cursor = {"offset": offset}
        elif cp.phase == "validate":
            progress = cp.row_cursor or {
                "count": 0,
                "checksum": "",
                "cursor": None,
                "coverage": {},
                "events": 0,
                "latest_event_day": None,
            }
            page = query_rows(
                table, snapshot_pk(cp.snapshot_id, "ROWS"), cursor=progress["cursor"], limit=100
            )
            for ref in page["Items"]:
                target = get(table, ref["target"])
                if target is None or digest(target) != ref["hash"]:
                    raise ValueError("Public row/reference validation failed")
                progress["count"] += 1
                progress["checksum"] = _chain(progress["checksum"], ref)
                if target.get("kind") == "event":
                    incoming = target["data"]["coverage"]
                    combined = Coverage(**progress["coverage"]).model_dump()
                    for field, value in incoming.items():
                        if field == "advertised_entrants":
                            combined[field] = (
                                value
                                if not progress["events"]
                                else (
                                    None
                                    if combined[field] is None or value is None
                                    else combined[field] + value
                                )
                            )
                        elif isinstance(value, dict):
                            for name, count in value.items():
                                combined[field][name] = combined[field].get(name, 0) + int(count)
                        else:
                            combined[field] += int(value)
                    progress["coverage"], progress["events"] = combined, progress["events"] + 1
                    if target["data"].get("starts_at"):
                        day = (
                            datetime.fromisoformat(target["data"]["starts_at"])
                            .astimezone(UTC)
                            .date()
                            .isoformat()
                        )
                        progress["latest_event_day"] = max(progress["latest_event_day"] or day, day)
            progress["cursor"] = page.get("LastEvaluatedKey")
            cp.row_cursor = progress
            if not progress["cursor"]:
                if progress["count"] != cp.row_count or (progress["checksum"] or "0" * 64) != (
                    cp.checksum or "0" * 64
                ):
                    raise ValueError("Public count/checksum validation failed")
                cp.phase, cp.directory_cursor, cp.row_cursor = (
                    "validate_inputs",
                    None,
                    {
                        "count": 0,
                        "coverage": progress["coverage"],
                        "events": progress["events"],
                        "latest_event_day": progress["latest_event_day"],
                        "last_order": None,
                    },
                )
        elif cp.phase == "validate_inputs":
            page = query_rows(
                table, snapshot_pk(cp.snapshot_id, "INPUTS"), cursor=cp.directory_cursor, limit=100
            )
            cp.row_cursor["count"] += len(page["Items"])
            for member in page["Items"]:
                cp.row_cursor["last_order"] = max(
                    cp.row_cursor["last_order"] or 0, int(member["order"])
                )
            cp.directory_cursor = page.get("LastEvaluatedKey")
            if not cp.directory_cursor:
                if cp.row_cursor["count"] != cp.input_count:
                    raise ValueError("Input count validation failed")
                immutable_row(
                    table,
                    {
                        **snapshot_row_key(cp.snapshot_id, "META", "SUMMARY"),
                        "coverage": cp.row_cursor["coverage"],
                        "event_count": cp.row_cursor["events"],
                        "latest_event_day": cp.row_cursor["latest_event_day"],
                        "last_capture": datetime.fromtimestamp(
                            cp.row_cursor["last_order"] / 1_000_000, UTC
                        ).isoformat()
                        if cp.row_cursor["last_order"] is not None
                        else None,
                    },
                )
                cp.phase = "complete"
        elif cp.phase == "complete":
            marker = {
                **snapshot_row_key(cp.snapshot_id, "META", "COMPLETE"),
                "kind": "complete",
                "schema_version": cp.schema_version,
                "policy_version": cp.policy_version,
                "epoch": cp.epoch,
                "row_count": cp.row_count,
                "checksum": cp.checksum,
                "input_count": cp.input_count,
            }
            # Exact immutable marker, guarded before and after creation.
            transact(table, [lease_guard(lease, clock()), _epoch_guard(cp.epoch)])
            immutable_row(table, marker)
            _save(table, cp, lease, clock)
            return PublicBuildResult(status="complete", snapshot_id=cp.snapshot_id, checkpoint=cp)
        else:
            raise ValueError("Unsupported public checkpoint phase")
        _save(table, cp, lease, clock)
    return PublicBuildResult(status="pending", snapshot_id=cp.snapshot_id, checkpoint=cp)


def publish_public_snapshot(
    result: PublicBuildResult, *, table, lease: Lease, clock: Callable[[], int]
) -> bool:
    if lease.key != public_lease_key():
        raise ValueError("Expected public lease")
    transact(table, [lease_guard(lease, clock())])
    cp = result.checkpoint
    marker = get(table, snapshot_row_key(result.snapshot_id, "META", "COMPLETE"))
    if (
        result.status != "complete"
        or not marker
        or marker.get("schema_version") != PUBLIC_SCHEMA
        or marker.get("policy_version") != POLICY_VERSION
    ):
        raise ValueError("Snapshot is not supported/complete")
    if any(marker[k] != getattr(cp, k) for k in ("epoch", "row_count", "checksum", "input_count")):
        raise ValueError("Result differs from validated COMPLETE marker")
    state = get(table, public_state_key())
    if state["epoch"] != cp.epoch or state.get("snapshot_id") != cp.expected_snapshot_id:
        return False
    info_key = snapshot_row_key(result.snapshot_id, "META", "INFO")
    if get(table, info_key) is None:
        immutable_row(
            table, {**info_key, "generated_at": datetime.fromtimestamp(clock(), UTC).isoformat()}
        )
    conditions = "epoch = :epoch AND " + (
        "snapshot_id = :old" if cp.expected_snapshot_id else "attribute_not_exists(snapshot_id)"
    )
    values = {
        ":epoch": cp.epoch,
        ":id": result.snapshot_id,
        ":false": False,
        ":time": datetime.fromtimestamp(clock(), UTC).isoformat(),
    }
    if cp.expected_snapshot_id:
        values[":old"] = cp.expected_snapshot_id
    transact(
        table,
        [
            lease_guard(lease, clock()),
            {
                "Update": {
                    "Key": public_state_key(),
                    "UpdateExpression": (
                        "SET snapshot_id = :id, dirty = :false, published_at = :time"
                    ),
                    "ConditionExpression": conditions,
                    "ExpressionAttributeValues": values,
                }
            },
        ],
    )
    return True


def seed_public_inputs(event_ids: list[str], *, table, s3_client, max_steps: int = 100) -> dict:
    if max_steps < 1 or len(set(event_ids)) != len(event_ids):
        raise ValueError("Positive budget and distinct explicit IDs required")
    identity = digest(event_ids)
    key = {"PK": "V2#PUBLIC#IMPORT", "SK": identity}
    progress = get(table, key) or {"offset": 0, "gaps": []}
    for event_id in event_ids[int(progress["offset"]) : int(progress["offset"]) + max_steps]:
        pointer = get(table, event_key(event_id))
        try:
            if not pointer:
                raise ValueError("No committed pointer")
            obs = get(
                table,
                {
                    "PK": revision_pk(event_id, pointer["revision_id"]),
                    "SK": f"OBS#{pointer['order']}",
                },
            )
            if not obs:
                raise ValueError("No exact historical observation")
            value = observation_from_metadata(
                native(obs["input"]),
                event_id=event_id,
                rid=pointer["revision_id"],
                order=int(pointer["order"]),
            )
            _validated_input(value.model_dump(), table=table, s3_client=s3_client)
            actions = public_registration_actions(
                value,
                current_pointer=pointer,
                current_member=get(table, public_member_key(event_id)),
            )
            if actions:
                transact(
                    table,
                    [
                        {
                            "ConditionCheck": {
                                "Key": event_key(event_id),
                                "ConditionExpression": "revision_id = :id AND #order = :order",
                                "ExpressionAttributeNames": {"#order": "order"},
                                "ExpressionAttributeValues": {
                                    ":id": pointer["revision_id"],
                                    ":order": pointer["order"],
                                },
                            }
                        },
                        *actions,
                    ],
                )
        except (ValueError, KeyError) as error:
            immutable_row(
                table,
                {
                    "PK": "V2#PUBLIC#IMPORT#" + identity,
                    "SK": digest(event_id),
                    "event_id": event_id,
                    "reason": str(error)[:500],
                },
            )
            progress["gaps"] = [*progress["gaps"], event_id][-25:]
        progress["offset"] += 1
        table.put_item(Item={**key, **progress})
    return {
        "status": "pending"
        if progress["offset"] < len(event_ids)
        else ("incomplete" if progress["gaps"] else "complete"),
        "resume_id": identity,
        "processed": int(progress["offset"]),
        "total": len(event_ids),
        "gaps": progress["gaps"],
    }


def invalidate_public_inputs(*, table, policy_version: str) -> None:
    if policy_version != POLICY_VERSION:
        raise ValueError("Unsupported cut policy version")
    table.update_item(
        Key=public_state_key(),
        UpdateExpression="SET dirty = :true, policy_version = :version ADD epoch :one",
        ExpressionAttributeValues={":true": True, ":version": policy_version, ":one": 1},
    )


def rollback_public_snapshot(
    snapshot_id: str, *, table, lease: Lease, expected_snapshot_id: str, clock: Callable[[], int]
) -> bool:
    if lease.key != public_lease_key():
        raise ValueError("Expected public lease")
    marker = get(table, snapshot_row_key(snapshot_id, "META", "COMPLETE"))
    if (
        not marker
        or marker.get("schema_version") != PUBLIC_SCHEMA
        or marker.get("policy_version") != POLICY_VERSION
    ):
        raise ValueError("Rollback requires retained supported COMPLETE snapshot")
    state = get(table, public_state_key())
    try:
        transact(
            table,
            [
                lease_guard(lease, clock()),
                {
                    "Update": {
                        "Key": public_state_key(),
                        "UpdateExpression": (
                            "SET snapshot_id = :id, dirty = :dirty, "
                            "operator_rollback = :true, published_at = :time"
                        ),
                        "ConditionExpression": "snapshot_id = :old AND epoch = :epoch",
                        "ExpressionAttributeValues": {
                            ":id": snapshot_id,
                            ":old": expected_snapshot_id,
                            ":epoch": state["epoch"],
                            ":dirty": bool(state.get("dirty") or state["epoch"] != marker["epoch"]),
                            ":true": True,
                            ":time": datetime.fromtimestamp(clock(), UTC).isoformat(),
                        },
                    }
                },
            ],
        )
    except ClientError as error:
        if not conditional_failure(error):
            raise
        return False
    return True
