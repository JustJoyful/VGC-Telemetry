"""Version 2 normalization dispatch and deterministic semantic identity."""

import hashlib
import json

from core.models.catalog import CatalogSnapshot
from core.models.champions import (
    ChampionsTournament,
    Diagnostic,
    NormalizationResult,
    SourceCapture,
)
from core.source_admission import SourceAdmission


def normalize_capture(
    capture: SourceCapture, *, admission: SourceAdmission, catalog: CatalogSnapshot
) -> NormalizationResult:
    if capture.source == "limitless":
        from scraper.limitless_normalizer import normalize_limitless_capture

        return normalize_limitless_capture(capture, admission, catalog)
    return NormalizationResult(
        admission_status="rejected",
        diagnostics=[
            Diagnostic(code="unknown_source", path="source", message="No source adapter exists")
        ],
    )


def normalized_content_hash(tournament: ChampionsTournament) -> str:
    def semantic(value):
        if isinstance(value, dict):
            return {
                k: semantic(v) for k, v in value.items() if k not in ("evidence", "field_evidence")
            }
        if isinstance(value, list):
            return [semantic(v) for v in value]
        return value

    value = semantic(tournament.model_dump(mode="json", by_alias=True))
    value["teams"].sort(key=lambda row: row["entry_id"])
    value["matches"].sort(key=lambda row: row["match_id"])
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(body).hexdigest()
