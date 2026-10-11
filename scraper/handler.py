"""EventBridge entry point for default-disabled collection modes."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from core.aws import aws_client
from core.source_admission import load_admissions
from core.telemetry import RunTelemetry
from db.client import get_dynamodb_table
from scraper.dispatcher import CollectionFailure, collect_sources, run_collection_worker
from scraper.limitless_client import LimitlessAPIClient

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    telemetry = RunTelemetry(
        "reference" if event.get("mode") == "reference" else "scraper",
        context=context,
        source="official" if event.get("mode") == "reference" else "limitless",
    )
    try:
        result = _run(event, telemetry)
    except Exception as error:
        if not isinstance(error, CollectionFailure):
            telemetry.failure(error)
        telemetry.finish("error")
        raise
    telemetry.finish(result["status"])
    return result


def _run(event: dict[str, Any], telemetry: RunTelemetry) -> dict[str, Any]:
    mode = event.get("mode", "discover")
    if mode not in ("discover", "recent", "reference", "reconcile", "backfill", "recover"):
        raise ValueError("Unsupported collector mode")
    ids = event.get("event_ids")
    if mode == "backfill" and (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 100
        or any(not isinstance(value, str) or not value for value in ids)
    ):
        raise ValueError("Backfill needs 1–100 explicit event IDs")
    backfill_order = event.get("backfill_order", 0)
    if mode == "backfill" and (type(backfill_order) is not int or backfill_order < 0):
        raise ValueError("Backfill order must be a nonnegative integer")
    if mode == "reference":
        admissions = load_admissions(
            Path(os.environ.get("SOURCE_ADMISSION_PATH", str(ROOT / "core/sources/admission.json")))
        )
        if not any(a.source == "limitless" and a.enabled and a.verified for a in admissions):
            return {"status": "disabled", "reason": "source_admission_not_enabled"}
        import httpx

        from scraper.reference import check_references

        with httpx.Client() as client:
            return check_references(
                table=get_dynamodb_table(),
                s3_client=aws_client("s3"),
                bucket=os.environ["S3_RAW_BUCKET_NAME"],
                client=client,
                telemetry=telemetry,
            )
    if mode == "recover":
        response = aws_client("lambda").invoke(
            FunctionName=os.environ["INGESTION_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=json.dumps({"mode": "native", "recover": True}).encode(),
        )
        if response["StatusCode"] != 202:
            raise RuntimeError("Recovery handoff was not accepted")
        return {"status": "pending", "reason": "recovery_enqueued"}
    admissions = load_admissions(
        Path(os.environ.get("SOURCE_ADMISSION_PATH", str(ROOT / "core/sources/admission.json")))
    )
    active = [admission for admission in admissions if admission.enabled]
    if not active:
        return {"status": "disabled", "reason": "source_admission_not_enabled"}
    table = get_dynamodb_table() if active else None
    s3 = aws_client("s3")
    catalog_index = Path(
        os.environ.get("CATALOG_INDEX_PATH", str(ROOT / "core/regulations/v2/index.json"))
    )
    bucket_name = os.environ.get("S3_RAW_BUCKET_NAME", "vgc-telemetry-raw-dev")
    report = (
        collect_sources(
            admissions=admissions,
            catalog_index=catalog_index,
            s3_client=s3,
            bucket_name=bucket_name,
            table=table if mode == "discover" else None,
            page_size=int(os.environ.get("COLLECTION_PAGE_SIZE", "50")),
            max_pages=int(os.environ.get("COLLECTION_MAX_PAGES", "10")),
            telemetry=telemetry,
        )
        if mode == "discover"
        else None
    )
    if active:
        lambda_client = aws_client("lambda")
        for admission in active:
            client = LimitlessAPIClient(on_retry=telemetry.retry)
            try:
                worker = run_collection_worker(
                    admission=admission,
                    catalog_index=catalog_index,
                    s3_client=s3,
                    bucket_name=bucket_name,
                    client=client,
                    table=table,
                    invoke_client=lambda_client,
                    ingestion_function=os.environ["INGESTION_FUNCTION_NAME"],
                    mode=mode,
                    backfill_ids=ids,
                    backfill_order=backfill_order,
                    batch_size=int(os.environ.get("COLLECTION_BATCH_SIZE", "10")),
                    recent_days=int(os.environ.get("RECENT_DAYS", "7")),
                    reconcile_days=int(os.environ.get("RECONCILE_DAYS", "90")),
                    recent_hours=int(os.environ.get("COLLECTION_INTERVAL_HOURS", "6")),
                    telemetry=telemetry,
                )
            finally:
                client.client.close()
            if report is None:
                from scraper.dispatcher import CollectionReport

                report = CollectionReport()
            previous = report.sources.get(admission.source)
            if previous:
                worker.discovered += previous.discovered
                worker.pending += previous.pending
                worker.continuation_required |= previous.continuation_required
            report.sources[admission.source] = worker
            if worker.continuation_required:
                payload = {"mode": mode}
                if mode == "backfill":
                    payload.update(event_ids=ids, backfill_order=backfill_order)
                continuation = lambda_client.invoke(
                    FunctionName=os.environ["SCRAPER_FUNCTION_NAME"],
                    InvocationType="Event",
                    Payload=json.dumps(payload).encode(),
                )
                if continuation["StatusCode"] != 202:
                    raise RuntimeError("Collection continuation was not accepted")
    has_pending = report and any(source.pending for source in report.sources.values())
    return {
        "status": "pending" if has_pending else "success",
        "report": report.model_dump(mode="json") if report else {},
    }
