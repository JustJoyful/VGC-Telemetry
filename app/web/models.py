"""Validated web filters and presentation contexts; API models remain authoritative."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from app.models.v2 import SourceId
from app.models.v2_queries import TeamQuery


class FeedQuery(TeamQuery):
    previous_event: SourceId | None = None


class ExplorerFilters(TeamQuery):
    """Supported selectors; each page also validates its existing API query model."""

    metric: Literal[
        "species", "move", "ability", "item", "nature", "stat_points", "registered_mega"
    ] = "species"
    group_by: Literal["species", "form"] = "form"
    move_id: str | None = Field(default=None, pattern=r"^[a-z0-9]+$", max_length=100)
    registered_mega_id: str | None = Field(default=None, pattern=r"^[a-z0-9]+$", max_length=100)
    previous_event: SourceId | None = None
    game_data_revision: str | None = None
    custom_rule: str | None = None


@dataclass
class OverviewContext:
    filters: ExplorerFilters
    data: dict = field(default_factory=dict)


@dataclass
class PokemonContext(OverviewContext):
    pokemon_id: str = ""


@dataclass
class TeamContext(OverviewContext):
    team_id: str = ""
