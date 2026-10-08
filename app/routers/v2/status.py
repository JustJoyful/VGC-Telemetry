"""Measured local read status; no collector checks, schedules or scans."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.public_queries import PublicReader, get_public_reader
from app.models.v2 import Coverage, StatusView
from app.models.v2_queries import NoQuery
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
    return StatusView(
        snapshot_id=snapshot_id,
        dirty=state.get("dirty", False),
        input_epoch=state.get("epoch", 0),
        last_capture=captured,
        published_at=state.get("published_at"),
        stale=(now - captured).total_seconds() > 86400 if captured else None,
        coverage=Coverage(**summary.get("coverage", {})),
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
