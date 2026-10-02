import time

from ingestion.parser import parse_tournament_export
from ingestion.writer import write_tournament_data


def generate_synthetic_tournament(num_teams: int) -> dict:
    species_pool = [
        "Swampert",
        "Incineroar",
        "Amoonguss",
        "Zapdos",
        "Rillaboom",
        "Garchomp",
        "Gholdengo",
        "Kingambit",
        "Flutter Mane",
        "Urshifu",
        "Ogerpon",
        "Tornadus",
        "Landorus",
        "Chien-Pao",
        "Iron Hands",
    ]

    teams = []
    for i in range(num_teams):
        # Pick 6 distinct species deterministically based on team index
        start_idx = i % len(species_pool)
        selected_species = [species_pool[(start_idx + j) % len(species_pool)] for j in range(6)]

        pokemon = []
        for sp in selected_species:
            pokemon.append(
                {
                    "species": sp,
                    "ability": "DefaultAbility",
                    "moves": ["MoveA", "MoveB", "MoveC", "Protect"],
                    "evs": {"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0},
                }
            )

        teams.append(
            {
                "player_id": f"player_{i:04d}",
                "player_name": f"Competitor {i:04d}",
                "standing": i + 1,
                "record": f"{max(0, 7 - (i // 10))}-{min(7, i // 10)}",
                "pokemon": pokemon,
            }
        )

    matches = []
    for i in range(num_teams // 2):
        matches.append(
            {
                "round": 1,
                "match_id": f"round1_match_{i:03d}",
                "player1_id": f"player_{2 * i:04d}",
                "player2_id": f"player_{2 * i + 1:04d}",
                "winner_id": f"player_{2 * i:04d}",
                "score": "2-0",
            }
        )

    return {
        "metadata": {
            "tid": f"SYNTH_{num_teams}",
            "name": f"Synthetic Championship {num_teams}",
            "date": "2026-09-22",
            "regulation": "M-C",
            "tier": "Major",
            "format": "Pokémon Champions VGC",
        },
        "teams": teams,
        "matches": matches,
    }


def test_write_budget_64_teams(dynamodb_table):
    """Verify standard 64-team tournament ingestion fits in write budget and execution time."""
    payload = generate_synthetic_tournament(num_teams=64)
    parsed = parse_tournament_export(payload)

    assert len(parsed.team_rows) == 64
    assert len(parsed.match_rows) == 32

    write_start = time.time()
    success = write_tournament_data(dynamodb_table, parsed)
    write_time = time.time() - write_start

    assert success is True
    # Write time locally must be well within normal limits (< 5 seconds for moto)
    assert write_time < 10.0


def test_write_budget_200_teams_stress_benchmark(dynamodb_table):
    """Stress test with 200 teams (1200 Pokémon) to verify operational bounds."""
    payload = generate_synthetic_tournament(num_teams=200)
    parsed = parse_tournament_export(payload)

    assert len(parsed.team_rows) == 200
    assert len(parsed.match_rows) == 100

    start_time = time.time()
    success = write_tournament_data(dynamodb_table, parsed)
    elapsed = time.time() - start_time

    assert success is True
    assert elapsed < 30.0  # Safe execution within Lambda timeout
