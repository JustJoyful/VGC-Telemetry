"""Foreground live refresh through the existing collection, ingestion and recovery paths.

A refresh discovers bounded listing pages, captures the newest discovered events through
the explicit backfill worker, hands verified results to native ingestion and drains
recovery until the complete public snapshot is published. It never writes public rows
directly; a failed fetch or validation leaves the last complete snapshot in place.
"""

import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError

from core.source_admission import load_admissions
from core.telemetry import RunTelemetry
from db.collection_state import event_work_page
from db.leases import acquire_lease, release_lease
from db.publication import get
from db.schema_public import public_state_key
from ingestion.retry import BatchFailure
from scripts.local.roles import SCHEDULES, DirectIngestion

logger = logging.getLogger(__name__)

# Long enough for a bounded refresh that waits out upstream rate-limit windows.
REFRESH_LEASE_TTL = 3600
MESSAGES = {
    "completed": "Newest admitted events were collected and the public snapshot is current.",
    "pending": (
        "Newest admitted events were processed; remaining discovered work continues in the "
        "background while the stack runs."
    ),
    "no_eligible_events": (
        "The source responded, but no newly collected event passed admission and validation."
    ),
    "failed": ("Collection failed; this refresh left the last complete public snapshot unchanged."),
    "disabled": "Live collection is disabled: no enabled, verified source admission.",
}


def snapshot_id(table):
    return (get(table, public_state_key()) or {}).get("snapshot_id")


def newest_events(table, source, limit):
    """Newest discovered events by listed start date; undated listings stay for the scheduler."""
    rows, cursor = [], None
    while True:
        page = event_work_page(table, source, "capture", cursor=cursor, limit=100)
        rows.extend(page["Items"])
        if not page.get("LastEvaluatedKey"):
            break
        cursor = page["LastEvaluatedKey"]["SK"]
    dated = [r for r in rows if isinstance((r.get("listing") or {}).get("date"), str)]
    dated.sort(key=lambda r: (r["listing"]["date"], r["event_id"]), reverse=True)
    chosen = [r["event_id"] for r in dated[:limit]]
    remaining = sum(1 for r in rows if r.get("status") == "pending" and r["event_id"] not in chosen)
    return chosen, remaining


def _codes(diagnostics, limit=10):
    codes = {}
    for diagnostic in diagnostics:
        codes[diagnostic.code] = codes.get(diagnostic.code, 0) + 1
    return dict(sorted(codes.items(), key=lambda item: -item[1])[:limit])


def refresh(
    table,
    s3,
    bucket,
    *,
    admission_path: Path,
    catalog_index: Path,
    client_factory=None,
    newest: int | None = None,
    recover_rounds: int = 20,
    clock=time.time,
):
    from scraper.dispatcher import CollectionFailure, collect_sources, run_collection_worker

    if client_factory is None:
        from scraper.limitless_client import LimitlessAPIClient

        client_factory = LimitlessAPIClient
    newest = newest if newest is not None else int(os.environ.get("LIVE_REFRESH_EVENTS", "10"))
    if not 1 <= newest <= 100:
        raise ValueError("LIVE_REFRESH_EVENTS must be 1–100")
    try:
        admissions = load_admissions(admission_path)
    except (OSError, ValueError) as error:
        return _outcome("failed", reason="admission_invalid", error=type(error).__name__)
    active = [a for a in admissions if a.enabled]
    if not active:
        return _outcome("disabled", reason="source_admission_not_enabled")
    unverified = [a.source for a in active if not (a.verified and a.evidence and a.format_mappings)]
    if unverified:
        return _outcome("failed", reason="admission_unverified", sources=unverified)
    before = snapshot_id(table)
    telemetry = RunTelemetry("local-refresh")
    owner = uuid4().hex
    leases = []
    try:
        # Fence every scheduled mode: one collector at a time. Recovery needs no fence;
        # native ingestion and publication hold their own leases for concurrent callers.
        for key in [{"PK": "V2#LOCAL#SCHEDULE#" + mode, "SK": "LEASE"} for mode in SCHEDULES]:
            leases.append(
                acquire_lease(
                    table=table, key=key, owner=owner, now=int(clock()), ttl=REFRESH_LEASE_TTL
                )
            )
    except ClientError as error:
        _release(leases, table)
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _outcome("failed", reason="collection_in_progress", snapshot_id=before)
        raise
    try:
        sources = {}
        for admission in active:
            sources[admission.source] = _refresh_source(
                admission,
                table=table,
                s3=s3,
                bucket=bucket,
                catalog_index=catalog_index,
                client_factory=client_factory,
                newest=newest,
                clock=clock,
                telemetry=telemetry,
                collect_sources=collect_sources,
                run_collection_worker=run_collection_worker,
                failure_type=CollectionFailure,
            )
        collection_failed = any(s["failed"] for s in sources.values())
        # A failed refresh never moves the public pointer; independently validated work
        # it handed off is published by the worker's normal recovery cycle instead.
        published, publication_error = (
            (False, None) if collection_failed else _recover(table, s3, recover_rounds)
        )
    finally:
        _release(leases, table)
    after = snapshot_id(table)
    failed = collection_failed or publication_error
    accepted = sum(s["accepted"] for s in sources.values())
    if failed:
        status = "failed"
    elif accepted == 0:
        status = "no_eligible_events"
    elif not published or any(s["remaining"] or s["discovery_pending"] for s in sources.values()):
        status = "pending"
    else:
        status = "completed"
    result = _outcome(
        status,
        sources=sources,
        publication="published" if published else "deferred" if failed else "pending",
        snapshot_before=before,
        snapshot_id=after,
        snapshot_changed=before != after,
    )
    if publication_error:
        result["publication_error"] = publication_error
    telemetry.finish(status)
    return result


