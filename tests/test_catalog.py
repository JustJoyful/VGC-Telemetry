import importlib
import importlib.util
import json

import pytest

from core.models.champions import ChampionsTournament
from tests.champions_helpers import catalog_data


def modules():
    assert importlib.util.find_spec("core.catalog"), "Catalog validation is missing"
    return importlib.import_module("core.catalog"), importlib.import_module("core.models.catalog")


def tournament(pokemon):
    return ChampionsTournament(
        metadata={
            "event_id": "event",
            "game_id": "pokemon-champions",
            "battle_format": "doubles",
            "rules_id": "M-C",
            "catalog_revision": "test-mc",
            "game_data_revision": "test-revision",
        },
        teams=[{"entry_id": "a", "publication_status": "partial", "pokemon": pokemon}],
    )


def test_legal_base_form_derives_mega_capability():
    api, models = modules()
    result = api.validate_known_fields(
        tournament(
            [
                {
                    "species_id": "gengar",
                    "ability_id": "cursedbody",
                    "moves": ["protect"],
                    "item": {"state": "held", "item_id": "gengarite"},
                }
            ]
        ),
        models.CatalogSnapshot(**catalog_data()),
    )
    assert result.admission_status == "verified"
    mon = result.tournament.teams[0].pokemon[0]
    assert mon.form_id is None and mon.mega_variant_ids == ["gengarmega"]
    assert result.diagnostics == []


def test_invalid_attribute_does_not_erase_valid_species():
    api, models = modules()
    result = api.validate_known_fields(
        tournament(
            [
                {
                    "species_id": "gengar",
                    "ability_id": "shadowtag",
                    "moves": ["fakeout"],
                    "nature_id": "madeup",
                }
            ]
        ),
        models.CatalogSnapshot(**catalog_data()),
    )
    mon = result.tournament.teams[0].pokemon[0]
    assert mon.species_id == "gengar"
    assert mon.ability_id is None and mon.moves is None and mon.nature_id is None
    assert {d.code for d in result.diagnostics} == {
        "illegal_ability",
        "illegal_move",
        "unknown_nature",
    }


def test_removed_species_is_diagnosed_without_dropping_other_entrants():
    api, models = modules()
    result = api.validate_known_fields(
        tournament([{"species_id": "gengar"}, {"species_id": "mewtwo"}]),
        models.CatalogSnapshot(**catalog_data()),
    )
    assert [p.species_id for p in result.tournament.teams[0].pokemon] == ["gengar"]
    assert result.diagnostics[0].code == "illegal_species"


def test_incomplete_or_unverified_catalog_remains_pending():
    api, models = modules()
    for changes in [{"coverage_complete": False}, {"verification_status": "pending"}]:
        result = api.validate_known_fields(
            tournament([{"species_id": "mewtwo"}]),
            models.CatalogSnapshot(**(catalog_data() | changes)),
        )
        assert result.admission_status == "pending"
        assert result.tournament.teams[0].pokemon[0].species_id == "mewtwo"
        assert all(d.code != "illegal_species" for d in result.diagnostics)


def test_snapshot_mismatch_is_not_silently_reinterpreted():
    api, models = modules()
    data = catalog_data() | {"snapshot_id": "other", "rules_id": "M-B"}
    result = api.validate_known_fields(
        tournament([{"species_id": "gengar"}]), models.CatalogSnapshot(**data)
    )
    assert result.admission_status == "pending"
    assert result.diagnostics[0].code == "catalog_mismatch"


def test_wrong_mega_stone_is_diagnosed():
    api, models = modules()
    data = catalog_data()
    data["items"]["gengarite"]["mega_species_id"] = "charizard"
    result = api.validate_known_fields(
        tournament([{"species_id": "gengar", "item": {"state": "held", "item_id": "gengarite"}}]),
        models.CatalogSnapshot(**data),
    )
    assert result.tournament.teams[0].pokemon[0].item.item_id == "gengarite"
    assert result.tournament.teams[0].pokemon[0].mega_variant_ids == []
    assert result.diagnostics[0].code == "incompatible_stone"


def test_catalog_loader_verifies_index_hash_and_identity(tmp_path):
    import hashlib

    api, _ = modules()
    data = catalog_data()
    body = json.dumps(data).encode()
    (tmp_path / "snapshot.json").write_bytes(body)
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "snapshots": {
                    "test-mc": {"path": "snapshot.json", "sha256": hashlib.sha256(body).hexdigest()}
                }
            }
        )
    )
    assert api.load_catalog("test-mc", index_path=tmp_path / "index.json").rules_id == "M-C"
    (tmp_path / "snapshot.json").write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        api.load_catalog("test-mc", index_path=tmp_path / "index.json")


def test_real_resolved_catalogs_cover_current_and_prior_regulations():
    from pathlib import Path

    from core.catalog import load_catalog

    index = Path("core/regulations/v2/index.json")
    assert index.exists(), "Resolved source-backed catalogs are missing"
    current = load_catalog("champions-mc-3661ce4", index_path=index)
    prior = load_catalog("champions-mb-3661ce4", index_path=index)
    assert current.coverage_complete and prior.coverage_complete
    assert current.upstream_revision == "3661ce40bf9001d185ce078b8920e12304204609"
    assert len(current.species) == 390 and len(prior.species) == 355
    assert len(current.items) == 166 and len(prior.items) == 148
    assert len(current.natures) == len(prior.natures) == 25
    assert set(prior.species) != set(current.species)
    assert "protect" in current.species["gengar"].move_ids
    assert current.species["gengar"].ability_ids == ["cursedbody"]
    assert current.items["floettite"].mega_species_id == "floetteeternal"
    assert current.items["floettite"].mega_variant_id == "floettemega"
    assert current.species["gengarmega"].battle_only
    for catalog in [current, prior]:
        assert catalog.source_kind == "community"
        for species in catalog.species.values():
            assert set(species.move_ids) <= catalog.moves.keys()
            assert set(species.ability_ids) <= catalog.abilities.keys()
        for item in catalog.items.values():
            if item.mega_variant_id:
                assert item.mega_species_id in catalog.species
                assert item.mega_variant_id in catalog.species


def test_stone_links_resolve_each_registered_form():
    api, models = modules()
    data = catalog_data()
    data["items"]["gengarite"] = {
        "name": "Test shared stone",
        "mega_mappings": {"gengar": "gengarmega"},
    }
    result = api.validate_known_fields(
        tournament([{"species_id": "gengar", "item": {"state": "held", "item_id": "gengarite"}}]),
        models.CatalogSnapshot(**data),
    )
    assert result.tournament.teams[0].pokemon[0].mega_variant_ids == ["gengarmega"]
