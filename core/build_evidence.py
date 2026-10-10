"""Normalize explicitly supplied public JSON exports; no automatic source is admitted."""

import hashlib
from urllib.parse import urlsplit

from core.models.builds import BuildRecord, BuildSource, PublishedBuildPayload
from core.models.champions import CapturedDocument, EvidenceRef
from ingestion.staging import canonical_bytes


def normalize_public_build(document, *, source, captured_at):
    document = CapturedDocument.model_validate(document.model_dump())
    source = BuildSource.model_validate(source.model_dump())
    if document.status != 200 or urlsplit(document.url).scheme != "https":
        raise ValueError("Published build must be successful public HTTPS evidence")
    if len(document.body.encode()) > 350_000:
        raise ValueError("Published build exceeds supported size")
    payload = PublishedBuildPayload.model_validate_json(document.body)
    evidence = EvidenceRef(
        url=document.url,
        content_hash=document.content_hash,
        captured_at=captured_at,
        document_key="build:" + document.content_hash,
    )
    pokemon = []
    for mon in payload.pokemon:
        fields = {
            name: [evidence]
            for name, value in mon.model_dump().items()
            if name != "field_evidence"
            and value is not None
            and not (name == "item" and value["state"] == "unknown")
        }
        pokemon.append(mon.model_copy(update={"field_evidence": fields}))
    value = {
        **payload.model_dump(exclude={"schema_name"}, mode="json"),
        "source": source.source,
        "pokemon": [p.model_dump(mode="json") for p in pokemon],
        "evidence": evidence.model_dump(mode="json"),
        "document": document.model_dump(mode="json"),
        "admission": source.model_dump(mode="json"),
    }
    rid = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return BuildRecord(revision_id=rid, **value)
