from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.public_queries import PublicReader, get_public_reader
from app.models.v2 import Page, PublicEvidence, SourceId, TeamView
from app.models.v2_queries import PageQuery, TeamQuery

router = APIRouter(prefix="/v2/teams", tags=["Native teams"])
Reader = Annotated[PublicReader, Depends(get_public_reader)]


@router.get("", response_model=Page[TeamView])
def teams(reader: Reader, query: Annotated[TeamQuery, Query()]):
    """Event/entry ID order; unknown builds remain null. Default proven members only."""
    snap, position = reader.request_snapshot(query, "/v2/teams")
    section = "REG#" + query.regulation if query.regulation else "TEAMS"
    return reader.page(snap, section=section, query=query, position=position, limit=query.limit)


@router.get("/{team_id}", response_model=TeamView)
def team(team_id: SourceId, reader: Reader, query: Annotated[PageQuery, Query()]):
    snap, _ = reader.request_snapshot(query, "/v2/teams/" + team_id)
    return reader.get_team(snap, team_id)


@router.get("/{team_id}/evidence", response_model=Page[PublicEvidence])
def evidence(team_id: SourceId, reader: Reader, query: Annotated[PageQuery, Query()]):
    snap, position = reader.request_snapshot(query, f"/v2/teams/{team_id}/evidence")
    reader.get_team(snap, team_id)
    return reader.page(
        snap, section="EVIDENCE#TEAM#" + team_id, query=query, position=position, limit=query.limit
    )
