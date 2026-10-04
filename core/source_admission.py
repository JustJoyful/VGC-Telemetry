"""Evidence-based event classification; no default regulation or date inference."""

import json
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, ValidationError

from core.models.champions import (
    Diagnostic,
    EventIdentification,
    EvidenceRef,
    Identifier,
    SourceCapture,
    StrictModel,
    TournamentMetadataV2,
)


class FormatMapping(StrictModel):
    game: str = "VGC"
    platforms: list[str] = Field(default_factory=list)
    rules_id: Identifier
    catalog_revision: Identifier
    game_data_revision: Identifier
    battle_format: Literal["doubles", "singles"]
    evidence: list[EvidenceRef] = Field(min_length=1)


class EventMapping(FormatMapping):
    name: str | None = None
    starts_at: str | None = None
    division_id: str | None = None


class SourceAdmission(StrictModel):
    source: Literal["limitless", "rk9"]
    enabled: bool = False
    verified: bool = False
    adapter_version: str = "2.0.0"
    evidence: list[EvidenceRef] = Field(default_factory=list)
    format_mappings: dict[str, FormatMapping] = Field(default_factory=dict)
    event_mappings: dict[str, EventMapping] = Field(default_factory=dict)
    event_ids: list[str] = Field(default_factory=list)
    public_scraping_authorized: bool = False
    max_events: int = Field(default=10, ge=1, le=100)
    max_team_pages: int = Field(default=1000, ge=0, le=5000)


def load_admissions(path: Path) -> list[SourceAdmission]:
    admissions = [
        SourceAdmission.model_validate(value) for value in json.loads(path.read_text())["sources"]
    ]
    if len({a.source for a in admissions}) != len(admissions):
        raise ValueError("Duplicate source admission configuration")
    return admissions


def document_evidence(capture: SourceCapture, key: str) -> EvidenceRef:
    doc = capture.documents[key]
    return EvidenceRef(
        url=doc.url, captured_at=capture.fetched_at, document_key=key, content_hash=doc.content_hash
    )


def classify_event(capture: SourceCapture, admission: SourceAdmission) -> EventIdentification:
    def pending(code: str, message: str, status="pending"):
        return EventIdentification(
            status=status, diagnostics=[Diagnostic(code=code, path="metadata", message=message)]
        )

    if capture.source != admission.source or not admission.verified or not admission.evidence:
        return pending("source_unverified", "Source identity mapping is not verified")
    event_id = quote(capture.source_event_id, safe="")
    if capture.source == "limitless":
        origins = {
            key: f"https://play.limitlesstcg.com/api/tournaments/{event_id}/{key}"
            for key in ("details", "standings", "pairings")
        }
    else:
        origins = {
            key: f"https://rk9.gg/{path}/{event_id}"
            for key, path in (
                ("event", "tournament"),
                ("roster", "roster"),
                ("pairings", "pairings"),
            )
        }
    for key, url in origins.items():
        if key in capture.documents and capture.documents[key].url != url:
            return pending(
                "capture_origin_conflict",
                "Document URL does not match source/event identity",
                "rejected",
            )
    try:
        if capture.source == "limitless":
            doc = capture.documents["details"]
            if doc.status != 200:
                return pending("source_unavailable", "Event details are unavailable")
            details = json.loads(doc.body)
            if not isinstance(details, dict):
                return pending("invalid_details", "Event details must be an object")
            if details.get("id", capture.source_event_id) != capture.source_event_id:
                return pending("event_identity_conflict", "Capture and details event IDs differ")
            mapping = admission.format_mappings.get(details.get("format"))
            if mapping is None:
                return pending(
                    "format_unverified", "Game/format is not an admitted Champions format"
                )
            if (
                details.get("game") != mapping.game
                or details.get("platform") not in mapping.platforms
            ):
                return pending(
                    "platform_conflict",
                    "Game/platform conflicts with admission evidence",
                    "rejected",
                )
            if details.get("specialRules") or details.get("bannedCards"):
                return pending("custom_rules", "Custom rules need a separate verified population")
            phases = details.get("phases", [])
            if not isinstance(phases, list) or any(not isinstance(p, dict) for p in phases):
                return pending("invalid_metadata", "Tournament phases must be objects")
            metadata = TournamentMetadataV2(
                event_id=f"limitless:{capture.source_event_id}",
                name=details.get("name"),
                starts_at=details.get("date"),
                game_id="pokemon-champions",
                battle_format=mapping.battle_format,
                rules_id=mapping.rules_id,
                catalog_revision=mapping.catalog_revision,
                game_data_revision=mapping.game_data_revision,
                entrant_count=details.get("players"),
                match_formats=sorted({phase["mode"] for phase in phases if phase.get("mode")}),
                evidence=[document_evidence(capture, "details"), *mapping.evidence],
            )
        else:
            # Mapping requires organizer rules evidence; the logo alone establishes no regulation.
            mapping = admission.event_mappings.get(capture.source_event_id)
            if mapping is None:
                return pending(
                    "event_rules_unverified", "RK9 event needs sourced game/rules mapping"
                )
            doc = capture.documents["event"]
            if (
                doc.status != 200
                or 'src="/static/images/pokemon-vg-champions-300.png"' not in doc.body
            ):
                return pending("game_unverified", "Event has no verified Champions game marker")
            metadata = TournamentMetadataV2(
                event_id=f"rk9:{capture.source_event_id}",
                name=mapping.name,
                starts_at=mapping.starts_at,
                game_id="pokemon-champions",
                battle_format=mapping.battle_format,
                division_id=mapping.division_id,
                rules_id=mapping.rules_id,
                catalog_revision=mapping.catalog_revision,
                game_data_revision=mapping.game_data_revision,
                evidence=[document_evidence(capture, "event"), *mapping.evidence],
            )
        if metadata.battle_format != "doubles":
            return pending(
                "unsupported_format", "Initial analytic population is doubles", "rejected"
            )
        return EventIdentification(status="verified", metadata=metadata)
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        return pending("invalid_metadata", f"Unable to validate event metadata: {error}")
