"""Versioned source proof, independent of roster publication."""

from typing import Literal

from pydantic import Field

from core.models.champions import EvidenceRef, PositiveInt, StrictModel


class CutEvidence(EvidenceRef):
    path: str


class CutDecision(StrictModel):
    policy_version: str
    status: Literal["verified_complete", "verified_partial", "none", "unknown"]
    phase_id: PositiveInt | None = None
    participant_ids: list[str] = Field(default_factory=list)
    expected_participant_count: PositiveInt | None = None
    evidence: list[CutEvidence] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class EntryEligibility(StrictModel):
    status: Literal["included", "excluded", "unknown"]
    reason: str
    evidence: list[CutEvidence] = Field(default_factory=list)
