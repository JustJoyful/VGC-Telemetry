"""Measured local read status; no collector checks, schedules or scans."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.public_queries import PublicReader, get_public_reader
from app.models.v2 import Coverage, StatusView
from app.models.v2_queries import NoQuery
from db.catalog import PENDING_KEY, STATE_KEY
from db.schema_public import public_capture_key, public_state_key, snapshot_row_key

router = APIRouter(prefix="/v2", tags=["Manual data status"])


def data_status(*, reader: PublicReader, now: datetime) -> StatusView:
    state = reader.get(public_state_key()) or {}
    snapshot_id = state.get("snapshot_id")
    summary = reader.get(snapshot_row_key(snapshot_id, "META", "SUMMARY")) if snapshot_id else {}
    summary = summary or {}
    observed = reader.get(public_capture_key()) or {}
    known_times = [
        value for value in (summary.get("last_capture"), observed.get("captured_at")) if value
    ]
    last_capture = max(known_times, key=datetime.fromisoformat) if known_times else None
    captured = datetime.fromisoformat(last_capture) if last_capture else None
    catalog = reader.get(STATE_KEY) or {}
    pending = reader.get(PENDING_KEY) or {}
    check = reader.get({"PK": "V2#CATALOG", "SK": "CHECK"}) or {}
    checked = check.get("last_successful_check")
    runtime = reader.config.catalog_runtime
    return StatusView(
        snapshot_id=snapshot_id,
        dirty=state.get("dirty", False),
        input_epoch=state.get("epoch", 0),
        last_capture=captured,
        published_at=state.get("published_at"),
        stale=(now - captured).total_seconds() > 86400 if captured else None,
        coverage=Coverage(**summary.get("coverage", {})),
        catalog_active_revision=runtime.current.snapshot_id
        if runtime and runtime.current
        else catalog.get("active_revision"),
        catalog_pending_revision=pending.get("pending_revision"),
        catalog_verification_status=catalog.get("verification_status")
        or ("pending" if pending or check.get("pending") else None),
        catalog_last_successful_check=checked,
        catalog_checked_at=check.get("checked_at"),
        catalog_stale=True
        if runtime and runtime.stale
        else (now - datetime.fromisoformat(checked)).total_seconds() > 172800
        if checked
        else None,
        catalog_coverage=[
            "Pinned M-B/M-C; historical M-A/unknown patches unverified",
            "Supplementary community mechanics; no official game export verification",
        ],
    )


@router.get("/status", response_model=StatusView)
def status(
    reader: Annotated[PublicReader, Depends(get_public_reader)], query: Annotated[NoQuery, Query()]
):
    """Manual/disabled collection. Unmeasured successful checks and source errors remain null."""
    return data_status(reader=reader, now=datetime.fromtimestamp(reader.config.clock(), UTC))
