"""Bounded immutable reference reads independent of tournament storage."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.models.v2 import MoveView, Page, PokemonView, PublicEvidence, RegulationView, SnapshotInfo
from app.models.v2_queries import PageQuery, PokemonQuery, ReferenceQuery, canonical_query
from app.v2.config import V2ReadConfig, get_v2_config
from app.v2.cursors import _decode, decode_cursor, decode_snapshot, encode_cursor, encode_snapshot
from app.v2.errors import V2Error, unavailable
from core.catalog import to_id
from core.public_reference import load_public_reference
from ingestion.staging import canonical_bytes

router = APIRouter(prefix="/v2", tags=["Pinned native reference"])
Config = Annotated[V2ReadConfig, Depends(get_v2_config)]


def _token(snapshot, config):
    return encode_snapshot(
        snapshot.snapshot_id, expires_at=snapshot.expires_at, secret=config.require_secret()
    )


def _request(query, endpoint, config):
    secret = config.require_secret()
    pinned = None
    if query.cursor:
        value = _decode(query.cursor, now=config.clock(), secret=secret, kind="cursor")
        pinned = SnapshotInfo(snapshot_id=value["snapshot_id"], expires_at=value["exp"])
    if query.snapshot:
        token = decode_snapshot(query.snapshot, now=config.clock(), secret=secret)
        if pinned and pinned != token:
            raise V2Error(400, "invalid_cursor", "Snapshot and cursor differ")
        pinned = token
    digest, default = None, config.default_catalog_revision
    if pinned:
        parts = pinned.snapshot_id.split(":")
        if len(parts) != 3 or parts[0] != "reference":
            raise V2Error(400, "invalid_cursor", "Not a reference snapshot")
        digest, default = parts[1:]
    ref = load_public_reference(digest)
    if default not in ref["catalogs"]:
        raise unavailable("Default native catalog is unavailable")
    revision = getattr(query, "catalog_revision", None) or default
    if revision not in ref["catalogs"]:
        raise V2Error(404, "not_found", "Catalog not found")
    snapshot = pinned or SnapshotInfo(
        snapshot_id="reference:" + ref["manifest_hash"] + ":" + default,
        expires_at=config.clock() + config.token_seconds,
    )
    resolved = {**canonical_query(query), "catalog_revision": revision, "order": "canonical-id"}
    position = None
    if query.cursor:
        position = decode_cursor(
            query.cursor,
            endpoint=endpoint,
            query=resolved,
            limit=query.limit,
            now=config.clock(),
            secret=secret,
        )["position"]
    return ref, snapshot, revision, resolved, position


def _page(items, *, model, snapshot, endpoint, resolved, position, limit, config):
    if not 1 <= limit <= 100:
        raise V2Error(422, "invalid_query", "Limit must be 1–100")
    page = Page[model](items=[], snapshot=_token(snapshot, config))
    last = None
    for ordinal, item in items:
        if position is not None and ordinal <= position:
            continue
        candidate = page.model_copy(update={"items": [*page.items, item]})
        if (
            len(page.items) == limit
            or len(canonical_bytes(candidate.model_dump(mode="json"))) + 4096
            > config.response_bytes
        ):
            if not page.items:
                raise unavailable("Reference item exceeds supported response size")
            page.next_cursor = encode_cursor(
                snapshot=snapshot,
                endpoint=endpoint,
                query=resolved,
                limit=limit,
                position=last,
                secret=config.require_secret(),
            )
            break
        page.items.append(item)
        last = ordinal
    if len(canonical_bytes(page.model_dump(mode="json"))) > config.response_bytes:
        raise unavailable("Reference response exceeds supported size")
    return page


def _evidence(catalog):
    return [
        PublicEvidence(**ref.model_dump(include={"url", "content_hash", "captured_at"}))
        for ref in catalog.evidence[:4]
    ]


def _pokemon(identifier, revision, reference):
    catalog = reference["catalogs"][revision]
    species = catalog.species[identifier]
    return PokemonView(
        pokemon_id=identifier,
        species_id=species.base_species_id,
        name=species.name,
        catalog_revision=revision,
        types=species.types,
        base_stats=species.base_stats,
        ability_ids=species.ability_ids,
        battle_only=species.battle_only,
        is_mega=species.is_mega,
        registration_eligibility="confirmed"
        if identifier in reference["eligibility"][revision]["eligible_registration_ids"]
        else "unknown",
        learnset_count=len(species.move_ids),
        learnset_link=f"/v2/pokemon/{identifier}/learnset?catalog_revision={revision}",
        provenance=_evidence(catalog),
        catalog_hash=reference["index"]["snapshots"][revision]["sha256"],
    )


def _resolve(identifier, catalog):
    # Canonical IDs win over aliases; display names use explicit retained mappings.
    if identifier in catalog.species:
        return identifier
    key = to_id(identifier)
    resolved = catalog.aliases.get(key, key)
    if resolved not in catalog.species:
        raise V2Error(404, "not_found", "Pokemon not found")
    return resolved


def reference_page(
    *,
    catalog_revision: str,
    reference_manifest_hash: str,
    query,
    position: str | None,
    limit: int,
    config=None,
    snapshot=None,
) -> Page[PokemonView]:
    config = config or get_v2_config()
    config.require_secret()
    reference = load_public_reference(reference_manifest_hash)
    if catalog_revision not in reference["catalogs"]:
        raise V2Error(404, "not_found", "Catalog not found")
    catalog = reference["catalogs"][catalog_revision]
    if query.form_id and (
        not query.species_id
        or query.form_id not in catalog.species
        or catalog.species[query.form_id].base_species_id != query.species_id
    ):
        raise V2Error(422, "invalid_query", "Species and form are incompatible")
    snapshot = snapshot or SnapshotInfo(
        snapshot_id=f"reference:{reference_manifest_hash}:{config.default_catalog_revision}",
        expires_at=config.clock() + config.token_seconds,
    )
    items = [
        (identifier, _pokemon(identifier, catalog_revision, reference))
        for identifier, species in sorted(catalog.species.items())
        if (not query.species_id or species.base_species_id == query.species_id)
        and (not query.form_id or identifier == query.form_id)
        and (
            not query.legal_only
            or identifier in reference["eligibility"][catalog_revision]["eligible_registration_ids"]
        )
    ]
    resolved = {
        **canonical_query(query),
        "catalog_revision": catalog_revision,
        "order": "canonical-id",
    }
    return _page(
        items,
        model=PokemonView,
        snapshot=snapshot,
        endpoint="/v2/pokemon",
        resolved=resolved,
        position=position,
        limit=limit,
        config=config,
    )


def regulation_page(
    *, reference_manifest_hash: str, position: str | None, limit: int, config=None, snapshot=None
) -> Page[RegulationView]:
    config = config or get_v2_config()
    config.require_secret()
    ref = load_public_reference(reference_manifest_hash)
    snapshot = snapshot or SnapshotInfo(
        snapshot_id=f"reference:{reference_manifest_hash}:{config.default_catalog_revision}",
        expires_at=config.clock() + config.token_seconds,
    )
    default = snapshot.snapshot_id.split(":")[2]
    items = [
        (
            revision,
            RegulationView(
                catalog_revision=revision,
                rules_id=c.rules_id,
                effective_from=c.effective_from,
                effective_until=c.effective_until,
                default=revision == default,
                coverage="Pinned M-B/M-C; historical M-A and unknown patches unverified",
                provenance=_evidence(c),
            ),
        )
        for revision, c in sorted(ref["catalogs"].items())
    ]
    resolved = {
        **canonical_query(PageQuery()),
        "catalog_revision": default,
        "order": "canonical-id",
    }
    return _page(
        items,
        model=RegulationView,
        snapshot=snapshot,
        endpoint="/v2/regulations",
        resolved=resolved,
        position=position,
        limit=limit,
        config=config,
    )


@router.get("/regulations", response_model=Page[RegulationView])
def regulations(config: Config, query: Annotated[PageQuery, Query()]):
    """Retained native intervals; distinguish official pools from community mechanics."""
    ref, snap, _, _, position = _request(query, "/v2/regulations", config)
    return regulation_page(
        reference_manifest_hash=ref["manifest_hash"],
        position=position,
        limit=query.limit,
        config=config,
        snapshot=snap,
    )


@router.get("/pokemon", response_model=Page[PokemonView])
def pokemon(config: Config, query: Annotated[PokemonQuery, Query()]):
    """Canonical IDs; legal_only requires explicit official-pool identity."""
    ref, snap, revision, _, position = _request(query, "/v2/pokemon", config)
    return reference_page(
        catalog_revision=revision,
        reference_manifest_hash=ref["manifest_hash"],
        query=query,
        position=position,
        limit=query.limit,
        config=config,
        snapshot=snap,
    )


@router.get("/pokemon/{identifier}", response_model=PokemonView)
def pokemon_detail(identifier: str, config: Config, query: Annotated[ReferenceQuery, Query()]):
    ref, snap, revision, _, _ = _request(query, "/v2/pokemon/" + identifier, config)
    value = _pokemon(_resolve(identifier, ref["catalogs"][revision]), revision, ref)
    value.snapshot = _token(snap, config)
    if len(canonical_bytes(value.model_dump(mode="json"))) > config.response_bytes:
        raise unavailable("Reference item exceeds supported response size")
    return value


@router.get("/pokemon/{identifier}/learnset", response_model=Page[MoveView])
def learnset(identifier: str, config: Config, query: Annotated[ReferenceQuery, Query()]):
    """Complete pinned community learnset; move-ID order, bounded pages, fixed expiry."""
    endpoint = "/v2/pokemon/" + identifier + "/learnset"
    ref, snap, revision, resolved, position = _request(query, endpoint, config)
    catalog = ref["catalogs"][revision]
    identity = _resolve(identifier, catalog)
    moves = [
        (
            move,
            MoveView(
                move_id=move,
                name=catalog.moves[move].name,
                details=catalog.moves[move].details,
                catalog_revision=revision,
                catalog_hash=ref["index"]["snapshots"][revision]["sha256"],
            ),
        )
        for move in sorted(catalog.species[identity].move_ids)
    ]
    return _page(
        moves,
        model=MoveView,
        snapshot=snap,
        endpoint=endpoint,
        resolved=resolved,
        position=position,
        limit=query.limit,
        config=config,
    )
