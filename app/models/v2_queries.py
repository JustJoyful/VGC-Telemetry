"""Strict public filters and resolved canonical token inputs."""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.v2 import SourceId
from core.models.champions import StrictModel


class PageQuery(StrictModel):
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None
    snapshot: str | None = None


class NoQuery(StrictModel):
    pass


class ReferenceQuery(PageQuery):
    catalog_revision: str | None = None


class PopulationQuery(PageQuery):
    source: Literal["limitless"] | None = None
    regulation: str | None = None
    catalog_revision: str | None = None
    division_id: str | None = None
    competition_id: str | None = None
    match_mode: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Dates must be ordered inclusive UTC dates")
        return self


class TournamentQuery(PopulationQuery):
    cut_status: Literal["verified_complete", "verified_partial", "none", "unknown"] | None = None


class TeamQuery(PopulationQuery):
    tournament_id: SourceId | None = None
    player_id: SourceId | None = None
    species_id: str | None = None
    form_id: str | None = None
    mega_variant_id: str | None = None
    placement_min: int | None = Field(default=None, ge=1)
    placement_max: int | None = Field(default=None, ge=1)
    eligibility: Literal["included", "all"] = "included"

    @model_validator(mode="after")
    def compatible(self) -> Self:
        if self.form_id and not self.species_id:
            raise ValueError("form_id requires species_id")
        if self.placement_min and self.placement_max and self.placement_min > self.placement_max:
            raise ValueError("Placements must be ordered")
        return self


class MatchQuery(PageQuery):
    phase: int | None = Field(default=None, ge=1)
    outcome: (
        Literal["win", "tie", "double_loss", "bye", "automatic_loss", "pending", "unknown"] | None
    ) = None


class AnalyticsQuery(PopulationQuery):
    tournament_id: SourceId | None = None
    species_id: str | None = None
    form_id: str | None = None
    placement_min: int | None = Field(default=None, ge=1)
    placement_max: int | None = Field(default=None, ge=1)
    population_id: str | None = None
    game_data_revision: str | None = None
    custom_rule: str | None = None
    metric: Literal[
        "species", "move", "ability", "item", "nature", "stat_points", "registered_mega"
    ] = "species"
    group_by: Literal["species", "form"] = "form"

    @model_validator(mode="after")
    def window(self) -> Self:
        if self.form_id and not self.species_id:
            raise ValueError("form_id requires species_id")
        if self.placement_min and self.placement_max and self.placement_min > self.placement_max:
            raise ValueError("Placements must be ordered")
        if self.date_from and self.date_to and (self.date_to - self.date_from).days >= 90:
            raise ValueError("Maximum inclusive window is 90 days")
        return self


class CoreQuery(AnalyticsQuery):
    size: Literal[2, 3] = 2
    members: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("size", mode="before")
    @classmethod
    def http_size(cls, value):
        # Literal integers do not coerce HTTP query strings automatically.
        return int(value) if isinstance(value, str) and value in ("2", "3") else value

    @model_validator(mode="after")
    def distinct_members(self) -> Self:
        if self.metric != "species":
            raise ValueError("Core queries do not select attribute metrics")
        if len(set(self.members)) != len(self.members) or len(self.members) > self.size:
            raise ValueError("Core members must be distinct and fit requested size")
        self.members = sorted(self.members)
        return self


class PokemonQuery(PageQuery):
    catalog_revision: str | None = None
    species_id: str | None = None
    form_id: str | None = None
    legal_only: bool = False


def canonical_query(query: BaseModel) -> dict:
    return query.model_dump(mode="json", exclude={"cursor", "snapshot", "limit"})