def _refresh_source(
    admission,
    *,
    table,
    s3,
    bucket,
    catalog_index,
    client_factory,
    newest,
    clock,
    telemetry,
    collect_sources,
    run_collection_worker,
    failure_type,
):
    stats = {
        "discovered": 0,
        "discovery_pending": False,
        "requested": 0,
        "captured": 0,
        "accepted": 0,
        "pending": 0,
        "rejected": 0,
        "failed": 0,
        "remaining": 0,
        "diagnostics": {},
    }
    diagnostics = []
    client = client_factory(on_retry=telemetry.retry)
    try:
        try:
            report = collect_sources(
                admissions=[admission],
                catalog_index=catalog_index,
                s3_client=s3,
                bucket_name=bucket,
                clients={admission.source: client},
                table=table,
                page_size=int(os.environ.get("COLLECTION_PAGE_SIZE", "50")),
                max_pages=int(os.environ.get("COLLECTION_MAX_PAGES", "10")),
                telemetry=telemetry,
            ).sources[admission.source]
            stats["discovered"] = report.discovered
            stats["discovery_pending"] = report.continuation_required
        except failure_type as failure:
            report = failure.report.sources[admission.source]
            stats["failed"] += report.failed
            stats["diagnostics"] = _codes(report.diagnostics)
            logger.warning("live_refresh_discovery_failed source=%s", admission.source)
            return stats
        ids, stats["remaining"] = newest_events(table, admission.source, newest)
        stats["requested"] = len(ids)
        if not ids:
            return stats
        order = int(clock() * 1000)
        # Bounded: one batch per ten requested events, plus the terminal page check.
        for _ in range(len(ids) // 10 + 2):
            try:
                batch = run_collection_worker(
                    admission=admission,
                    catalog_index=catalog_index,
                    s3_client=s3,
                    bucket_name=bucket,
                    client=client,
                    table=table,
                    invoke_client=DirectIngestion(),
                    ingestion_function="local-direct",
                    mode="backfill",
                    backfill_ids=ids,
                    backfill_order=order,
                    batch_size=10,
                    telemetry=telemetry,
                )
            except failure_type as failure:
                batch = failure.report.sources[admission.source]
                _add(stats, batch)
                diagnostics.extend(batch.diagnostics)
                logger.warning("live_refresh_capture_failed source=%s", admission.source)
                break
            _add(stats, batch)
            diagnostics.extend(batch.diagnostics)
            if not batch.continuation_required:
                break
    except (ClientError, BotoCoreError, RuntimeError, ValueError) as error:
        stats["failed"] += 1
        stats["diagnostics"] = {**_codes(diagnostics), "refresh_error": type(error).__name__}
        logger.warning("live_refresh_error source=%s error=%s", admission.source, type(error))
        return stats
    finally:
        client.client.close()
    stats["diagnostics"] = _codes(diagnostics)
    return stats


def _add(stats, batch):
    stats["captured"] += batch.captured
    stats["accepted"] += len(batch.staged_references)
    stats["pending"] += batch.pending
    stats["rejected"] += batch.rejected
    stats["failed"] += batch.failed


def _recover(table, s3, rounds):
    from ingestion.native_handler import handle_native_event

    for _ in range(rounds):
        try:
            result = handle_native_event({"recover": True}, table=table, s3_client=s3, max_steps=20)
        except BatchFailure:
            return False, "publication_failed"
        if result["status"] == "completed":
            return True, None
    return False, None


def _release(leases, table):
    for lease in leases:
        try:
            release_lease(lease, table=table)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise


def _outcome(status, **fields):
    return {"status": status, "message": MESSAGES[status], **fields}
