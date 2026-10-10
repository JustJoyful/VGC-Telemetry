"""Explicit native publication and recovery entry point."""

import random as randomness
import time
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from core.telemetry import RunTelemetry
from db.client import get_dynamodb_table
from db.leases import acquire_lease, release_lease
from db.publication import get, promote_revision
from db.schema_v2 import event_key, lease_key, population_key
from ingestion.contributions import build_contributions, query_rows, stage_revision
from ingestion.generations import build_generation, publish_generation
from ingestion.quarantine import quarantine_payload
from ingestion.recovery import conditional_failure, drain_intents
from ingestion.retry import BatchFailure, retry
from ingestion.revisions import InvalidRevisionInput, read_staged_result

DIRTY_CURSOR_KEY = {"PK": "V2#RECOVERY", "SK": "DIRTY"}


def dirty_page(table):
    progress = get(table, DIRTY_CURSOR_KEY) or {}
    return query_rows(table, "V2#DIRTY", cursor=progress.get("cursor"), limit=100)


def advance_dirty_cursor(table, page):
    table.put_item(Item={**DIRTY_CURSOR_KEY, "cursor": page.get("LastEvaluatedKey")})


def dirty_work_remaining(table) -> bool:
    # Deferred jobs can remain on earlier pages after the cursor has advanced.
    return bool(query_rows(table, "V2#DIRTY", limit=1)["Items"])


def handle_native_event(
    event: dict,
    context=None,
    *,
    table=None,
    s3_client=None,
    now: int | None = None,
    sleep=time.sleep,
    random=randomness.random,
    max_steps: int = 100,
) -> dict:
    telemetry = RunTelemetry("native", context=context)
    try:
        result = _handle_native_event(
            event,
            table=table,
            s3_client=s3_client,
            now=now,
            sleep=sleep,
            random=random,
            max_steps=max_steps,
            telemetry=telemetry,
        )
    except Exception as error:
        if not isinstance(error, BatchFailure):
            telemetry.failure(error)
        telemetry.finish("error")
        raise
    telemetry.finish(result["status"])
    return result


def _handle_native_event(event, *, table, s3_client, now, sleep, random, max_steps, telemetry):
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    table = table if table is not None else get_dynamodb_table()
    s3_client = s3_client if s3_client is not None else boto3.client("s3")
    owner = uuid4().hex

    def clock():
        return int(time.time()) if now is None else now

    def run(operation):
        return retry(operation, sleep=sleep, random=random, on_retry=telemetry.retry)

    outcomes = []
    for ref in event.get("normalized", []):
        held = None
        try:
            try:
                from db.catalog import CatalogStore

                value = run(
                    lambda: read_staged_result(
                        s3_client=s3_client,
                        bucket=ref["bucket"],
                        key=ref["key"],
                        version_id=ref.get("version_id"),
                        catalog_store=CatalogStore(
                            table=table, s3_client=s3_client, bucket=ref["bucket"]
                        ),
                    )
                )
            except InvalidRevisionInput as invalid:
                validation_message = str(invalid)
                args = {"Bucket": ref["bucket"], "Key": ref["key"]}
                if ref.get("version_id"):
                    args["VersionId"] = ref["version_id"]
                raw = run(lambda: s3_client.get_object(**args)["Body"].read())
                qk = run(
                    lambda: quarantine_payload(
                        s3_client=s3_client,
                        bucket_name=ref["bucket"],
                        source_key=ref["key"],
                        raw_content=raw,
                        error_message=validation_message,
                    )
                )
                outcomes.append({"status": "quarantined", "key": ref["key"], "quarantine_key": qk})
                telemetry.emit("Quarantines")
                telemetry.failure(invalid, event_ref=ref.get("key"), result="quarantined")
                continue
            from db.public_inputs import note_public_capture

            run(lambda: note_public_capture(value.order, table=table))
            manifest = run(
                lambda: stage_revision(
                    build_contributions(value),
                    table=table,
                    s3_client=s3_client,
                    bucket=ref["bucket"],
                )
            )
            key = event_key(manifest.event_id)
            held = run(
                lambda: acquire_lease(
                    table=table, key=lease_key(key), owner=owner, now=clock(), ttl=60
                )
            )
            current = get(table, key)
            promotion = run(
                lambda: promote_revision(
                    manifest,
                    table=table,
                    lease=held,
                    expected_revision=current["revision_id"] if current else None,
                    now=clock(),
                    clock=clock,
                    observation=value,
                )
            )
            if promotion.status == "promoted":
                telemetry.emit("AcceptedWork")
                telemetry.emit("NormalizedRevisions")
            release_lease(held, table=table)
            held = None
            if promotion.status in ("stale", "pending_order"):
                outcomes.append({"status": promotion.status, "event_id": manifest.event_id})
                telemetry.emit("RejectedWork" if promotion.status == "stale" else "PendingWork")
                continue
            # A bounded page plus durable cursor; repeated invocations continue recovery.
            recovery = run(
                lambda: drain_intents(
                    table=table, owner="native-recovery", now=clock(), limit=100, clock=clock
                )
            )
            page = dirty_page(table)
            dirty = page["Items"]
            populations = sorted(
                set(
                    manifest.populations
                    + recovery.populations
                    + [i["population_id"] for i in dirty]
                    + [i["population_id"] for i in promotion.intents]
                )
            )
            published = resume_generations(
                populations, table=table, owner=owner, clock=clock, run=run, max_steps=max_steps
            )
            advance_dirty_cursor(table, page)
            outcomes.append(
                {
                    "status": "published"
                    if published
                    and not page.get("LastEvaluatedKey")
                    and not recovery.cursor
                    and not recovery.deferred
                    and not dirty_work_remaining(table)
                    else "pending",
                    "event_id": manifest.event_id,
                    "revision_id": manifest.revision_id,
                }
            )
            if outcomes[-1]["status"] == "pending":
                telemetry.emit("PendingWork")
        except (ClientError, BotoCoreError) as failure:
            status = (
                "pending"
                if isinstance(failure, ClientError) and conditional_failure(failure)
                else "error"
            )
            outcomes.append({"status": status, "key": ref["key"], "error": str(failure)})
            telemetry.failure(failure, event_ref=ref.get("key"), result=status)
            if status == "pending":
                telemetry.emit("PendingWork")
        finally:
            if held:
                try:
                    release_lease(held, table=table)
                except ClientError as failure:
                    if not conditional_failure(failure):
                        raise
    if event.get("recover"):
        try:
            report = run(
                lambda: drain_intents(
                    table=table, owner="native-recovery", now=clock(), limit=100, clock=clock
                )
            )
            page = dirty_page(table)
            dirty = page["Items"]
            published = resume_generations(
                sorted({r["population_id"] for r in dirty}),
                table=table,
                owner=owner,
                clock=clock,
                run=run,
                max_steps=max_steps,
            )
            advance_dirty_cursor(table, page)
            public_published = resume_public(
                table=table,
                s3_client=s3_client,
                owner=owner,
                clock=clock,
                run=run,
                max_steps=max_steps,
                telemetry=telemetry,
            )
            outcomes.append(
                {
                    "status": "published"
                    if published
                    and not page.get("LastEvaluatedKey")
                    and not report.cursor
                    and public_published
                    and not report.deferred
                    and not dirty_work_remaining(table)
                    else "pending",
                    "acknowledged": report.acknowledged,
                    "public_status": "published" if public_published else "pending",
                }
            )
            if outcomes[-1]["status"] == "pending":
                telemetry.emit("PendingWork")
        except (ClientError, BotoCoreError) as error:
            telemetry.failure(error, result="error")
            outcomes.append(
                {
                    "status": "pending"
                    if isinstance(error, ClientError) and conditional_failure(error)
                    else "error",
                    "error": str(error),
                }
            )
    if any(r["status"] == "error" for r in outcomes):
        raise BatchFailure(outcomes)
    return {
        "status": "pending"
        if any(r["status"] in ("pending", "pending_order") for r in outcomes)
        else "completed",
        "results": outcomes,
    }


