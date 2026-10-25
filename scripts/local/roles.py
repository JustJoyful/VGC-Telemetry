"""One process per role, durable schedule completion, lease-fenced local recovery."""

import json
import logging
import os
import signal
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

from botocore.exceptions import ClientError

from core.aws import aws_client, validate_local
from core.source_admission import load_admissions
from core.telemetry import RunTelemetry
from db.client import get_dynamodb_table
from db.leases import acquire_lease, lease_guard, release_lease
from db.publication import get, transact

SCHEDULES = {"discover": 21600, "recent": 21600, "reference": 86400, "reconcile": 604800}
WORKER_LEASE = {"PK": "V2#LOCAL#DEMO", "SK": "LEASE"}
# Seconds without a heartbeat before a role is unhealthy. A scheduler beats before each
# mode; one live mode may wait out upstream rate limits but stays within its 900s lease.
HEARTBEAT_LIMITS = {"worker": 180, "scheduler": 960}
logger = logging.getLogger(__name__)


class DirectIngestion:
    def invoke(self, *, Payload, **kwargs):
        from ingestion.native_handler import handle_native_event

        result = handle_native_event(json.loads(Payload))
        # The native handler durably registers input/recovery work before returning.
        if result["status"] not in {"completed", "pending"}:
            raise RuntimeError("Local native handoff failed")
        return {"StatusCode": 202}


def run_source(mode):
    from scraper.dispatcher import collect_sources, run_collection_worker
    from scraper.limitless_client import LimitlessAPIClient

    admissions = load_admissions(
        Path(os.environ.get("SOURCE_ADMISSION_PATH", "core/sources/admission.json"))
    )
    active = [a for a in admissions if a.enabled and a.verified]
    if not active:
        return {"status": "disabled", "reason": "source_admission_not_enabled"}
    table, s3 = get_dynamodb_table(), aws_client("s3")
    bucket = os.environ["S3_RAW_BUCKET_NAME"]
    telemetry = RunTelemetry("local-" + mode)
    if mode == "reference":
        import httpx

        from scraper.reference import check_references

        with httpx.Client() as client:
            return check_references(
                table=table, s3_client=s3, bucket=bucket, client=client, telemetry=telemetry
            )
    index = Path(os.environ.get("CATALOG_INDEX_PATH", "core/regulations/v2/index.json"))
    if mode == "discover":
        collect_sources(
            admissions=admissions,
            catalog_index=index,
            s3_client=s3,
            bucket_name=bucket,
            table=table,
            page_size=int(os.environ.get("COLLECTION_PAGE_SIZE", "50")),
            max_pages=int(os.environ.get("COLLECTION_MAX_PAGES", "10")),
            telemetry=telemetry,
        )
    reports = []
    for admission in active:
        client = LimitlessAPIClient(on_retry=telemetry.retry)
        try:
            report = run_collection_worker(
                admission=admission,
                catalog_index=index,
                s3_client=s3,
                bucket_name=bucket,
                client=client,
                table=table,
                invoke_client=DirectIngestion(),
                ingestion_function="local-direct",
                mode=mode,
                batch_size=int(os.environ.get("COLLECTION_BATCH_SIZE", "10")),
                recent_hours=int(os.environ.get("COLLECTION_INTERVAL_HOURS", "6")),
                telemetry=telemetry,
            )
            reports.append(report.model_dump(mode="json"))
        finally:
            client.client.close()
    return {
        "status": "pending"
        if any(r.get("continuation_required") for r in reports)
        else "completed",
        "reports": reports,
    }


def heartbeat(role):
    Path(f"/tmp/vgc-{role}-heartbeat").write_text(str(time.time()))


def scheduler_tick(table, *, run=run_source, now=None, beat=None):
    if os.environ.get("LOCAL_SCHEDULES_ENABLED", "false") != "true":
        return {"status": "disabled"}
    supplied_now = now

    def clock():
        return int(time.time()) if supplied_now is None else supplied_now

    now = clock()
    ran = 0
    for mode, interval in SCHEDULES.items():
        key = {"PK": "LOCAL#SCHEDULE", "SK": mode}
        if now < int((get(table, key) or {}).get("next_at", 0)):
            continue
        if beat:
            beat()
        lease = acquire_lease(
            table=table,
            key={"PK": "V2#LOCAL#SCHEDULE#" + mode, "SK": "LEASE"},
            owner=uuid4().hex,
            now=now,
            ttl=900,
        )
        try:
            if now < int((get(table, key) or {}).get("next_at", 0)):
                continue
            report = run(mode)
            if report.get("status") == "pending":
                # Continue bounded pages promptly rather than delaying until the next interval.
                next_at = now + 30
            else:
                next_at = now + interval
            transact(
                table,
                [
                    lease_guard(lease, clock()),
                    {"Put": {"Item": {**key, "next_at": next_at, "last_result": report["status"]}}},
                ],
            )
            ran += 1
        finally:
            release_lease(lease, table=table)
    return {"status": "completed", "ran": ran}


def serve(role):
    validate_local()
    stop = Event()

    def interrupt(*_):
        stop.set()
        if role == "scheduler":
            # Unwind now: finally blocks release schedule/collection leases, and durable
            # checkpoints resume the interrupted page. The worker ends at its call boundary.
            raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupt)
    table, s3 = get_dynamodb_table(), aws_client("s3")
    while not stop.is_set():
        heartbeat(role)
        try:
            if role == "scheduler":
                scheduler_tick(table, beat=lambda: heartbeat(role))
            else:
                lease = acquire_lease(
                    table=table, key=WORKER_LEASE, owner=uuid4().hex, now=int(time.time()), ttl=900
                )
                try:
                    from ingestion.native_handler import handle_native_event

                    handle_native_event({"recover": True}, table=table, s3_client=s3, max_steps=20)
                finally:
                    release_lease(lease, table=table)
            heartbeat(role)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                logger.exception("local_role_error %s", role)
            else:
                heartbeat(role)
        except Exception:
            logger.exception("local_role_error %s", role)
        stop.wait(30)
