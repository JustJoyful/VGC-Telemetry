"""Versioned Champions contracts; legacy EV contracts remain in contracts.py."""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, pattern=r"\S")]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
Point = Annotated[int, Field(strict=True, ge=0, le=32)]
AdmissionStatus = Literal["verified", "pending", "rejected"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StatPoints(StrictModel):
    hp: Point
    atk: Point
    def_: Point = Field(alias="def")
    spa: Point
    spd: Point
    spe: Point

    @model_validator(mode="after")
    def budget(self) -> Self:
        if sum(self.model_dump().values()) > 66:
            raise ValueError("Total Stat Points cannot exceed 66")
        return self

    def canonical(self) -> str:
        return f"{self.hp}/{self.atk}/{self.def_}/{self.spa}/{self.spd}/{self.spe}"


class ItemSlot(StrictModel):
    state: Literal["unknown", "empty", "held"] = "unknown"
    item_id: Identifier | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.state == "held") != (self.item_id is not None):
            raise ValueError("Only a held item has an item_id")
        return self


class EvidenceRef(StrictModel):
    url: Identifier
    captured_at: AwareDatetime
    document_key: Identifier
    content_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Diagnostic(StrictModel):
    code: Identifier
    path: str
    message: Identifier
    evidence: list[EvidenceRef] = Field(default_factory=list)


class CapturedDocument(StrictModel):
    url: Identifier
    content_type: str
    body: str
    content_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    status: Annotated[int, Field(strict=True, ge=100, le=599)] = 200

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if hashlib.sha256(self.body.encode("utf-8")).hexdigest() != self.content_hash:
            raise ValueError("Document content hash does not match UTF-8 body")
        return self


class SourceCapture(StrictModel):
    source: Literal["limitless", "rk9"]
    source_event_id: Identifier
    fetched_at: AwareDatetime
    adapter_version: Identifier = "2.0.0"
    documents: dict[str, CapturedDocument] = Field(min_length=1)


class ChampionsPokemon(StrictModel):
    species_id: Identifier
    form_id: Identifier | None = None
    ability_id: Identifier | None = None
    moves: Annotated[list[Identifier], Field(min_length=1, max_length=4)] | None = None
    moves_complete: bool | None = None
    item: ItemSlot = Field(default_factory=ItemSlot)
    nature_id: Identifier | None = None
    stat_points: StatPoints | None = None
    mega_variant_ids: list[Identifier] | None = None
    field_evidence: dict[str, list[EvidenceRef]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_moves(self) -> Self:
        if self.moves is not None and len(self.moves) != len(set(self.moves)):
            raise ValueError("Duplicate moves")
        if self.moves is None and self.moves_complete is True:
            raise ValueError("Unpublished moves cannot be complete")
        return self


class TournamentRecord(StrictModel):
    wins: NonnegativeInt
    losses: NonnegativeInt
    ties: NonnegativeInt | None = None


class TeamRosterV2(StrictModel):
    entry_id: Identifier
    player_id: Identifier | None = None
    player_name: Identifier | None = None
    standing: PositiveInt | None = None
    record: TournamentRecord | None = None
    division_id: Identifier | None = None
    publication_status: Literal["absent", "partial", "complete"] = "absent"
    pokemon: list[ChampionsPokemon] = Field(default_factory=list, max_length=6)
    field_evidence: dict[str, list[EvidenceRef]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_roster(self) -> Self:
        if self.publication_status == "complete" and len(self.pokemon) != 6:
            raise ValueError("A complete roster has six identified slots")
        if self.publication_status == "absent" and self.pokemon:
            raise ValueError("An absent roster cannot contain Pokemon")
        return self


class MatchResultV2(StrictModel):
    match_id: Identifier
    phase: PositiveInt | None = None
    round: PositiveInt | None = None
    table: PositiveInt | None = None
    bracket_label: Identifier | None = None
    player1_id: Identifier | None = None
    player2_id: Identifier | None = None
    winner_id: Identifier | None = None
    outcome: Literal["win", "tie", "double_loss", "bye", "automatic_loss", "pending", "unknown"]
    score: Identifier | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_outcome(self) -> Self:
        if self.winner_id is not None and self.winner_id not in (self.player1_id, self.player2_id):
            raise ValueError("Winner is not a participant")
        if self.outcome in ("win", "bye") and self.winner_id is None:
            raise ValueError("A win or bye requires a sourced winner")
        if self.outcome in ("tie", "double_loss", "automatic_loss", "pending") and self.winner_id:
            raise ValueError("Outcome cannot have a winner")
        if self.outcome == "win" and (not self.player1_id or not self.player2_id):
            raise ValueError("A played win requires two players")
        participants = int(self.player1_id is not None) + int(self.player2_id is not None)
        if self.outcome in ("bye", "automatic_loss") and participants != 1:
            raise ValueError("Bye/automatic loss requires exactly one participant")
        if self.outcome in ("tie", "double_loss") and participants != 2:
            raise ValueError("Tie/double loss requires two participants")
        if self.player1_id and self.player1_id == self.player2_id:
            raise ValueError("A player cannot play themselves")
        return self


class TournamentMetadataV2(StrictModel):
    event_id: Identifier
    name: Identifier | None = None
    starts_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    game_id: Literal["pokemon-champions"] | None = None
    battle_format: Literal["doubles", "singles"] | None = None
    division_id: Identifier | None = None
    competition_id: Identifier | None = None
    match_formats: list[Identifier] = Field(default_factory=list)
    custom_rules: list[str] = Field(default_factory=list)
    rules_id: Identifier | None = None
    catalog_revision: Identifier | None = None
    game_data_revision: Identifier | None = None
    entrant_count: NonnegativeInt | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ChampionsTournament(StrictModel):
    schema_version: Literal[2] = 2
    metadata: TournamentMetadataV2
    teams: list[TeamRosterV2] = Field(default_factory=list)
    matches: list[MatchResultV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_entries(self) -> Self:
        for identities in (
            [team.entry_id for team in self.teams],
            [m.match_id for m in self.matches],
        ):
            if len(identities) != len(set(identities)):
                raise ValueError("Duplicate team or match identities")
        return self


class EventIdentification(StrictModel):
    status: AdmissionStatus
    metadata: TournamentMetadataV2 | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class NormalizationResult(StrictModel):
    admission_status: AdmissionStatus
    tournament: ChampionsTournament | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_identity(self) -> Self:
        if self.admission_status == "verified":
            if self.tournament is None:
                raise ValueError("Verified results require a tournament")
            meta = self.tournament.metadata
            if (
                meta.game_id != "pokemon-champions"
                or meta.battle_format != "doubles"
                or not meta.rules_id
                or not meta.catalog_revision
                or not meta.game_data_revision
            ):
                raise ValueError("Verified results require Champions doubles and versioned rules")
        return self
