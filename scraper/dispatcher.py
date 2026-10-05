"""Explicit, bounded source collection. No aggregate publication or DynamoDB writes."""

from pathlib import Path
from typing import Any

from pydantic import Field

from core.catalog import load_catalog
from core.models.champions import Diagnostic, NormalizationResult, StrictModel
from core.source_admission import SourceAdmission, classify_event
from ingestion.normalize import normalize_capture
from ingestion.staging import stage_capture, stage_normalized
from scraper.limitless_client import LimitlessAPIClient


class SourceReport(StrictModel):
    discovered: int = 0
    captured: int = 0
    pending: int = 0
    rejected: int = 0
    failed: int = 0
    staged_references: list[dict[str, str]] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class CollectionReport(StrictModel):
    sources: dict[str, SourceReport] = Field(default_factory=dict)


class CollectionFailure(RuntimeError):
    def __init__(self, report):
        self.report = report
        super().__init__("Configured source collection failed; independent staged work is retained")


def collect_sources(
    *,
    admissions: list[SourceAdmission],
    catalog_index: Path,
    s3_client: Any,
    bucket_name: str,
    clients=None,
) -> CollectionReport:
    if len({a.source for a in admissions}) != len(admissions):
        raise ValueError("Duplicate source configuration")
    for a in admissions:
        if a.enabled and (not a.verified or not a.evidence or not a.format_mappings):
            raise ValueError(f"Enabled source lacks verified admission configuration: {a.source}")
    report = CollectionReport()
    supplied = clients or {}
    for admission in admissions:
        source = admission.source
        stats = SourceReport()
        report.sources[source] = stats
        if not admission.enabled:
            continue
        client = supplied.get(source)
        owns_client = client is None
        if client is None:
            client = LimitlessAPIClient()
        try:
            try:
                if admission.event_ids:
                    ids = list(dict.fromkeys(admission.event_ids))
                elif source == "limitless":
                    ids = []
                    for format_id, mapping in admission.format_mappings.items():
                        for row in client.get_tournaments(
                            game=mapping.game, format=format_id, limit=admission.max_events, page=1
                        ):
                            identifier = row.get("id")
                            if not isinstance(identifier, str) or not identifier:
                                raise ValueError("Discovered event has no source ID")
                            if identifier not in ids:
                                ids.append(identifier)
                stats.discovered = len(ids)
                if len(ids) > admission.max_events:
                    stats.diagnostics.append(
                        Diagnostic(
                            code="collection_limit",
                            path="events",
                            message=(
                                "Discovery exceeds bounded Phase 0 collection; "
                                "durable pagination is Phase 3"
                            ),
                        )
                    )
            except Exception as error:
                stats.failed += 1
                stats.diagnostics.append(
                    Diagnostic(
                        code="discovery_failed",
                        path=source,
                        message=str(error) or type(error).__name__,
                    )
                )
                continue
            for event_id in ids[: admission.max_events]:
                try:
                    capture = client.capture_tournament(event_id)
                    capture_key = stage_capture(
                        capture, s3_client=s3_client, bucket_name=bucket_name
                    )
                    stats.captured += 1
                    refs = {"capture_key": capture_key}
                    stats.staged_references.append(refs)
                    identification = classify_event(capture, admission)
                    if identification.status == "verified":
                        catalog = load_catalog(
                            identification.metadata.catalog_revision, index_path=catalog_index
                        )
                        result = normalize_capture(capture, admission=admission, catalog=catalog)
                    else:
                        result = NormalizationResult(
                            admission_status=identification.status,
                            diagnostics=identification.diagnostics,
                        )
                    refs["normalized_key"] = stage_normalized(
                        result,
                        capture_key=capture_key,
                        s3_client=s3_client,
                        bucket_name=bucket_name,
                    )
                    if result.admission_status == "pending":
                        stats.pending += 1
                    elif result.admission_status == "rejected":
                        stats.rejected += 1
                    stats.diagnostics.extend(result.diagnostics)
                    # Actual HTTP refusals are visible operational failures,
                    # even though evidence was retained.
                    failures = [key for key, doc in capture.documents.items() if doc.status >= 400]
                    if failures:
                        raise RuntimeError(
                            f"HTTP refusal/failure in captured documents: {failures}"
                        )
                except Exception as error:
                    stats.failed += 1
                    stats.diagnostics.append(
                        Diagnostic(
                            code="capture_failed",
                            path=event_id,
                            message=str(error) or type(error).__name__,
                        )
                    )
        finally:
            if owns_client:
                client.client.close()
    if any(s.failed for s in report.sources.values()):
        raise CollectionFailure(report)
    return report
