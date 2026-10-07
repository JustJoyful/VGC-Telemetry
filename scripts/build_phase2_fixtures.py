"""Assemble retained public fixtures only; no network or production metric reducers."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from core.models.champions import SourceCapture

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "tests/fixtures/sources/captures"


def assemble_event(event_id: str) -> tuple[SourceCapture, list[dict]]:
    documents, report = {}, []
    for key in ("details", "standings", "pairings"):
        url = f"https://play.limitlesstcg.com/api/tournaments/{event_id}/{key}"
        matches = []
        for path in sorted(CAPTURES.glob("*.json")):
            meta = json.loads(path.read_text())
            if meta["url"] == url:
                matches.append((path, meta))
        if len(matches) != 1:
            raise ValueError(f"Missing/ambiguous retained evidence for {url}")
        path, meta = matches[0]
        body_path = CAPTURES / meta["body_path"]
        body = body_path.read_bytes()
        if meta["status"] != 200 or hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError(f"Invalid retained capture {path}")
        documents[key] = dict(
            url=url,
            body=body.decode("utf-8"),
            content_hash=meta["sha256"],
            status=meta["status"],
            content_type=meta["content_type"],
        )
        report.append(
            dict(
                metadata_path=str(path.relative_to(ROOT)),
                body_path=str(body_path.relative_to(ROOT)),
                url=url,
                sha256=meta["sha256"],
                captured_at=datetime.fromisoformat(meta["captured_at"]).isoformat(),
            )
        )
    return SourceCapture(
        source="limitless",
        source_event_id=event_id,
        fetched_at=max(r["captured_at"] for r in report),
        documents=documents,
    ), report


def build():
    capture, report = assemble_event("6abe6180880ed327106e0cf4")
    path = ROOT / "tests/fixtures/sources/limitless/second-accepted-capture.json"
    path.write_text(capture.model_dump_json(indent=2) + "\n")
    evidence = ROOT / "tests/fixtures/phase2/source-evidence.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(report, indent=2) + "\n")


def build_reference():
    """Copy retained bytes and project only explicit official-name registrations."""
    from core.catalog import load_catalog
    from scripts.build_phase0_fixtures import official_id

    root = ROOT / "core/regulations/v2/public"
    root.mkdir(parents=True, exist_ok=True)

    def retain(body, suffix):
        digest = hashlib.sha256(body).hexdigest()
        path = root / (digest + suffix)
        if path.exists() and path.read_bytes() != body:
            raise ValueError("Immutable reference conflict")
        if not path.exists():
            path.write_bytes(body)
        return {"path": path.name, "sha256": digest}

    def encoded(value):
        return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()

    original = ROOT / "core/regulations/v2/index.json"
    index = json.loads(original.read_text())
    retained_index, sidecars = {"snapshots": {}}, {}
    for revision, entry in index["snapshots"].items():
        catalog = load_catalog(revision, index_path=original)
        body = (original.parent / entry["path"]).read_bytes()
        retained_index["snapshots"][revision] = retain(body, ".catalog.json")
        evidence = next(ref for ref in catalog.evidence if "web-view.app" in ref.url)
        captures = [
            path
            for path in CAPTURES.glob("*.json")
            if (meta := json.loads(path.read_text()))["url"] == evidence.url
            and meta["sha256"] == evidence.content_hash
        ]
        if len(captures) != 1:
            raise ValueError("Official pool capture is missing or ambiguous")
        meta = json.loads(captures[0].read_text())
        pool = (CAPTURES / meta["body_path"]).read_bytes()
        if meta["status"] != 200 or hashlib.sha256(pool).hexdigest() != evidence.content_hash:
            raise ValueError("Official pool hash mismatch")
        rows = json.loads(re.search(r"const pokemons = (\[.*?\]);", pool.decode()).group(1))
        resolved = [
            {"source_index": i, "official_name": row[2], "canonical_id": official_id(row[2])[1]}
            for i, row in enumerate(rows)
        ]
        ids = sorted({row["canonical_id"] for row in resolved})
        if set(ids) - catalog.species.keys() or any(catalog.species[i].battle_only for i in ids):
            raise ValueError("Unresolved/battle-only official registration")
        sidecar = {
            "catalog_revision": revision,
            "catalog_hash": entry["sha256"],
            "source_row_count": len(rows),
            "resolved_rows": resolved,
            "eligible_registration_ids": ids,
            "pool": retain(pool, ".pool.body"),
            "original_metadata_path": str(captures[0].relative_to(ROOT)),
            "evidence": {
                "url": evidence.url,
                "content_hash": evidence.content_hash,
                "captured_at": meta["captured_at"],
                "path": "const pokemons[*]",
            },
        }
        sidecars[revision] = retain(encoded(sidecar), ".eligibility.json")
    manifest = {
        "schema": "public-reference-v1",
        "index": retain(encoded(retained_index), ".index.json"),
        "original_index_hash": hashlib.sha256(original.read_bytes()).hexdigest(),
        "eligibility": sidecars,
        "license": retain((original.parent / "UPSTREAM_LICENSE.txt").read_bytes(), ".license.txt"),
    }
    pointer = retain(encoded(manifest), ".json")
    bundled = root / "bundled.json"
    body = encoded({"manifest_hash": pointer["sha256"]})
    if bundled.exists() and bundled.read_bytes() != body:
        raise ValueError("Bundled reference changed; explicit reviewed activation required")
    if not bundled.exists():
        bundled.write_bytes(body)
    return pointer["sha256"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference", action="store_true", help="Build retained static projection only"
    )
    if parser.parse_args().reference:
        print(build_reference())
    else:
        build()
