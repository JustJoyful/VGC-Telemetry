"""Local Phase 0 verification artifact. This is not a public /v2 API response."""

import argparse
from pathlib import Path

from pydantic import Field

from core.catalog import load_catalog
from core.models.champions import (
    ChampionsTournament,
    Diagnostic,
    NormalizationResult,
    SourceCapture,
    StrictModel,
)
from core.source_admission import classify_event, load_admissions
from ingestion.normalize import normalize_capture


class PreviewReport(StrictModel):
    schema_version: int = 2
    admission_status: str
    tournament: ChampionsTournament | None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    observed_entrants: int = 0
    complete_rosters: int = 0
    partial_rosters: int = 0
    absent_rosters: int = 0
    eligible_rosters: int = 0
    unknown_fields: dict[str, int] = Field(default_factory=dict)


def build_preview(result: NormalizationResult) -> PreviewReport:
    tournament = result.tournament
    report = PreviewReport(
        admission_status=result.admission_status,
        tournament=tournament,
        diagnostics=result.diagnostics,
    )
    if tournament is None:
        return report
    report.observed_entrants = len(tournament.teams)
    for team in tournament.teams:
        field = team.publication_status + "_rosters"
        setattr(report, field, getattr(report, field) + 1)
    report.eligible_rosters = (
        report.complete_rosters if result.admission_status == "verified" else 0
    )
    unknown = {
        name: 0
        for name in ["ability_id", "moves", "item", "nature_id", "stat_points", "mega_variant_ids"]
    }
    for team in tournament.teams:
        for mon in team.pokemon:
            for name in unknown:
                absent = (
                    mon.item.state == "unknown" if name == "item" else getattr(mon, name) is None
                )
                unknown[name] += int(absent)
    report.unknown_fields = unknown
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["capture", "admission", "catalog-index", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        capture = SourceCapture.model_validate_json(args.capture.read_text())
        admissions = load_admissions(args.admission)
        admission = next((a for a in admissions if a.source == capture.source), None)
        if admission is None:
            raise ValueError("Capture has no source admission configuration")
        identification = classify_event(capture, admission)
        if identification.status == "verified":
            catalog = load_catalog(
                identification.metadata.catalog_revision, index_path=args.catalog_index
            )
            result = normalize_capture(capture, admission=admission, catalog=catalog)
        else:
            result = NormalizationResult(
                admission_status=identification.status, diagnostics=identification.diagnostics
            )
        args.output.write_text(build_preview(result).model_dump_json(indent=2) + "\n")
    except (ValueError, OSError) as error:
        parser.exit(2, f"Preview failed: {error}\n")


if __name__ == "__main__":
    main()
