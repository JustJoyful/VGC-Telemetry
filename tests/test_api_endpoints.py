import base64

from fastapi.testclient import TestClient

from app.main import app
from db.schema import (
    moves_usage_sk,
    regulation_pk,
    species_usage_sk,
    team_gsi1,
    team_sk,
    tournament_pk,
)

client = TestClient(app)


def test_teams_endpoint_returns_teams_and_cursor(dynamodb_table):
    # Seed 2 teams for tournament T100
    dynamodb_table.put_item(
        Item={
            "PK": tournament_pk("T100"),
            "SK": team_sk("P001"),
            "player_id": "P001",
            "player_name": "Ash Ketchum",
            "standing": 1,
            "record": "7-0",
            "pokemon": [],
            "GSI1PK": team_gsi1("P001", "2026-10-01", "T100")[0],
            "GSI1SK": team_gsi1("P001", "2026-10-01", "T100")[1],
        }
    )
    dynamodb_table.put_item(
        Item={
            "PK": tournament_pk("T100"),
            "SK": team_sk("P002"),
            "player_id": "P002",
            "player_name": "Gary Oak",
            "standing": 2,
            "record": "6-1",
            "pokemon": [],
            "GSI1PK": team_gsi1("P002", "2026-10-01", "T100")[0],
            "GSI1SK": team_gsi1("P002", "2026-10-01", "T100")[1],
        }
    )

    response = client.get("/v1/teams?tournament_id=T100&limit=1")
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1
    assert body["pagination"]["limit"] == 1
    assert body["pagination"]["has_more"] is True
    assert body["pagination"]["next_cursor"] is not None

    # Fetch next page using next_cursor
    next_cursor = body["pagination"]["next_cursor"]
    response2 = client.get(f"/v1/teams?tournament_id=T100&limit=1&cursor={next_cursor}")
    assert response2.status_code == 200
    body2 = response2.json()
    assert len(body2["data"]) == 1
    assert body2["data"][0]["player_id"] != body["data"][0]["player_id"]


def test_teams_endpoint_invalid_cursor_returns_400():
    response = client.get("/v1/teams?tournament_id=T100&cursor=not_valid_base64!!!")
    assert response.status_code == 400
    assert "Invalid pagination cursor" in response.json()["detail"]

    # Valid base64 but not valid JSON
    bad_json_cursor = base64.urlsafe_b64encode(b"not json").decode("utf-8")
    response_bad_json = client.get(f"/v1/teams?tournament_id=T100&cursor={bad_json_cursor}")
    assert response_bad_json.status_code == 400
    assert "Invalid pagination cursor" in response_bad_json.json()["detail"]


def test_meta_endpoint_species_usage(dynamodb_table):
    dynamodb_table.put_item(
        Item={
            "PK": regulation_pk("M-C"),
            "SK": species_usage_sk("Swampert"),
            "species": "Swampert",
            "count": 42,
        }
    )

    response = client.get("/v1/meta?regulation=M-C&metric=usage&species=Swampert")
    assert response.status_code == 200
    body = response.json()
    assert body["regulation"] == "M-C"
    assert body["metric"] == "usage"
    assert body["data"]["count"] == 42


def test_meta_endpoint_moves_usage(dynamodb_table):
    dynamodb_table.put_item(
        Item={
            "PK": regulation_pk("M-C"),
            "SK": moves_usage_sk("Swampert"),
            "species": "Swampert",
            "moves": {"Wave Crash": 38, "Earthquake": 40, "Protect": 41, "Ice Spinner": 15},
        }
    )

    response = client.get("/v1/meta?regulation=M-C&metric=moves&species=Swampert")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["moves"]["Wave Crash"] == 38


def test_meta_endpoint_partner_bidirectional_prefix_query(dynamodb_table):
    # Seed bidirectional partner co-occurrences
    dynamodb_table.put_item(
        Item={
            "PK": regulation_pk("M-C"),
            "SK": "PARTNER#Swampert#Garchomp",
            "species": "Swampert",
            "partner": "Garchomp",
            "count": 28,
        }
    )
    dynamodb_table.put_item(
        Item={
            "PK": regulation_pk("M-C"),
            "SK": "PARTNER#Swampert#Zapdos",
            "species": "Swampert",
            "partner": "Zapdos",
            "count": 19,
        }
    )
    dynamodb_table.put_item(
        Item={
            "PK": regulation_pk("M-C"),
            "SK": "PARTNER#Garchomp#Swampert",
            "species": "Garchomp",
            "partner": "Swampert",
            "count": 28,
        }
    )

    # Query partners of Swampert (must return Garchomp and Zapdos, but NOT duplicate reverse)
    response = client.get("/v1/meta?regulation=M-C&metric=partner&species=Swampert")
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    partners = {item["partner"] for item in body["data"]}
    assert partners == {"Garchomp", "Zapdos"}


def test_player_history_endpoint(dynamodb_table):
    dynamodb_table.put_item(
        Item={
            "PK": tournament_pk("T101"),
            "SK": team_sk("P007"),
            "player_id": "P007",
            "player_name": "Cynthia",
            "standing": 1,
            "record": "8-0",
            "pokemon": [],
            "GSI1PK": team_gsi1("P007", "2026-10-15", "T101")[0],
            "GSI1SK": team_gsi1("P007", "2026-10-15", "T101")[1],
        }
    )

    response = client.get("/v1/players/P007")
    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == "P007"
    assert len(body["history"]) == 1
    assert body["history"][0]["standing"] == 1
