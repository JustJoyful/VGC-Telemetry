"""Bounded public DTOs. Unknown fields and storage metadata are rejected."""

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import Field

from core.models.champions import ItemSlot, StatPoints, StrictModel
from core.models.public_read import PopulationKey

SourceId = Annotated[str, Field(pattern=r"^limitless:[^\s:]+(?::[^\s:]+)*$")]


class PublicEvidence(StrictModel):
    url: str
    content_hash: str
    captured_at: datetime
    path: str | None = None


class EligibilityView(StrictModel):
    status: Literal["included", "excluded", "unknown"]
    reason: str
    evidence: list[PublicEvidence] = Field(default_factory=list)


class PokemonBuild(StrictModel):
    species_id: str
    form_id: str | None = None
    ability_id: str | None = None
    moves: list[str] | None = None
    moves_complete: bool | None = None
    item: ItemSlot = Field(default_factory=ItemSlot)
    nature_id: str | None = None
    stat_points: StatPoints | None = Field(
        default=None,
        description="Native six-stat allocations, 0–32 each, total ≤66. Unknown is null.",
    )
    mega_variant_ids: list[str] | None = Field(
        default=None, description="Registered capability; no activation evidence."
    )
    field_evidence: dict[str, list[PublicEvidence]] = Field(default_factory=dict)
    evidence_counts: dict[str, int] = Field(default_factory=dict)


class SnapshotInfo(StrictModel):
    snapshot_id: str
    expires_at: int = 0
    generated_at: datetime | None = None


class Coverage(StrictModel):
    advertised_entrants: int | None = None
    standings_entries: int = 0
    proven_cut_participants: int = 0
    mapped_participants: int = 0
    complete_rosters: int = 0
    partial_rosters: int = 0
    absent_rosters: int = 0
    invalid_rosters: int = 0
    eligible_complete_rosters: int = 0
    excluded_membership: int = 0
    unknown_membership: int = 0
    unknown_stat_points: int = 0
    known_attributes: dict[str, int] = Field(default_factory=dict)
    unknown_attributes: dict[str, int] = Field(default_factory=dict)


class PublicCut(StrictModel):
    policy_version: str
    status: str
    phase_id: int | None
    expected_participant_count: int | None
    participant_count: int
    evidence: list[PublicEvidence] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class EventSummary(StrictModel):
    event_id: SourceId
    name: str | None = None
    source: Literal["limitless"] = "limitless"
    starts_at: datetime | None = None
    completed_at: datetime | None = None
    division_id: str | None = None
    competition_id: str | None = None
    game_id: str = "pokemon-champions"
    battle_format: str = "doubles"
    rules_id: str
    catalog_revision: str
    game_data_revision: str
    match_formats: list[str] = Field(default_factory=list)
    custom_rules: list[str] = Field(default_factory=list)
    entrant_count: int | None = None
    cut: PublicCut
    coverage: Coverage
    populations: dict[str, PopulationKey] = Field(default_factory=dict)
    provenance: list[PublicEvidence] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)
    snapshot: str | None = None
    generated_at: datetime | None = None


class Performance(StrictModel):
    wins: int = 0
    losses: int = 0
    ties: int = 0
    double_losses: int = 0
    played: int = 0
    percentage: float | None = None
    mirrors: int = 0
    byes: int = 0
    excluded_outcomes: int = 0
    unlinked: int = 0
    association: str = "registered-team outcomes, not battle participation"


class TeamView(StrictModel):
    team_id: SourceId
    event_id: SourceId
    player_id: SourceId | None = None
    player_name: str | None = None
    standing: int | None = None
    publication_status: Literal["absent", "partial", "complete"]
    eligibility: EligibilityView
    pokemon: list[PokemonBuild] = Field(default_factory=list, max_length=6)
    population_id: str | None = None
    catalog_revision: str | None = None
    starts_at: datetime | None = None
    rules_id: str | None = None
    division_id: str | None = None
    competition_id: str | None = None
    match_formats: list[str] = Field(default_factory=list)
    provenance: list[PublicEvidence] = Field(default_factory=list)
    provenance_count: int = 0
    diagnostics: list[str] = Field(default_factory=list)
    evidence_link: str | None = None
    snapshot: str | None = None
    generated_at: datetime | None = None
    performance: Performance = Field(default_factory=Performance)


class MatchView(StrictModel):
    match_id: SourceId
    phase: int | None = None
    round: int | None = None
    bracket_label: str | None = None
    player1_id: SourceId | None = None
    player2_id: SourceId | None = None
    winner_id: SourceId | None = None
    outcome: str
    linkage_status: str
    provenance: list[PublicEvidence] = Field(default_factory=list)


class MetricView(StrictModel):
    metric: str
    identity: str
    value: str | None = None
    group_by: str = "form"
    count: int
    denominator: int = Field(
        description="Eligible complete rosters for usage; known slots for attributes."
    )
    percentage: float | None
    known_count: int
    unknown_count: int
    excluded_count: int
    population_id: str
    catalog_revision: str
    snapshot_id: str
    generated_at: datetime | None
    performance: Performance = Field(default_factory=Performance)
    provenance_links: list[str] = Field(default_factory=list)
    nature_alignment: dict[str, str | None] | None = None


class CoreView(MetricView):
    members: list[str]
    size: Literal[2, 3]


class RegulationView(StrictModel):
    catalog_revision: str
    rules_id: str
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    default: bool = False
    coverage: str
    authority: str = "Official registration pool; supplementary community mechanics"
    provenance: list[PublicEvidence] = Field(default_factory=list)


class PokemonView(StrictModel):
    pokemon_id: str
    species_id: str
    name: str
    catalog_revision: str
    types: list[str]
    base_stats: dict[str, int]
    ability_ids: list[str]
    battle_only: bool
    is_mega: bool
    registration_eligibility: Literal["confirmed", "unknown"]
    learnset_count: int
    learnset_link: str
    authority: str = "Supplementary pinned community reference, not official mechanics"
    provenance: list[PublicEvidence] = Field(default_factory=list)
    catalog_hash: str | None = None
    snapshot: str | None = None
    generated_at: datetime | None = None


class MoveView(StrictModel):
    move_id: str
    name: str
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    catalog_revision: str | None = None
    catalog_hash: str | None = None
    authority: str = "Supplementary pinned community reference, not official mechanics"


class StatusView(StrictModel):
    mode: Literal["manual"] = "manual"
    source: Literal["limitless"] = "limitless"
    collection_enabled: bool = False
    last_successful_check: datetime | None = None
    last_capture: datetime | None = None
    published_at: datetime | None = None
    snapshot_id: str | None = None
    dirty: bool = False
    input_epoch: int = 0
    stale: bool | None = None
    source_errors: list[str] | None = None
    catalog_coverage: list[str] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)


T = TypeVar("T")


class Page(StrictModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    snapshot: str
    generated_at: datetime | None = None
    coverage: Coverage = Field(default_factory=Coverage)


class ErrorDetail(StrictModel):
    code: str
    message: str
    request_id: str
    details: dict | None = None


class ErrorEnvelope(StrictModel):
    error: ErrorDetail
