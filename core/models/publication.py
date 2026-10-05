"""Internal schema-2 publication contracts, independent of public API DTOs."""

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StrictStr

from core.models.champions import NormalizationResult, StrictModel


class StagedEnvelope(StrictModel):
    schema_version: Literal[2]
    capture_key: Annotated[StrictStr, Field(min_length=1)]
    capture_version_id: Annotated[StrictStr, Field(min_length=1)] | None = None
    semantic_hash: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")] | None
    result: NormalizationResult


class RevisionInput(StrictModel):
    result: NormalizationResult
    bucket: str
    normalized_key: str
    normalized_version_id: str | None
    capture_key: str
    capture_version_id: str | None
    capture_hash: str
    semantic_hash: str
    source: str
    event_id: str
    adapter_version: str
    schema_version: int = 2
    catalog_revision: str
    captured_at: AwareDatetime
    order: int
    source_order: str | None = None


class ContributionBundle(StrictModel):
    input: RevisionInput
    revision_id: str
    rows: list[dict[str, Any]]
    populations: list[str]


class RevisionManifest(StrictModel):
    event_id: str
    revision_id: str
    order: int
    populations: list[str]
    row_count: int
    checksum: str
    bucket: str
    object_key: str
    ready: bool = True


class Lease(StrictModel):
    key: dict[str, str]
    owner: str
    token: str
    expires_at: int
    ttl: int


class PromotionResult(StrictModel):
    status: str
    revision_id: str | None = None
    intents: list[dict[str, Any]] = []


class ManifestPage(StrictModel):
    population_id: str
    epoch: int
    members: list[dict[str, Any]]
    cursor: dict[str, Any] | None = None


class RecoveryReport(StrictModel):
    acknowledged: int = 0
    deferred: int = 0
    populations: list[str] = []
    cursor: dict[str, Any] | None = None


class BuildCheckpoint(StrictModel):
    population_id: str
    generation_id: str
    epoch: int
    phase: str = "copy"
    cursor: dict[str, Any] | None = None
    member: dict[str, Any] | None = None
    row_cursor: dict[str, Any] | None = None
    carry: dict[str, Any] | None = None
    row_count: int = 0
    checksum: str = ""


class BuildResult(StrictModel):
    population_id: str
    generation_id: str
    epoch: int
    complete: bool
    checkpoint: BuildCheckpoint
    row_count: int = 0
    checksum: str = ""
