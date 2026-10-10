"""Sourced, immutable reference snapshots; coverage and authority are explicit."""

from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from core.models.champions import CapturedDocument, EvidenceRef, Identifier, StrictModel


class ReferenceEntry(StrictModel):
    name: Identifier
    details: dict[str, Any] = Field(default_factory=dict)


class ItemReference(ReferenceEntry):
    mega_mappings: dict[Identifier, Identifier] = Field(default_factory=dict)
    mega_species_id: Identifier | None = None
    mega_variant_id: Identifier | None = None


class NatureReference(ReferenceEntry):
    plus: str | None = None
    minus: str | None = None


class SpeciesReference(ReferenceEntry):
    base_species_id: Identifier
    ability_ids: list[Identifier]
    move_ids: list[Identifier]
    types: list[str] = Field(default_factory=list)
    base_stats: dict[str, int] = Field(default_factory=dict)
    is_mega: bool = False
    battle_only: bool = False


class CatalogSnapshot(StrictModel):
    snapshot_id: Identifier
    rules_id: Identifier
    game_data_revision: Identifier
    effective_from: AwareDatetime | None
    effective_until: AwareDatetime | None = None
    verification_status: Literal["verified", "pending"]
    coverage_complete: bool
    source_kind: Literal["official", "community", "synthetic"]
    upstream_revision: Identifier
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=20)
    species: dict[str, SpeciesReference]
    items: dict[str, ItemReference]
    abilities: dict[str, ReferenceEntry]
    moves: dict[str, ReferenceEntry]
    natures: dict[str, NatureReference]
    aliases: dict[str, str] = Field(default_factory=dict)
    allocation_per_stat: int = 32
    allocation_total: int = 66
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_snapshot(self):
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("Invalid effective interval")
        if self.coverage_complete and not all(
            (self.species, self.items, self.moves, self.abilities, self.natures)
        ):
            raise ValueError("A complete catalog cannot have empty reference collections")
        return self


class CatalogChangeReview(StrictModel):
    reviewer: Identifier
    reason: Identifier
    evidence: list[EvidenceRef] = Field(min_length=1)
    quotations: dict[str, str] = Field(min_length=1)
    additions: dict[str, list[str]]
    removals: dict[str, list[str]]


class CatalogCandidate(StrictModel):
    model_config = {"extra": "forbid", "frozen": True}
    candidate_id: Identifier
    input_snapshot: dict
    previous_snapshot: CatalogSnapshot | None = None
    snapshot: CatalogSnapshot | None = None
    documents: dict[str, CapturedDocument]
    review: CatalogChangeReview | None = None
    status: Literal["verified", "pending"]
    validation_errors: list[str]
    registration_ids: list[str]
    historical_coverage: str = "M-B/M-C only; M-A and intermediate historical patches unverified"
    additions: dict[str, list[str]]
    removals: dict[str, list[str]]
    unchanged: bool = False
