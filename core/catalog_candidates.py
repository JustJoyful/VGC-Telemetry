"""Evidence-backed candidates; changed mechanics require an explicit official review."""

import hashlib
import html
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from core.catalog import load_catalog, to_id
from core.models.catalog import CatalogCandidate, CatalogChangeReview, CatalogSnapshot
from core.models.champions import CapturedDocument
from ingestion.staging import canonical_bytes

COLLECTIONS = ("species", "items", "abilities", "moves", "natures")


def official_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "champions-news.pokemon-home.com",
        "web-view.app.pokemonchampions.jp",
    }


def notice_interval(body, rules_id):
    text = html.unescape(re.sub("<[^>]+>", " ", body))
    if f"Regulation Set {rules_id}" not in text:
        raise ValueError("Official rules identity missing")
    matches = re.findall(r"([A-Z][a-z]+ \d{1,2}, \d{4}), at (\d{2}:\d{2}) UTC", text)
    if len(matches) != 2:
        raise ValueError("Unknown or conflicting official interval")
    start, end = [
        datetime.strptime(f"{d} {t}", "%B %d, %Y %H:%M").replace(tzinfo=UTC) for d, t in matches
    ]
    return start, end + timedelta(minutes=1)  # published final minute is inclusive


def build_candidate(data, *, documents, previous=None, review=None):
    if len(canonical_bytes(data)) > 10_000_000 or len(documents) > 20:
        raise ValueError("Catalog candidate exceeds bounded evidence size")
    data = json.loads(canonical_bytes(data))
    data["snapshot_id"] = "candidate-input"
    errors, snapshot, ids = [], None, []
    documents = {url: CapturedDocument.model_validate(doc) for url, doc in documents.items()}
    if review is not None:
        review = CatalogChangeReview.model_validate(review)
    identity = {
        "snapshot": {k: v for k, v in data.items() if k != "snapshot_id"},
        "documents": {k: v.model_dump(mode="json") for k, v in documents.items()},
        "previous": previous.model_dump(mode="json") if previous else None,
        "review": review.model_dump(mode="json") if review else None,
    }
    cid = hashlib.sha256(canonical_bytes(identity)).hexdigest()
    additions, removals = {}, {}
    for collection in COLLECTIONS:
        current = set(data[collection]) if isinstance(data.get(collection), dict) else set()
        old = set(getattr(previous, collection)) if previous else set()
        additions[collection], removals[collection] = sorted(current - old), sorted(old - current)
    try:
        snapshot = CatalogSnapshot.model_validate(data | {"snapshot_id": "champions-" + cid})
    except ValidationError:
        errors.append("model_validation")
    if not data.get("coverage_complete"):
        errors.append("incomplete_coverage")
    evidence = [ref for ref in (data.get("evidence") or []) if isinstance(ref, dict)]
    if not evidence or any(ref.get("url") not in documents for ref in evidence):
        errors.append("evidence_missing")
    if any(
        ref.get("url") in documents
        and (
            documents[ref["url"]].url != ref["url"]
            or documents[ref["url"]].content_hash != ref.get("content_hash")
            or documents[ref["url"]].status != 200
        )
        for ref in evidence
    ):
        errors.append("evidence_hash_mismatch")
    revision = data.get("upstream_revision", "")
    if (
        not isinstance(revision, str)
        or not re.fullmatch("[a-f0-9]{40}", revision)
        or not any(
            url.startswith(f"https://raw.githubusercontent.com/smogon/pokemon-showdown/{revision}/")
            for url in documents
        )
    ):
        errors.append("upstream_evidence_missing")
    if any(
        not official_url(ref.get("url", ""))
        and not ref.get("url", "").startswith(
            f"https://raw.githubusercontent.com/smogon/pokemon-showdown/{revision}/"
        )
        for ref in evidence
    ):
        errors.append("unsupported_evidence_source")
    if data.get("source_kind") != "community":
        errors.append("supplementary_authority_required")
    if data.get("game_data_revision") != "showdown-" + str(revision):
        errors.append("upstream_identity_conflict")
    if previous is None:
        # A URL/hash authenticates a document, not an arbitrary resolved export.
        index = Path(__file__).resolve().parent / "regulations/v2/index.json"
        trusted = [
            load_catalog(revision, index_path=index).model_dump(mode="json")
            for revision in json.loads(index.read_text())["snapshots"]
        ]
        excluded = {"snapshot_id", "evidence"}
        content = {k: v for k, v in data.items() if k not in excluded}
        if not any(
            content == {k: v for k, v in known.items() if k not in excluded} for known in trusted
        ):
            errors.append("bootstrap_export_unverified")
    elif previous.verification_status != "verified" or not previous.coverage_complete:
        errors.append("baseline_unverified")
    notices = [doc for url, doc in documents.items() if official_url(url) and "/page/" in url]
    pools = [
        doc for url, doc in documents.items() if official_url(url) and url.endswith("/pokemon.html")
    ]
    try:
        if len(notices) != 1 or len(pools) != 1 or snapshot is None:
            raise ValueError("Missing official rule/pool evidence")
        start, end = notice_interval(notices[0].body, snapshot.rules_id)
        if (snapshot.effective_from, snapshot.effective_until) != (start, end):
            errors.append("effective_time_conflict")
        if pools[0].url not in notices[0].body:
            raise ValueError("Pool not linked by official notice")
        if any(doc.url not in {ref.get("url") for ref in evidence} for doc in notices + pools):
            errors.append("evidence_missing")
        rows = json.loads(re.search(r"const pokemons = (\[.*?\]);", pools[0].body).group(1))
        ids = sorted({snapshot.aliases.get(to_id(row[2]), to_id(row[2])) for row in rows})
        if not rows or any(
            i not in snapshot.species or snapshot.species[i].battle_only for i in ids
        ):
            errors.append("incomplete_coverage")
    except (ValueError, TypeError, IndexError, AttributeError):
        errors.append("official_evidence_missing")
    if snapshot:
        for species in snapshot.species.values():
            if (
                set(species.ability_ids) - snapshot.abilities.keys()
                or set(species.move_ids) - snapshot.moves.keys()
            ):
                errors.append("reference_integrity")
        for item in snapshot.items.values():
            if any(
                base not in snapshot.species or mega not in snapshot.species
                for base, mega in item.mega_mappings.items()
            ):
                errors.append("reference_integrity")
    changed = previous is not None and any(additions[c] or removals[c] for c in COLLECTIONS)
    # Mechanical field changes also require review, even if the ID sets are identical.
    previous_data = previous.model_dump(mode="json") if previous else {}
    unchanged = previous is not None and all(
        data.get(k) == previous_data.get(k)
        for k in (
            "rules_id",
            "game_data_revision",
            "upstream_revision",
            "effective_from",
            "effective_until",
            "aliases",
            "allocation_per_stat",
            "allocation_total",
            *COLLECTIONS,
        )
    )
    if previous and (changed or not unchanged):
        if not review:
            errors.append("change_review_required")
        elif (
            review.additions != additions
            or review.removals != removals
            or any(
                not official_url(ref.url)
                or ref.url not in documents
                or ref.content_hash != documents[ref.url].content_hash
                or not review.quotations.get(ref.url)
                or review.quotations[ref.url] not in documents[ref.url].body
                for ref in review.evidence
            )
        ):
            errors.append("official_change_conflict")
    status = "pending" if errors else "verified"
    if snapshot:
        snapshot = snapshot.model_copy(update={"verification_status": status})
    return CatalogCandidate(
        candidate_id=cid,
        input_snapshot=data,
        previous_snapshot=previous,
        snapshot=snapshot,
        documents=documents,
        review=review,
        status=status,
        validation_errors=sorted(set(errors)),
        registration_ids=ids,
        additions=additions,
        removals=removals,
        unchanged=unchanged,
    )


def validate_candidate(candidate):
    checked = build_candidate(
        candidate.input_snapshot,
        documents=candidate.documents,
        previous=candidate.previous_snapshot,
        review=candidate.review,
    )
    if checked != candidate:
        raise ValueError("Candidate differs from evidence-derived validation")
    return checked
