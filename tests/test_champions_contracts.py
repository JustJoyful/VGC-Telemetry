import hashlib
from importlib import import_module
from importlib.util import find_spec

import pytest
from pydantic import ValidationError


def load_models():
    assert find_spec("core.models.champions"), "Native Champions contracts are missing"
    return import_module("core.models.champions")


def points(**changes):
    return {"hp": 32, "atk": 32, "def": 2, "spa": 0, "spd": 0, "spe": 0} | changes


def test_native_stat_points_and_json_alias():
    models = load_models()
    spread = models.StatPoints.model_validate(points())
    assert spread.canonical() == "32/32/2/0/0/0"
    assert spread.model_dump(by_alias=True)["def"] == 2


@pytest.mark.parametrize(
    "change", [{"hp": 33}, {"spa": 1}, {"hp": -1}, {"hp": True}, {"hp": 2.5}, {"hp": "2"}]
)
def test_native_stat_points_reject_bad_allocations(change):
    models = load_models()
    with pytest.raises(ValidationError):
        models.StatPoints.model_validate(points(**change))


def test_partial_build_round_trip_preserves_unknowns():
    models = load_models()
    mon = models.ChampionsPokemon(species_id="gengar")
    restored = models.ChampionsPokemon.model_validate_json(mon.model_dump_json())
    assert restored.stat_points is None
    assert restored.nature_id is None
    assert restored.moves is None
    assert restored.item.state == "unknown"
    assert restored.mega_variant_ids is None


def test_item_and_registered_mega_are_separate():
    models = load_models()
    mon = models.ChampionsPokemon(
        species_id="gengar", item={"state": "held", "item_id": "gengarite"}
    )
    assert mon.form_id is None
    assert models.ItemSlot(state="empty").item_id is None
    for value in [
        {"state": "held"},
        {"state": "empty", "item_id": "gengarite"},
        {"state": "unknown", "item_id": "gengarite"},
    ]:
        with pytest.raises(ValidationError):
            models.ItemSlot.model_validate(value)


@pytest.mark.parametrize(
    "extra",
    [
        {"moves": []},
        {"moves": ["protect", "protect"]},
        {"moves": ["a", "b", "c", "d", "e"]},
        {"moves": [""]},
        {"tera_type": "Ghost"},
        {"evs": points()},
    ],
)
def test_native_build_forbids_invalid_or_legacy_fields(extra):
    models = load_models()
    with pytest.raises(ValidationError):
        models.ChampionsPokemon(species_id="gengar", **extra)


def test_roster_and_private_build_completeness_are_independent():
    models = load_models()
    mons = [
        {"species_id": species}
        for species in ["gengar", "incineroar", "rillaboom", "pelipper", "sneasler", "dragonite"]
    ]
    team = models.TeamRosterV2(
        entry_id="limitless:event:player", publication_status="complete", pokemon=mons
    )
    assert all(mon.stat_points is None for mon in team.pokemon)
    assert models.TeamRosterV2(entry_id="entry").publication_status == "absent"
    with pytest.raises(ValidationError):
        models.TeamRosterV2(entry_id="entry", publication_status="complete", pokemon=mons[:1])
    with pytest.raises(ValidationError):
        models.TeamRosterV2(entry_id="entry", publication_status="absent", pokemon=mons)


def test_match_outcomes_do_not_fabricate_a_winner():
    models = load_models()
    match = models.MatchResultV2(match_id="match", outcome="tie", player1_id="a", player2_id="b")
    assert match.winner_id is None
    for changes in [
        {"outcome": "win", "winner_id": None},
        {"outcome": "win", "winner_id": "c"},
        {"outcome": "tie", "winner_id": "a"},
    ]:
        with pytest.raises(ValidationError):
            models.MatchResultV2(match_id="match", player1_id="a", player2_id="b", **changes)


def test_capture_checks_document_integrity():
    models = load_models()
    body = '{"game":"VGC"}'
    doc = {
        "url": "https://play.limitlesstcg.com/api/games",
        "content_type": "application/json",
        "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
    }
    assert models.CapturedDocument(**doc).body == body
    with pytest.raises(ValidationError):
        models.CapturedDocument(**(doc | {"body": "altered"}))


def test_verified_result_requires_identified_tournament():
    models = load_models()
    with pytest.raises(ValidationError):
        models.NormalizationResult(admission_status="verified")
    assert models.NormalizationResult(admission_status="pending").tournament is None


@pytest.mark.parametrize(
    "outcome,player2,winner",
    [
        ("bye", "b", "a"),
        ("automatic_loss", "b", None),
        ("tie", None, None),
        ("double_loss", None, None),
    ],
)
def test_result_kind_requires_corresponding_participant_count(outcome, player2, winner):
    from core.models.champions import MatchResultV2

    with pytest.raises(ValidationError):
        MatchResultV2(
            match_id="match", player1_id="a", player2_id=player2, winner_id=winner, outcome=outcome
        )
