"""Catalog selection and known-field validation, independent of legacy configuration."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from core.models.catalog import CatalogSnapshot
from core.models.champions import ChampionsTournament, Diagnostic, ItemSlot, NormalizationResult


def to_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@lru_cache(maxsize=16)
def _parse_snapshot(body: bytes) -> CatalogSnapshot:
    return CatalogSnapshot.model_validate_json(body)


def load_catalog(snapshot_id: str, *, index_path: Path, store=None) -> CatalogSnapshot:
    index_path = index_path.resolve()
    index = json.loads(index_path.read_text())
    entry = index["snapshots"].get(snapshot_id)
    if entry is None:
        if store is not None:
            return store.load_revision(snapshot_id)
        raise ValueError(f"Unknown catalog snapshot: {snapshot_id}")
    path = (index_path.parent / entry["path"]).resolve()
    if not path.is_relative_to(index_path.parent):
        raise ValueError("Catalog path leaves its index directory")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != entry["sha256"]:
        raise ValueError("Catalog content hash mismatch")
    catalog = _parse_snapshot(body).model_copy(deep=True)
    if catalog.snapshot_id != snapshot_id:
        raise ValueError("Catalog identity mismatch")
    return catalog


def resolve_id(value: str, collection: str, catalog: CatalogSnapshot) -> str:
    key = to_id(value)
    entries = getattr(catalog, collection)
    if key in entries:
        return key
    alias = catalog.aliases.get(key)
    if alias in entries:
        return alias
    for identifier, entry in entries.items():
        if to_id(entry.name) == key:
            return identifier
    return key


def validate_known_fields(
    tournament: ChampionsTournament, catalog: CatalogSnapshot
) -> NormalizationResult:
    result = tournament.model_copy(deep=True)
    meta = result.metadata
    diagnostics: list[Diagnostic] = []

    def diagnose(code: str, path: str, message: str):
        diagnostics.append(
            Diagnostic(code=code, path=path, message=message, evidence=catalog.evidence)
        )

    if (
        meta.catalog_revision != catalog.snapshot_id
        or meta.rules_id != catalog.rules_id
        or meta.game_data_revision != catalog.game_data_revision
    ):
        diagnose("catalog_mismatch", "metadata", "Event and catalog revisions differ")
    elif not catalog.coverage_complete or catalog.verification_status != "verified":
        diagnose("catalog_pending", "metadata", "Reference coverage has not been verified")
    elif meta.starts_at and (
        (catalog.effective_from and meta.starts_at < catalog.effective_from)
        or (catalog.effective_until and meta.starts_at >= catalog.effective_until)
    ):
        diagnose(
            "catalog_effective_interval", "metadata", "Event is outside the sourced rules interval"
        )
    if diagnostics:
        return NormalizationResult(
            admission_status="pending", tournament=result, diagnostics=diagnostics
        )

    for team_index, team in enumerate(result.teams):
        valid = []
        seen_species: set[str] = set()
        for slot, mon in enumerate(team.pokemon):
            path = f"teams.{team_index}.pokemon.{slot}"
            ref = catalog.species.get(mon.form_id or mon.species_id)
            if ref is None or ref.base_species_id != mon.species_id or ref.battle_only:
                diagnose(
                    "illegal_species", path, "Species/form is not a registered form in this catalog"
                )
                continue
            if mon.species_id in seen_species:
                diagnose(
                    "duplicate_species", path, "Repeated base species violates the roster clause"
                )
                continue
            seen_species.add(mon.species_id)
            if mon.ability_id is not None and mon.ability_id not in ref.ability_ids:
                diagnose(
                    "illegal_ability",
                    path + ".ability_id",
                    "Ability is incompatible with registered form",
                )
                mon.ability_id = None
            if mon.moves is not None:
                known = [
                    move for move in mon.moves if move in ref.move_ids and move in catalog.moves
                ]
                if len(known) != len(mon.moves):
                    diagnose(
                        "illegal_move",
                        path + ".moves",
                        "One or more moves are outside this learnset",
                    )
                    mon.moves = known or None
                    mon.moves_complete = False
            if mon.nature_id is not None and mon.nature_id not in catalog.natures:
                diagnose("unknown_nature", path + ".nature_id", "Nature is not in this catalog")
                mon.nature_id = None
            mon.mega_variant_ids = None if mon.item.state == "unknown" else []
            if mon.item.state == "held":
                item = catalog.items.get(mon.item.item_id)
                if item is None:
                    diagnose("illegal_item", path + ".item", "Item is not in this catalog")
                    mon.item = ItemSlot()
                    mon.mega_variant_ids = None
                elif item.mega_variant_id or item.mega_mappings:
                    # Some base forms share a species but only one can use this stone.
                    variant = item.mega_mappings.get(mon.form_id or mon.species_id)
                    if not variant and item.mega_species_id == (mon.form_id or mon.species_id):
                        variant = item.mega_variant_id
                    if not variant:
                        diagnose(
                            "incompatible_stone",
                            path + ".item",
                            "Held stone does not confer Mega capability on this registered form",
                        )
                        # Holding a legal stone is not evidence that the holder can Mega Evolve.
                        mon.mega_variant_ids = []
                    else:
                        mon.mega_variant_ids = [variant]
                        mon.field_evidence["mega_variant_ids"] = catalog.evidence
            valid.append(mon)
        if len(valid) != len(team.pokemon):
            team.publication_status = "partial"
        team.pokemon = valid
    return NormalizationResult(
        admission_status="verified", tournament=result, diagnostics=diagnostics
    )
