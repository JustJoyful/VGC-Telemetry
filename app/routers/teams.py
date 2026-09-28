import base64
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db.queries import query_teams_by_tournament
from app.models.responses import PaginationMetadata

router = APIRouter(prefix="/v1/teams", tags=["Teams"])


@router.get("")
def get_teams(
    tournament_id: str = Query(..., description="Unique tournament identifier"),
    limit: int = Query(20, ge=1, le=100, description="Page limit (1-100)"),
    cursor: str | None = Query(None, description="Base64 encoded cursor for pagination"),
) -> dict[str, Any]:
    """Query team rosters for a specific tournament with cursor pagination."""
    exclusive_start_key: dict[str, Any] | None = None
    if cursor:
        try:
            raw_bytes = base64.urlsafe_b64decode(cursor.encode("utf-8"))
            parsed = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("Cursor payload must be a JSON object")
            exclusive_start_key = parsed
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid pagination cursor: {exc}",
            ) from exc

    items, last_key = query_teams_by_tournament(
        tid=tournament_id,
        limit=limit,
        exclusive_start_key=exclusive_start_key,
    )

    next_cursor: str | None = None
    if last_key:
        encoded = base64.urlsafe_b64encode(json.dumps(last_key).encode("utf-8")).decode("utf-8")
        next_cursor = encoded

    pagination = PaginationMetadata(
        limit=limit,
        next_cursor=next_cursor,
        has_more=bool(next_cursor),
    )

    return {
        "tournament_id": tournament_id,
        "data": items,
        "pagination": pagination.model_dump(),
    }
