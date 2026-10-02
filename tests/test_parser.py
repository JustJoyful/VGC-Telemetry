import pytest

from core.models.contracts import TournamentExport
from ingestion.parser import parse_tournament_export


def create_sample_team(player_id: str, standing: int, species_list: list[str]) -> dict:
    pokemon = []
    for sp in species_list:
        p_data = {
            "species": sp,
            "ability": "Torrent" if sp == "Swampert" else "Intimidate",
            "moves": ["Protect", "Tackle"],
            "evs": {
                "hp": 252,
                "atk": 252,
                "def": 4,
                "spa": 0,
                "spd": 0,
                "spe": 0,
            },
        }
        if sp == "Absol":
            p_data["item"] = "Absolite Z"
            p_data["form"] = "Mega-Z"
        pokemon.append(p_data)

    return {
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "standing": standing,
        "record": "5-0",
        "pokemon": pokemon,
    }


@pytest.fixture
def sample_tournament_payload() -> dict:
    species = ["Swampert", "Incineroar", "Amoonguss", "Zapdos", "Rillaboom", "Absol"]
    return {
        "metadata": {
            "tid": "T1001",
            "name": "Champions Regional 2026",
            "date": "2026-09-20",
            "regulation": "M-C",
            "tier": "Regional",
            "format": "Pokémon Champions VGC",
        },
        "teams": [
            create_sample_team("P1", 1, species),
            create_sample_team(
                "P2", 2, ["Swampert", "Garchomp", "Amoonguss", "Zapdos", "Rillaboom", "Incineroar"]
            ),
        ],
        "matches": [
            {
                "round": 1,
                "match_id": "M1",
                "player1_id": "P1",
                "player2_id": "P2",
                "winner_id": "P1",
                "score": "2-1",
            }
        ],
    }


def test_parse_tournament_export_aggregates(sample_tournament_payload):
    result = parse_tournament_export(sample_tournament_payload)

    assert isinstance(result.export, TournamentExport)
    assert result.export.metadata.tid == "T1001"

    # Species counts: Swampert on both teams
    assert result.species_counts["Swampert"] == 2
    assert result.species_counts["Absol"] == 1
    assert result.species_counts["Garchomp"] == 1

    # Moves counts
    assert result.moves_counts["Swampert"]["Protect"] == 2
    assert result.moves_counts["Swampert"]["Tackle"] == 2

    # Ability counts
    assert result.ability_counts["Swampert"]["Torrent"] == 2

    # EV spread counts (252/252/4/0/0/0)
    assert result.evspread_counts["Swampert"]["252/252/4/0/0/0"] == 2

    # Bidirectional partner counts:
    # On Team 1: Swampert and Incineroar co-occur
    # On Team 2: Swampert and Incineroar co-occur
    assert result.partner_counts[("Swampert", "Incineroar")] == 2
    assert result.partner_counts[("Incineroar", "Swampert")] == 2

    # Swampert and Absol co-occur only on Team 1
    assert result.partner_counts[("Swampert", "Absol")] == 1
    assert result.partner_counts[("Absol", "Swampert")] == 1

    # Swampert and Garchomp co-occur only on Team 2
    assert result.partner_counts[("Swampert", "Garchomp")] == 1
    assert result.partner_counts[("Garchomp", "Swampert")] == 1

    # Verify each 6-pokemon team produces 15 * 2 = 30 directed pairs
    # Team 1 has 30 pairs, Team 2 has 30 pairs.
    assert sum(result.partner_counts.values()) == 60


def test_parse_tournament_export_builds_rows(sample_tournament_payload):
    result = parse_tournament_export(sample_tournament_payload)

    # 2 team rows
    assert len(result.team_rows) == 2
    p1_row = next(r for r in result.team_rows if r["SK"] == "TEAM#P1")
    assert p1_row["PK"] == "TOURNAMENT#T1001"
    assert p1_row["GSI1PK"] == "PLAYER#P1"
    assert p1_row["GSI1SK"] == "TOURNAMENT#2026-09-20#T1001"

    # 1 match row
    assert len(result.match_rows) == 1
    m1_row = result.match_rows[0]
    assert m1_row["PK"] == "TOURNAMENT#T1001"
    assert m1_row["SK"] == "MATCH#1#M1"
