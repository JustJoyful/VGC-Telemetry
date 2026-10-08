from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.public_queries import PublicReader, get_public_reader
from app.models.v2 import Page, SourceId, TeamView
from app.models.v2_queries import PageQuery
from app.v2.errors import V2Error

router = APIRouter(prefix="/v2/players", tags=["Native players"])


@router.get("/{player_id}", response_model=Page[TeamView])
def player(
    player_id: SourceId,
    reader: Annotated[PublicReader, Depends(get_public_reader)],
    query: Annotated[PageQuery, Query()],
):
    """Source-scoped history; UTC event date descending, unknown last; all context."""
    snap, position = reader.request_snapshot(query, "/v2/players/" + player_id)
    page = reader.page(
        snap, section="PLAYER#" + player_id, query=query, position=position, limit=query.limit
    )
    if not page.items and not query.cursor:
        raise V2Error(404, "not_found", "Player not found")
    return page
