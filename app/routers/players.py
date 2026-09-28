from typing import Any

from fastapi import APIRouter, Query

from app.db.queries import query_player_history

router = APIRouter(prefix="/v1/players", tags=["Players"])


@router.get("/{player_id}")
def get_player_history(
    player_id: str,
    limit: int = Query(50, ge=1, le=100, description="Max historical entries to return"),
) -> dict[str, Any]:
    """Query a player's tournament history and team entries ordered chronologically."""
    items = query_player_history(player_id=player_id, limit=limit)
    if not items:
        # Return empty list rather than 404 for valid queried players with no entries yet,
        # or return the player_id with history
        return {"player_id": player_id, "history": []}

    player_name = items[0].get("player_name", player_id)
    return {
        "player_id": player_id,
        "player_name": player_name,
        "history": items,
    }
