"""Strict internal public projection contracts; never returned as storage rows."""

from datetime import date
from typing import Any, Literal

from pydantic import Field

from core.models.champions import NonnegativeInt, StrictModel
from core.models.publication import RevisionInput
from core.models.top_cut import CutDecision
from db.schema_v2 import digest

PUBLIC_SCHEMA = "public-read-v1"


class PublicInput(StrictModel):
    event_id: str
    revision_id: str
    order: NonnegativeInt
    bucket: str
    normalized_key: str
    normalized_version_id: str | None = None
    capture_key: str
    capture_version_id: str | None = None
    capture_hash: str
    semantic_hash: str
    catalog_revision: str
    derivation_version: str = PUBLIC_SCHEMA


class PopulationKey(StrictModel):
    scope: Literal["top_cut"] = "top_cut"
    source: Literal["limitless"]
    game_id: Literal["pokemon-champions"]
    battle_format: Literal["doubles"]
    division_id: str | None = None
    competition_id: str | None = None
    rules_id: str
    catalog_revision: str
    game_data_revision: str
    match_formats: list[str] = Field(default_factory=list)
    custom_rules: list[str] = Field(default_factory=list)


def population_id(key: PopulationKey) -> str:
    value = key.model_dump(mode="json")
    value["match_formats"] = sorted(set(value["match_formats"]))
    value["custom_rules"] = sorted(set(value["custom_rules"]))
    return digest(value)


class DailyContribution(StrictModel):
    event_id: str
    entry_id: str
    population_id: str
    day: date | None = None
    placement: int | None = None
    metric: str
    identity: str
    value: str | None = None
    group_by: Literal["species", "form"] = "form"
    count: NonnegativeInt = 0
    denominator: NonnegativeInt = 0
    known_count: NonnegativeInt = 0
    unknown_count: NonnegativeInt = 0
    excluded_count: NonnegativeInt = 0
    wins: NonnegativeInt = 0
    losses: NonnegativeInt = 0
    ties: NonnegativeInt = 0
    double_losses: NonnegativeInt = 0
    mirrors: NonnegativeInt = 0
    byes: NonnegativeInt = 0
    unlinked: NonnegativeInt = 0


class PublicEventBundle(StrictModel):
    input: RevisionInput
    cut: CutDecision
    populations: dict[str, PopulationKey]
    rows: list[dict[str, Any]]


class PublicCheckpoint(StrictModel):
    snapshot_id: str
    epoch: NonnegativeInt
    expected_snapshot_id: str | None = None
    schema_version: str = PUBLIC_SCHEMA
    policy_version: str = "limitless-single-bracket-v1"
    phase: str = "copy"
    directory_cursor: dict[str, Any] | None = None
    event_cursor: dict[str, Any] | None = None
    row_cursor: dict[str, Any] | None = None
    row_count: NonnegativeInt = 0
    input_count: NonnegativeInt = 0
    checksum: str = ""


class PublicBuildResult(StrictModel):
    status: Literal["pending", "complete", "stale"]
    snapshot_id: str
    checkpoint: PublicCheckpoint
