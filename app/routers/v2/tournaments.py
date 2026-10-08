from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.public_queries import PublicReader, get_public_reader
from app.models.v2 import EventSummary, MatchView, Page, SourceId, TeamView
from app.models.v2_queries import MatchQuery, PageQuery, TournamentQuery

router = APIRouter(prefix="/v2/tournaments", tags=["Native tournaments"])
Reader = Annotated[PublicReader, Depends(get_public_reader)]


@router.get("", response_model=Page[EventSummary])
def tournaments(reader: Reader, query: Annotated[TournamentQuery, Query()]):
    """UTC dates descending, unknown last; at most 20 queries / 2,000 candidates."""
    snap, position = reader.request_snapshot(query, "/v2/tournaments")
    return reader.page(snap, section="EVENTS", query=query, position=position, limit=query.limit)


@router.get("/{event_id}", response_model=EventSummary)
def event(event_id: SourceId, reader: Reader, query: Annotated[PageQuery, Query()]):
    snap, _ = reader.request_snapshot(query, "/v2/tournaments/" + event_id)
    return reader.get_event(snap, event_id)


@router.get("/{event_id}/standings", response_model=Page[TeamView])
def standings(event_id: SourceId, reader: Reader, query: Annotated[PageQuery, Query()]):
    snap, position = reader.request_snapshot(query, f"/v2/tournaments/{event_id}/standings")
    reader.get_event(snap, event_id)
    return reader.page(
        snap,
        section=f"EVENT#{event_id}#STANDINGS",
        query=query,
        position=position,
        limit=query.limit,
    )


@router.get("/{event_id}/matches", response_model=Page[MatchView])
def matches(event_id: SourceId, reader: Reader, query: Annotated[MatchQuery, Query()]):
    snap, position = reader.request_snapshot(query, f"/v2/tournaments/{event_id}/matches")
    reader.get_event(snap, event_id)
    return reader.page(
        snap, section=f"EVENT#{event_id}#MATCHES", query=query, position=position, limit=query.limit
    )
