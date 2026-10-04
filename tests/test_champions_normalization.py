import importlib
import importlib.util
from datetime import timedelta

from tests.source_helpers import real_inputs
from tests.test_limitless_champions import changed


def api():
    assert importlib.util.find_spec("ingestion.normalize"), "Normalization dispatcher is missing"
    return importlib.import_module("ingestion.normalize")


def test_independently_valid_entries_and_fields_survive():
    c, a, k = real_inputs("limitless")
    rows = [
        {
            "player": "good",
            "decklist": [
                {
                    "id": "gengar",
                    "ability": 12,
                    "attacks": ["Protect"],
                    "nature": "Modest",
                    "evs": {"hp": 32},
                }
            ],
        },
        {"player": {"id": "bad"}},
        "malformed",
    ]
    result = api().normalize_capture(changed(c, "standings", rows), admission=a, catalog=k)
    assert len(result.tournament.teams) == 1
    mon = result.tournament.teams[0].pokemon[0]
    assert mon.species_id == "gengar" and mon.ability_id is None
    assert mon.moves == ["protect"] and mon.stat_points is None
    assert {"invalid_attribute", "invalid_entry", "unsupported_legacy_fields"} <= {
        d.code for d in result.diagnostics
    }


def test_private_unknowns_and_native_allocations():
    c, a, k = real_inputs("limitless")
    roster = [
        {"id": i} for i in ["gengar", "incineroar", "rillaboom", "politoed", "kommoo", "dragonite"]
    ]
    roster[0]["stat_points"] = {"hp": 32, "atk": 0, "def": 2, "spa": 32, "spd": 0, "spe": 0}
    r = api().normalize_capture(
        changed(c, "standings", [{"player": "a", "decklist": roster}, {"player": "b"}]),
        admission=a,
        catalog=k,
    )
    first, second = r.tournament.teams
    assert first.publication_status == "complete" and second.publication_status == "absent"
    assert first.pokemon[0].stat_points.canonical() == "32/0/2/32/0/0"
    assert all(p.stat_points is None for p in first.pokemon[1:])
    assert all(
        p.item.state == "unknown" and p.nature_id is None and p.moves is None for p in first.pokemon
    )


def test_semantic_hash_ignores_capture_provenance_but_tracks_build_and_catalog():
    c, a, k = real_inputs("limitless")
    module = api()
    original = module.normalize_capture(c, admission=a, catalog=k).tournament
    later = module.normalize_capture(
        c.model_copy(update={"fetched_at": c.fetched_at + timedelta(days=1)}),
        admission=a,
        catalog=k,
    ).tournament
    assert module.normalized_content_hash(original) == module.normalized_content_hash(later)
    assert original.model_dump() != later.model_dump()
    for field, value in [("nature_id", "timid"), ("moves", ["protect"]), ("species_id", "other")]:
        modified = original.model_copy(deep=True)
        setattr(modified.teams[0].pokemon[0], field, value)
        assert module.normalized_content_hash(original) != module.normalized_content_hash(modified)
    modified = original.model_copy(deep=True)
    modified.metadata.catalog_revision = "other"
    assert module.normalized_content_hash(original) != module.normalized_content_hash(modified)


def test_unknown_catalog_is_pending_without_erasing_species():
    c, a, k = real_inputs("limitless")
    result = api().normalize_capture(
        c, admission=a, catalog=k.model_copy(update={"coverage_complete": False})
    )
    assert result.admission_status == "pending" and len(result.tournament.teams) == 31
