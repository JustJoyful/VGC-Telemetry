import pytest
from pydantic import ValidationError

from core.config import is_legal_mega_stone
from core.models.contracts import (
    CompetitivePokemon,
    EVSpread,
    TeamRoster,
    TournamentExport,
    TournamentMetadata,
)


def test_ev_spread_valid():
    evs = EVSpread(**{"hp": 252, "atk": 0, "def": 4, "spa": 252, "spd": 0, "spe": 0})
    assert evs.hp == 252
    assert evs.def_ == 4
    assert evs.canonical() == "252/0/4/252/0/0"


def test_ev_spread_stat_cap_exceeded():
    with pytest.raises(ValidationError, match="less than or equal to 252"):
        EVSpread(**{"hp": 253, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0})


def test_ev_spread_negative_stat():
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        EVSpread(**{"hp": -1, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0})


def test_ev_spread_sum_cap_exceeded():
    with pytest.raises(ValidationError, match="Total EVs"):
        EVSpread(**{"hp": 252, "atk": 252, "def": 8, "spa": 0, "spd": 0, "spe": 0})


def test_pokemon_tera_type_forbidden():
    payload = {
        "species": "Flutter Mane",
        "ability": "Protosynthesis",
        "item": "Booster Energy",
        "moves": ["Moonblast", "Shadow Ball", "Dazzling Gleam", "Protect"],
        "evs": {"hp": 116, "atk": 0, "def": 68, "spa": 116, "spd": 4, "spe": 204},
        "tera_type": "Fairy",  # Forbidden!
    }
    with pytest.raises(ValidationError):
        CompetitivePokemon(**payload)


def test_pokemon_moves_structural_validation():
    # Duplicate moves
    with pytest.raises(ValidationError, match="duplicate"):
        CompetitivePokemon(
            species="Incineroar",
            ability="Intimidate",
            item="Sitrus Berry",
            moves=["Fake Out", "Fake Out", "Parting Shot", "Knock Off"],
            evs=EVSpread(**{"hp": 252, "atk": 4, "def": 156, "spa": 0, "spd": 76, "spe": 20}),
        )

    # Empty moves list
    with pytest.raises(ValidationError):
        CompetitivePokemon(
            species="Incineroar",
            ability="Intimidate",
            item="Sitrus Berry",
            moves=[],
            evs=EVSpread(**{"hp": 252, "atk": 4, "def": 156, "spa": 0, "spd": 76, "spe": 20}),
        )

    # > 4 moves
    with pytest.raises(ValidationError):
        CompetitivePokemon(
            species="Incineroar",
            ability="Intimidate",
            item="Sitrus Berry",
            moves=["Move1", "Move2", "Move3", "Move4", "Move5"],
            evs=EVSpread(**{"hp": 252, "atk": 4, "def": 156, "spa": 0, "spd": 76, "spe": 20}),
        )

    # Divergent Champions move (Wave Crash on Swampert) is allowed structurally
    swampert = CompetitivePokemon(
        species="Swampert",
        ability="Torrent",
        item="Rindo Berry",
        moves=["Wave Crash", "Earthquake", "Ice Spinner", "Protect"],
        evs=EVSpread(**{"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0}),
    )
    assert "Wave Crash" in swampert.moves


def test_mega_stone_detection_and_z_evolution():
    # Verified standard Mega Stone
    salamence = CompetitivePokemon(
        species="Salamence",
        form="Mega",
        item="Salamencite",
        ability="Aerilate",
        moves=["Hyper Voice", "Draco Meteor", "Tailwind", "Protect"],
        evs=EVSpread(**{"hp": 4, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
    )
    assert salamence.item == "Salamencite"
    assert salamence.form == "Mega"

    # Verified Z Mega Stone
    lucario_z = CompetitivePokemon(
        species="Lucario",
        form="Mega-Z",
        item="Lucarionite Z",
        ability="Adaptability",
        moves=["Close Combat", "Flash Cannon", "Bullet Punch", "Protect"],
        evs=EVSpread(**{"hp": 0, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 252}),
    )
    assert lucario_z.item == "Lucarionite Z"
    assert lucario_z.form == "Mega-Z"

    # Substring check: White Herb and Eviolite must NOT be flagged as Mega Stones
    assert not is_legal_mega_stone("White Herb", "M-C")
    assert not is_legal_mega_stone("Eviolite", "M-C")


def test_team_roster_requires_six_pokemon():
    pokemon = CompetitivePokemon(
        species="Amoonguss",
        ability="Regenerator",
        item="Rocky Helmet",
        moves=["Spore", "Rage Powder", "Pollene Puff", "Protect"],
        evs=EVSpread(**{"hp": 252, "atk": 0, "def": 156, "spa": 0, "spd": 100, "spe": 0}),
    )
    # Only 1 pokemon instead of 6
    with pytest.raises(ValidationError, match="at least 6 items"):
        TeamRoster(
            player_id="player123",
            player_name="Champion Red",
            standing=1,
            record="7-1",
            pokemon=[pokemon],
        )


def test_tournament_metadata_and_export():
    meta = TournamentMetadata(
        tid="champions-cup-01",
        name="Champions Regional Cup",
        date="2026-09-15",
        regulation="M-C",
        tier="Regional",
        format="Pokémon Champions VGC",
    )
    assert meta.regulation == "M-C"

    # TournamentExport extra fields forbidden
    with pytest.raises(ValidationError):
        TournamentExport(
            metadata=meta,
            teams=[],
            matches=[],
            extra_field="disallowed",
        )
