"""Public, attributed supplemental builds; never tournament observations."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, model_validator

from core.models.champions import (
    CapturedDocument,
    ChampionsPokemon,
    EvidenceRef,
    Identifier,
    StrictModel,
)


class BuildSource(StrictModel):
    source: Identifier
    enabled: bool = False
    public: bool
    reviewed_by: Identifier
    access_document: CapturedDocument
    access_quote: Identifier
    access_captured_at: AwareDatetime

    def validate_access(self):
        document = CapturedDocument.model_validate(self.access_document.model_dump())
        if (
            not self.public
            or document.status != 200
            or urlsplit(document.url).scheme != "https"
            or self.access_quote not in document.body
        ):
            raise ValueError("Public build access lacks reviewed evidence")
        return self

    @model_validator(mode="after")
    def access(self):
        return self.validate_access()


class PublishedBuildPayload(StrictModel):
    schema_name: Literal["published-build-v1"] = Field(alias="schema")
    build_id: Identifier
    author: Identifier
    event_source: Identifier
    event_id: Identifier
    player_id: Identifier | None = None
    entrant_id: Identifier | None = None
    pokemon: list[ChampionsPokemon] = Field(min_length=1, max_length=6)
    supersedes: Identifier | None = None

    @model_validator(mode="after")
    def stable_identity(self):
        if not self.player_id and not self.entrant_id:
            raise ValueError("Build requires stable player or entrant ID")
        identities = [(p.species_id, p.form_id) for p in self.pokemon]
        if len(set(identities)) != len(identities):
            raise ValueError("Build roster has duplicate slots")
        return self


class BuildRecord(StrictModel):
    revision_id: Identifier
    source: Identifier
    build_id: Identifier
    author: Identifier
    event_source: Identifier
    event_id: Identifier
    player_id: Identifier | None = None
    entrant_id: Identifier | None = None
    pokemon: list[ChampionsPokemon] = Field(min_length=1, max_length=6)
    supersedes: Identifier | None = None
    evidence: EvidenceRef
    document: CapturedDocument
    admission: BuildSource


class BuildCrossReference(StrictModel):
    source: Identifier
    source_event_id: Identifier
    source_player_id: Identifier
    target_source: Identifier
    target_event_id: Identifier
    target_entry_id: Identifier
    reviewer: Identifier
    reason: Identifier
    evidence: list[EvidenceRef] = Field(min_length=1)


class BuildLink(StrictModel):
    revision_id: Identifier
    status: Literal["linked", "unlinked", "pending_conflict"]
    event_source: Identifier | None = None
    event_id: Identifier | None = None
    entry_id: Identifier | None = None
    event_revision: Identifier | None = None
    diagnostics: list[str] = Field(default_factory=list)
    cross_reference: BuildCrossReference | None = None
