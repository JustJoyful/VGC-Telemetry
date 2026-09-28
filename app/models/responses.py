from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Health check response containing active regulation metadata."""

    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    regulation: str
    format: str = "Pokémon Champions VGC"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PaginationMetadata(BaseModel):
    """Cursor-based pagination envelope metadata."""

    model_config = ConfigDict(extra="forbid")

    limit: int
    next_cursor: str | None = None
    has_more: bool = False


class ErrorResponse(BaseModel):
    """Standardized error envelope."""

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str


class MetaStatResponse(BaseModel):
    """Generic aggregated stat item for meta queries."""

    model_config = ConfigDict(extra="forbid")

    regulation: str
    metric: str
    target: str
    data: dict[str, Any]