def resume_public(*, table, s3_client, owner, clock, run, max_steps, telemetry=None):
    from db.schema_public import public_lease_key, public_state_key
    from ingestion.public_snapshot import build_public_snapshot, publish_public_snapshot

    state = get(table, public_state_key())
    if telemetry:
        if not state or not state.get("dirty"):
            telemetry.emit("PublicationLag", 0)
        elif state.get("first_validated_at") is not None:
            telemetry.emit("PublicationLag", max(0, clock() - int(state["first_validated_at"])))
        else:
            telemetry.failure(
                ValueError("Validation time unknown"), result="pending_timestamp_unknown"
            )
    if not state or not state.get("dirty"):
        return True
    held = None
    try:
        held = run(
            lambda: acquire_lease(
                table=table, key=public_lease_key(), owner=owner, now=clock(), ttl=60
            )
        )
        result = run(
            lambda: build_public_snapshot(
                table=table, s3_client=s3_client, lease=held, clock=clock, max_steps=max_steps
            )
        )
        published = result.status == "complete" and run(
            lambda: publish_public_snapshot(result, table=table, lease=held, clock=clock)
        )
        if published and telemetry:
            telemetry.emit("Publications")
        return published
    except ClientError as error:
        if not conditional_failure(error):
            raise
        return False
    finally:
        if held:
            try:
                release_lease(held, table=table)
            except ClientError as error:
                if not conditional_failure(error):
                    raise


def resume_generations(populations, *, table, owner, clock, run, max_steps):
    published = True
    for pop in populations:
        held = None
        try:
            for _ in range(max_steps):
                result = run(
                    lambda: build_generation(
                        table=table,
                        population_id=pop,
                        owner=owner,
                        now=clock(),
                        checkpoint=None,
                        clock=clock,
                    )
                )
                if result.complete or (get(table, population_key(pop)) or {}).get("pending", 0):
                    break
            if not result.complete:
                published = False
                continue
            held = run(
                lambda: acquire_lease(
                    table=table,
                    key=lease_key(population_key(pop)),
                    owner=owner,
                    now=clock(),
                    ttl=60,
                )
            )
            state = get(table, population_key(pop))
            published &= run(
                lambda: publish_generation(
                    result,
                    table=table,
                    lease=held,
                    expected_publication=state.get("publication"),
                    now=clock(),
                    clock=clock,
                )
            )
        except ClientError as error:
            if not conditional_failure(error):
                raise
            published = False
        finally:
            if held:
                try:
                    release_lease(held, table=table)
                except ClientError as error:
                    if not conditional_failure(error):
                        raise
    return published
