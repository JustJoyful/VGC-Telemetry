"""Pinned public catalogs with optional retained runtime revisions."""

import hashlib
from functools import lru_cache

from app.v2.errors import unavailable
from core.aws import aws_client
from core.public_reference import load_public_reference
from core.runtime_catalog import CatalogRuntime
from db.catalog import CatalogStore
from db.client import get_dynamodb_table
from ingestion.staging import canonical_bytes


@lru_cache(maxsize=1)
def runtime_catalog(bucket):
    return CatalogRuntime(
        CatalogStore(table=get_dynamodb_table(), s3_client=aws_client("s3"), bucket=bucket)
    )


def reference(manifest_hash=None, *, config, revision=None):
    dynamic = manifest_hash and manifest_hash.startswith("catalog-")
    ref = load_public_reference(None if dynamic else manifest_hash)
    runtime = config.catalog_runtime
    if runtime is None or (manifest_hash and not dynamic):
        return ref
    try:
        if dynamic:
            candidate = runtime.candidate(manifest_hash.removeprefix("catalog-"))
            if candidate.status != "verified":
                raise ValueError("Unverified pinned reference")
            active = candidate.snapshot
        else:
            active = runtime.refresh()
            candidate = runtime.current_candidate
        for value in [candidate] + (
            [runtime.candidate(revision.removeprefix("champions-"))]
            if revision and revision not in ref["catalogs"] and revision != active.snapshot_id
            else []
        ):
            if value.status != "verified":
                raise ValueError("Unverified catalog")
            snapshot = value.snapshot
            ref["catalogs"][snapshot.snapshot_id] = snapshot
            ref["eligibility"][snapshot.snapshot_id] = {
                "eligible_registration_ids": value.registration_ids
            }
            ref["index"]["snapshots"][snapshot.snapshot_id] = {
                "sha256": hashlib.sha256(
                    canonical_bytes(snapshot.model_dump(mode="json"))
                ).hexdigest()
            }
        ref["manifest_hash"] = "catalog-" + candidate.candidate_id
        ref["default_revision"] = active.snapshot_id
        return ref
    except Exception as error:
        raise unavailable("Runtime reference is unavailable") from error


def resolve_catalog(revision, *, config):
    ref = load_public_reference(None)
    if revision in ref["catalogs"]:
        return ref["catalogs"][revision]
    if config.catalog_runtime:
        return config.catalog_runtime.revision(revision)
    raise ValueError("Catalog not found")


def validate_members(members, catalog):
    """Reject redundant roster selectors and battle-only observed forms."""
    from app.v2.errors import V2Error

    bases = set()
    for selector in members:
        kind, identifier = selector.split(":")
        entry = catalog.species.get(identifier)
        if kind == "species":
            valid = identifier in {s.base_species_id for s in catalog.species.values()}
            base = identifier
        else:
            valid = entry is not None and (
                entry.is_mega if kind == "mega" else not entry.battle_only
            )
            base = entry.base_species_id if entry else None
        if not valid or base in bases:
            raise V2Error(422, "invalid_query", "Unknown or incompatible member selectors")
        bases.add(base)
