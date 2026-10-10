"""Bounded durable collection contracts."""

import json
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from core.models.champions import StrictModel


class CollectionCheckpoint(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=128)
    page: int = Field(default=0, ge=0)
    cursor: str | int | None = None
    status: Literal["running", "completed", "failed"] = "running"
    last_successful_check: AwareDatetime | None = None
    error: str | None = Field(default=None, max_length=512)


class EventWorkItem(StrictModel):
    schema_version: Literal[1] = 1
    source: Literal["limitless"]
    event_id: str = Field(min_length=1, max_length=256)
    kind: Literal["capture", "reconcile", "backfill"]
    listing: dict[str, Any] = Field(default_factory=dict)
    completed_at: AwareDatetime | None = None
    checked_at: AwareDatetime | None = None
    status: Literal["pending", "staged", "handed_off", "completed", "failed"] = "pending"
    retry_count: int = Field(default=0, ge=0)
    requested_order: int | None = None
    observed_order: int | None = None
    staged_ref: dict[str, str] | None = None

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.model_dump(mode="json"), default=str).encode()) > 16_000:
            raise ValueError("Collection work row exceeds 16 KB")
        return self


class CollectionRunReport(StrictModel):
    run_id: str
    source: Literal["limitless"]
    mode: Literal["discover", "recent", "reference", "reconcile", "backfill", "recover"]
    pages: int = 0
    discovered: int = 0
    captured: int = 0
    failed: int = 0
