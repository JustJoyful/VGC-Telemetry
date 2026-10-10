"""Bounded source discovery, staging, and durable native handoff."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError
from pydantic import Field

from core.catalog import load_catalog
from core.models.champions import Diagnostic, NormalizationResult, StrictModel
from core.models.collection import EventWorkItem
from core.source_admission import SourceAdmission, classify_event
from db.catalog import CatalogStore
from db.collection_state import (
    advance_page,
    event_work_page,
    get_checkpoint,
    note_failure,
    put_event_work,
    request_backfill_work,
    restart_completed,
)
from db.leases import acquire_lease, release_lease
from db.schema_collection import collection_lease_key, collection_state_key
from db.schema_v2 import native
from ingestion.normalize import normalize_capture
from ingestion.staging import stage_capture, stage_normalized
from scraper.limitless_client import LimitlessAPIClient


class SourceReport(StrictModel):
    discovered: int = 0
    captured: int = 0
    pending: int = 0
    rejected: int = 0
    failed: int = 0
    continuation_required: bool = False
    staged_references: list[dict[str, str]] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class CollectionReport(StrictModel):
    sources: dict[str, SourceReport] = Field(default_factory=dict)


class CollectionFailure(RuntimeError):
    def __init__(self, report):
        self.report = report
        super().__init__("Configured source collection failed; independent staged work is retained")


def should_refetch(
    item: EventWorkItem,
    *,
    mode: str,
    now: datetime,
    recent_days: int = 7,
    reconcile_days: int = 90,
) -> bool:
    if mode == "recent":
        return item.completed_at is None or item.completed_at >= now - timedelta(days=recent_days)
    if mode == "reconcile":
        return item.completed_at is not None and item.completed_at >= now - timedelta(
            days=reconcile_days
        )
    return mode in ("discover", "backfill")


def collect_sources(
    *,
    admissions: list[SourceAdmission],
    catalog_index: Path,
    s3_client: Any,
    bucket_name: str,
    clients=None,
    table=None,
    catalog_store=None,
    page_size: int = 50,
    max_pages: int = 10,
    telemetry=None,
) -> CollectionReport:
    if not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
        raise ValueError("Invalid discovery bounds")
    if len({a.source for a in admissions}) != len(admissions):
        raise ValueError("Duplicate source configuration")
    for a in admissions:
        if a.enabled and (not a.verified or not a.evidence or not a.format_mappings):
            raise ValueError(f"Enabled source lacks verified admission configuration: {a.source}")
    report = CollectionReport()
    supplied = clients or {}
    for admission in admissions:
        source = admission.source
        stats = SourceReport()
        report.sources[source] = stats
        if not admission.enabled:
            continue
        client = supplied.get(source)
        owns_client = client is None
        if client is None:
            client = LimitlessAPIClient()
        try:
            try:
                if admission.event_ids:
                    ids = list(dict.fromkeys(admission.event_ids))
                    if table is not None:
                        for event_id in ids:
                            put_event_work(
                                table,
                                EventWorkItem(source=source, event_id=event_id, kind="capture"),
                            )
                elif source == "limitless":
                    ids = []
                    for format_id, mapping in admission.format_mappings.items():
                        if table is None:
                            pages = []
                            for page in range(1, max_pages + 1):
                                rows = client.get_tournaments(
                                    game=mapping.game, format=format_id, limit=page_size, page=page
                                )
                                pages.append((page, rows))
                                if not rows:
                                    break
                        else:
                            pages = _durable_pages(
                                table,
                                client,
                                source,
                                format_id,
                                mapping.game,
                                page_size,
                                max_pages,
                                telemetry=telemetry,
                            )
                        for _, rows in pages:
                            if telemetry and table is None:
                                telemetry.emit("DiscoveryPages")
                            for row in rows:
                                identifier = row["id"]
                                if identifier not in ids:
                                    ids.append(identifier)
                                    if telemetry and table is None:
                                        telemetry.emit("DiscoveredEvents")
                        if pages and pages[-1][1]:
                            stats.pending += 1
                            stats.continuation_required = table is not None
                stats.discovered = len(ids)
                if telemetry and admission.event_ids and ids:
                    telemetry.emit("DiscoveredEvents", len(ids))
                if table is not None:
                    continue
                if len(ids) > admission.max_events:
                    stats.diagnostics.append(
                        Diagnostic(
                            code="collection_limit",
                            path="events",
                            message=(
                                "Discovery exceeds bounded Phase 0 collection; "
                                "durable pagination is Phase 3"
                            ),
                        )
                    )
            except Exception as error:
                stats.failed += 1
                if telemetry:
                    telemetry.failure(error, result="discovery_failed")
                stats.diagnostics.append(
                    Diagnostic(
                        code="discovery_failed",
                        path=source,
                        message=str(error) or type(error).__name__,
                    )
                )
                continue
            for event_id in ids[: admission.max_events]:
                try:
                    capture = client.capture_tournament(event_id)
                    capture_key = stage_capture(
                        capture, s3_client=s3_client, bucket_name=bucket_name
                    )
                    stats.captured += 1
                    if telemetry:
                        telemetry.emit("Captures")
                    refs = {"capture_key": capture_key}
                    stats.staged_references.append(refs)
                    identification = classify_event(capture, admission)
                    if identification.status == "verified":
                        catalog = load_catalog(
                            identification.metadata.catalog_revision,
                            index_path=catalog_index,
                            store=catalog_store,
                        )
                        result = normalize_capture(capture, admission=admission, catalog=catalog)
                    else:
                        result = NormalizationResult(
                            admission_status=identification.status,
                            diagnostics=identification.diagnostics,
                        )
                    refs["normalized_key"] = stage_normalized(
                        result,
                        capture_key=capture_key,
                        s3_client=s3_client,
                        bucket_name=bucket_name,
                    )
                    if telemetry:
                        telemetry.emit("NormalizedRevisions")
                    if result.admission_status == "pending":
                        stats.pending += 1
                        if telemetry:
                            telemetry.emit("PendingWork")
                    elif result.admission_status == "rejected":
                        stats.rejected += 1
                        if telemetry:
                            telemetry.emit("RejectedWork")
                    elif telemetry:
                        telemetry.emit("AcceptedWork")
                    stats.diagnostics.extend(result.diagnostics)
                    # Actual HTTP refusals are visible operational failures,
                    # even though evidence was retained.
                    failures = [key for key, doc in capture.documents.items() if doc.status >= 400]
                    if failures:
                        raise RuntimeError(
                            f"HTTP refusal/failure in captured documents: {failures}"
                        )
                except Exception as error:
                    stats.failed += 1
                    if telemetry:
                        telemetry.failure(error, event_ref=event_id)
                    stats.diagnostics.append(
                        Diagnostic(
                            code="capture_failed",
                            path=event_id,
                            message=str(error) or type(error).__name__,
                        )
                    )
        finally:
            if owns_client:
                client.client.close()
    if any(s.failed for s in report.sources.values()):
        raise CollectionFailure(report)
    return report


def _durable_pages(table, client, source, format_id, game, page_size, max_pages, *, telemetry=None):
    key = collection_state_key(source, "discover", format_id)
    lease = acquire_lease(
        table=table,
        key=collection_lease_key(source, "discover", format_id),
        owner=uuid4().hex,
        now=int(time.time()),
        ttl=900,
    )
    pages = []
    seen = set()
    completed = 0
    try:
        previous = get_checkpoint(table, key) if telemetry else None
        state = restart_completed(table, key, lease, int(time.time()))
        completed = state.page if state else 0
        for _ in range(max_pages):
            page = completed + 1
            rows = client.get_tournaments(game=game, format=format_id, limit=page_size, page=page)
            if not isinstance(rows, list):
                raise ValueError("Discovery page must be a list")
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not row["id"].strip()
                ):
                    raise ValueError("Discovered event has no source ID")
                put_event_work(
                    table,
                    EventWorkItem(source=source, event_id=row["id"], kind="capture", listing=row),
                )
            advance_page(
                table,
                key,
                lease,
                expected_page=completed,
                page=page,
                now=int(time.time()),
                terminal=not rows,
            )
            if telemetry:
                telemetry.emit("DiscoveryPages")
                for row in rows:
                    if row["id"] not in seen:
                        seen.add(row["id"])
                        telemetry.emit("DiscoveredEvents")
            if not rows and telemetry:
                telemetry.emit(
                    "TournamentCheckAge",
                    max(0, int(time.time()) - int(previous.last_successful_check.timestamp()))
                    if previous and previous.last_successful_check
                    else 0,
                )
            pages.append((page, rows))
            completed = page
            if not rows:
                break
    except Exception as error:
        if telemetry:
            telemetry.failure(error, result="failed_checkpoint")
        try:
            note_failure(
                table,
                key,
                lease,
                expected_page=completed,
                now=int(time.time()),
                error=type(error).__name__,
            )
        except ClientError:
            pass
        raise
    finally:
        release_lease(lease, table=table)
    return pages


def run_collection_worker(
    *,
    admission: SourceAdmission,
    catalog_index: Path,
    s3_client,
    bucket_name: str,
    client,
    table,
    invoke_client,
    ingestion_function: str,
    mode: str,
    backfill_ids: list[str] | None = None,
    now: datetime | None = None,
    batch_size: int = 10,
    backfill_order: int = 0,
    recent_days: int = 7,
    reconcile_days: int = 90,
    recent_hours: int = 6,
    telemetry=None,
) -> SourceReport:
    if not admission.enabled or not admission.verified or not admission.evidence:
        raise ValueError("Source admission is not verified and enabled")
    if mode not in ("discover", "recent", "reconcile", "backfill") or not 1 <= batch_size <= 100:
        raise ValueError("Invalid worker mode or batch bound")
    if not 1 <= recent_days <= 7 or not 1 <= reconcile_days <= 90:
        raise ValueError("Routine windows exceed explicit backfill policy")
    if not 2 <= recent_hours <= 24:
        raise ValueError("Recent cadence must be 2–24 hours")
    if mode == "backfill":
        if (
            not backfill_ids
            or len(backfill_ids) > 100
            or any(not isinstance(v, str) or not v for v in backfill_ids)
        ):
            raise ValueError("Backfill requires 1–100 explicit event IDs")
        for event_id in dict.fromkeys(backfill_ids):
            request_backfill_work(table, admission.source, event_id, backfill_order)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Worker time must be timezone aware")
    kind = "backfill" if mode == "backfill" else "capture"
    scope = f"work#{backfill_order}" if mode == "backfill" else "work"
    key = collection_state_key(admission.source, mode, scope)
    lease = acquire_lease(
        table=table,
        key=collection_lease_key(admission.source, mode, scope),
        owner=uuid4().hex,
        now=int(now.timestamp()),
        ttl=900,
    )
    stats = SourceReport()
    completed = 0
    try:
        previous = get_checkpoint(table, key) if telemetry else None
        state = restart_completed(table, key, lease, int(now.timestamp()))
        completed = state.page if state else 0
        page = event_work_page(
            table,
            admission.source,
            kind,
            cursor=state.cursor if state else None,
            limit=batch_size,
        )
        for row in page["Items"]:
            item = EventWorkItem.model_validate(
                native({k: v for k, v in row.items() if k not in ("PK", "SK")})
            )
            if item.status != "staged" and not should_refetch(
                item,
                mode=mode,
                now=now,
                recent_days=recent_days,
                reconcile_days=reconcile_days,
            ):
                continue
            if mode in ("discover", "backfill") and item.status in ("handed_off", "completed"):
                continue
            if (
                mode == "recent"
                and item.status != "staged"
                and item.checked_at
                and item.checked_at > now - timedelta(hours=recent_hours)
            ):
                continue
            try:
                _process_event(
                    item,
                    admission=admission,
                    client=client,
                    catalog_index=catalog_index,
                    s3_client=s3_client,
                    bucket_name=bucket_name,
                    table=table,
                    invoke_client=invoke_client,
                    ingestion_function=ingestion_function,
                    now=now,
                    stats=stats,
                    telemetry=telemetry,
                )
            except Exception as error:
                stats.failed += 1
                if telemetry:
                    telemetry.failure(error, event_ref=item.event_id)
                stats.diagnostics.append(
                    Diagnostic(
                        code="capture_failed",
                        path=item.event_id,
                        message=type(error).__name__,
                    )
                )
        if stats.failed:
            raise CollectionFailure(CollectionReport(sources={admission.source: stats}))
        advance_page(
            table,
            key,
            lease,
            expected_page=state.page if state else 0,
            page=(state.page if state else 0) + 1,
            now=int(now.timestamp()),
            terminal=not page.get("LastEvaluatedKey"),
            cursor=page["LastEvaluatedKey"]["SK"] if page.get("LastEvaluatedKey") else None,
        )
        stats.continuation_required = bool(page.get("LastEvaluatedKey"))
        if telemetry and not page.get("LastEvaluatedKey") and mode != "reconcile":
            telemetry.emit(
                "TournamentCheckAge",
                max(0, int(now.timestamp()) - int(previous.last_successful_check.timestamp()))
                if previous and previous.last_successful_check
                else 0,
            )
    except Exception as error:
        if telemetry and not isinstance(error, CollectionFailure):
            telemetry.failure(error, result="failed_checkpoint")
        try:
            note_failure(
                table,
                key,
                lease,
                expected_page=completed,
                now=int(now.timestamp()),
                error=type(error).__name__,
            )
        except ClientError:
            pass
        raise
    finally:
        release_lease(lease, table=table)
    return stats


def _process_event(
    item,
    *,
    admission,
    client,
    catalog_index,
    s3_client,
    bucket_name,
    table,
    invoke_client,
    ingestion_function,
    now,
    stats,
    telemetry=None,
):
    from db.schema_collection import event_work_key

    key = event_work_key(item.source, item.event_id, item.kind)
    request_guard = " AND requested_order = :requested" if item.kind == "backfill" else ""
    request_values = {":requested": item.requested_order} if item.kind == "backfill" else {}
    if item.status != "staged":
        capture = client.capture_tournament(item.event_id)
        capture_key = stage_capture(capture, s3_client=s3_client, bucket_name=bucket_name)
        stats.captured += 1
        if telemetry:
            telemetry.emit("Captures")
        capture_version = s3_client.head_object(Bucket=bucket_name, Key=capture_key).get(
            "VersionId"
        )
        identification = classify_event(capture, admission)
        if identification.status == "verified":
            catalog = load_catalog(
                identification.metadata.catalog_revision,
                index_path=catalog_index,
                store=CatalogStore(table=table, s3_client=s3_client, bucket=bucket_name),
            )
            result = normalize_capture(capture, admission=admission, catalog=catalog)
        else:
            result = NormalizationResult(
                admission_status=identification.status,
                diagnostics=identification.diagnostics,
            )
        normalized_key = stage_normalized(
            result,
            capture_key=capture_key,
            capture_version_id=capture_version,
            s3_client=s3_client,
            bucket_name=bucket_name,
        )
        if telemetry:
            telemetry.emit("NormalizedRevisions")
        normalized_version = s3_client.head_object(Bucket=bucket_name, Key=normalized_key).get(
            "VersionId"
        )
        ref = {"bucket": bucket_name, "key": normalized_key}
        if normalized_version:
            ref["version_id"] = normalized_version
        order = int(capture.fetched_at.timestamp() * 1_000_000)
        table.update_item(
            Key=key,
            UpdateExpression=(
                "SET #status = :staged, staged_ref = :ref, "
                "observed_order = :order, checked_at = :checked"
            ),
            ConditionExpression=(
                "(attribute_not_exists(observed_order) OR observed_order < :order "
                "OR (observed_order = :order AND staged_ref = :ref))"
            )
            + request_guard,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":staged": "staged",
                ":ref": ref,
                ":order": order,
                ":checked": now.isoformat(),
                **request_values,
            },
        )
        if any(doc.status >= 400 for doc in capture.documents.values()):
            table.update_item(
                Key=key,
                UpdateExpression="SET #status = :failed",
                ConditionExpression="staged_ref = :ref" + request_guard,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":failed": "failed", ":ref": ref, **request_values},
            )
            raise RuntimeError("Source document request failed")
        if result.admission_status != "verified":
            table.update_item(
                Key=key,
                UpdateExpression="SET #status = :done",
                ConditionExpression="staged_ref = :ref" + request_guard,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":done": "completed", ":ref": ref, **request_values},
            )
            stats.pending += result.admission_status == "pending"
            stats.rejected += result.admission_status == "rejected"
            if telemetry:
                telemetry.emit(
                    "PendingWork" if result.admission_status == "pending" else "RejectedWork"
                )
            return
    else:
        ref = item.staged_ref
    payload = json.dumps({"mode": "native", "normalized": [ref]}).encode()
    response = invoke_client.invoke(
        FunctionName=ingestion_function,
        InvocationType="Event",
        Payload=payload,
    )
    if response["StatusCode"] != 202:
        raise RuntimeError("Native ingestion handoff was not accepted")
    table.update_item(
        Key=key,
        UpdateExpression="SET #status = :done",
        ConditionExpression="staged_ref = :ref" + request_guard,
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":done": "handed_off", ":ref": ref, **request_values},
    )
    stats.staged_references.append(ref)
    if telemetry:
        telemetry.emit("AcceptedWork")
