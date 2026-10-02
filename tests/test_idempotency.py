import pytest

from db.schema import (
    regulation_pk,
    species_usage_sk,
    status_sk,
    tournament_pk,
)
from ingestion.parser import parse_tournament_export
from ingestion.writer import write_tournament_data


@pytest.fixture
def tournament_data():
    return {
        "metadata": {
            "tid": "T2001",
            "name": "Midwest Champions 2026",
            "date": "2026-09-21",
            "regulation": "M-C",
            "tier": "Regional",
            "format": "Pokémon Champions VGC",
        },
        "teams": [
            {
                "player_id": "P_IDEM_1",
                "player_name": "Idem Player 1",
                "standing": 1,
                "record": "5-0",
                "pokemon": [
                    {
                        "species": "Swampert",
                        "ability": "Torrent",
                        "moves": ["Wave Crash", "Protect"],
                        "evs": {"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0},
                    },
                    {
                        "species": "Incineroar",
                        "ability": "Intimidate",
                        "moves": ["Fake Out", "Parting Shot"],
                        "evs": {"hp": 252, "atk": 0, "def": 100, "spa": 0, "spd": 156, "spe": 0},
                    },
                    {
                        "species": "Amoonguss",
                        "ability": "Regenerator",
                        "moves": ["Spore", "Pollen Puff"],
                        "evs": {"hp": 252, "atk": 0, "def": 156, "spa": 0, "spd": 100, "spe": 0},
                    },
                    {
                        "species": "Zapdos",
                        "ability": "Static",
                        "moves": ["Thunderbolt", "Roost"],
                        "evs": {"hp": 252, "atk": 0, "def": 100, "spa": 100, "spd": 56, "spe": 0},
                    },
                    {
                        "species": "Rillaboom",
                        "ability": "Grassy Surge",
                        "moves": ["Grassy Glide", "Wood Hammer"],
                        "evs": {"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0},
                    },
                    {
                        "species": "Garchomp",
                        "ability": "Rough Skin",
                        "moves": ["Earthquake", "Dragon Claw"],
                        "evs": {"hp": 4, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252},
                    },
                ],
            }
        ],
        "matches": [],
    }


def test_first_run_ingests_and_sets_status_processed(dynamodb_table, tournament_data):
    parsed = parse_tournament_export(tournament_data)
    success = write_tournament_data(dynamodb_table, parsed)
    assert success is True

    # Check STATUS item
    status_item = dynamodb_table.get_item(
        Key={"PK": tournament_pk("T2001"), "SK": status_sk()}
    ).get("Item")
    assert status_item is not None
    assert status_item["status"] == "PROCESSED"

    # Check Regulation species usage count
    reg_usage = dynamodb_table.get_item(
        Key={"PK": regulation_pk("M-C"), "SK": species_usage_sk("Swampert")}
    ).get("Item")
    assert reg_usage is not None
    assert reg_usage["count"] == 1
    assert "T2001" in reg_usage["processed_tids"]


def test_rerun_after_processed_is_blocked_no_op(dynamodb_table, tournament_data):
    parsed = parse_tournament_export(tournament_data)

    # First run succeeds
    assert write_tournament_data(dynamodb_table, parsed) is True

    # Second run is blocked because status is already PROCESSED
    assert write_tournament_data(dynamodb_table, parsed) is False

    # Check Regulation species usage count is STILL 1, not 2!
    reg_usage = dynamodb_table.get_item(
        Key={"PK": regulation_pk("M-C"), "SK": species_usage_sk("Swampert")}
    )["Item"]
    assert reg_usage["count"] == 1


def test_crash_recovery_from_processing_status(dynamodb_table, tournament_data):
    # Simulate a crashed run that wrote STATUS#PROCESSING but crashed before completion
    dynamodb_table.put_item(
        Item={
            "PK": tournament_pk("T2001"),
            "SK": status_sk(),
            "status": "PROCESSING",
        }
    )

    parsed = parse_tournament_export(tournament_data)

    # Crash recovery run should succeed because status is NOT 'PROCESSED'
    assert write_tournament_data(dynamodb_table, parsed) is True

    # Status should now be PROCESSED
    status_item = dynamodb_table.get_item(Key={"PK": tournament_pk("T2001"), "SK": status_sk()})[
        "Item"
    ]
    assert status_item["status"] == "PROCESSED"

    # Count must be 1
    reg_usage = dynamodb_table.get_item(
        Key={"PK": regulation_pk("M-C"), "SK": species_usage_sk("Swampert")}
    )["Item"]
    assert reg_usage["count"] == 1
