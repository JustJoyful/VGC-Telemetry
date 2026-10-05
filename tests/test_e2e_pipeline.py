import json
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.main import app
from app.middleware.rate_limit import DynamoDBRateLimitMiddleware
from db.schema import (
    regulation_pk,
    status_sk,
    tournament_pk,
)
from ingestion.handler import handle_s3_event


def test_full_pipeline_e2e(s3_bucket, dynamodb_table, monkeypatch):
    """Full End-to-End Integration Test:

    1. S3 raw tournament event ingestion
    2. Crash-safe DynamoDB transaction and bidirectional partner writes
    3. REST API queries (/health, /v1/teams, /v1/meta, /v1/players)
    4. Rate limit fail-open on throttle and 429 when exceeded
    5. S3 quarantine loop prevention
    """
    client = TestClient(app, client=("198.51.100.88", 40000))

    # -------------------------------------------------------------
    # 1. Ingestion: Upload valid tournament JSON to raw/
    # -------------------------------------------------------------
    tid = "T_E2E_2026"
    raw_key = f"raw/{tid}.json"
    tournament_payload = {
        "metadata": {
            "tid": tid,
            "name": "Indianapolis Champions Regional 2026",
            "date": "2026-10-18",
            "regulation": "M-C",
            "tier": "Regional",
            "format": "Pokémon Champions VGC",
        },
        "teams": [
            {
                "player_id": "P_E2E_1",
                "player_name": "Riley Reed",
                "standing": 1,
                "record": "8-1",
                "pokemon": [
                    {
                        "species": "Swampert",
                        "ability": "Torrent",
                        "moves": ["Wave Crash", "Earthquake", "Protect", "Ice Spinner"],
                        "evs": {"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0},
                    },
                    {
                        "species": "Incineroar",
                        "ability": "Intimidate",
                        "moves": ["Fake Out", "Parting Shot", "Knock Off", "Flare Blitz"],
                        "evs": {"hp": 252, "atk": 0, "def": 156, "spa": 0, "spd": 100, "spe": 0},
                    },
                    {
                        "species": "Amoonguss",
                        "ability": "Regenerator",
                        "moves": ["Spore", "Pollen Puff", "Rage Powder", "Protect"],
                        "evs": {"hp": 252, "atk": 0, "def": 156, "spa": 0, "spd": 100, "spe": 0},
                    },
                    {
                        "species": "Zapdos",
                        "ability": "Static",
                        "moves": ["Thunderbolt", "Roost", "Tailwind", "Volt Switch"],
                        "evs": {"hp": 252, "atk": 0, "def": 100, "spa": 100, "spd": 56, "spe": 0},
                    },
                    {
                        "species": "Rillaboom",
                        "ability": "Grassy Surge",
                        "moves": ["Grassy Glide", "Wood Hammer", "Fake Out", "High Horsepower"],
                        "evs": {"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0},
                    },
                    {
                        "species": "Garchomp",
                        "ability": "Rough Skin",
                        "moves": ["Earthquake", "Dragon Claw", "Protect", "Swords Dance"],
                        "evs": {"hp": 4, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252},
                    },
                ],
            },
            {
                "player_id": "P_E2E_2",
                "player_name": "Jordan Hayes",
                "standing": 2,
                "record": "7-2",
                "pokemon": [
                    {
                        "species": "Swampert",
                        "ability": "Torrent",
                        "moves": ["Wave Crash", "Protect", "Earthquake", "Yawn"],
                        "evs": {"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0},
                    },
                    {
                        "species": "Garchomp",
                        "ability": "Rough Skin",
                        "moves": ["Earthquake", "Protect", "Rock Slide", "Dragon Claw"],
                        "evs": {"hp": 4, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252},
                    },
                    {
                        "species": "Flutter Mane",
                        "ability": "Protosynthesis",
                        "moves": ["Moonblast", "Shadow Ball", "Dazzling Gleam", "Protect"],
                        "evs": {"hp": 116, "atk": 0, "def": 116, "spa": 140, "spd": 4, "spe": 132},
                    },
                    {
                        "species": "Chi-Yu",
                        "ability": "Beads of Ruin",
                        "moves": ["Heat Wave", "Dark Pulse", "Overheat", "Protect"],
                        "evs": {"hp": 4, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252},
                    },
                    {
                        "species": "Iron Hands",
                        "ability": "Quark Drive",
                        "moves": ["Fake Out", "Drain Punch", "Wild Charge", "Heavy Slam"],
                        "evs": {"hp": 140, "atk": 156, "def": 36, "spa": 0, "spd": 172, "spe": 4},
                    },
                    {
                        "species": "Ogerpon-Hearthflame",
                        "ability": "Mold Breaker",
                        "moves": ["Ivy Cudgel", "Horn Leech", "Follow Me", "Spiky Shield"],
                        "evs": {"hp": 252, "atk": 76, "def": 148, "spa": 0, "spd": 28, "spe": 4},
                    },
                ],
            },
        ],
        "matches": [],
    }

    s3_bucket.put_object(
        Key=raw_key,
        Body=json.dumps(tournament_payload).encode("utf-8"),
    )

    # Trigger ingestion Lambda handler
    s3_event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": s3_bucket.name},
                    "object": {"key": raw_key},
                }
            }
        ]
    }
    ingest_result = handle_s3_event(s3_event)
    assert ingest_result["status"] == "completed"
    assert ingest_result["results"][0]["status"] == "processed"

    # -------------------------------------------------------------
    # 2. Verify DynamoDB State & Bidirectional Partners
    # -------------------------------------------------------------
    # Check STATUS#PROCESSED
    status_item = dynamodb_table.get_item(Key={"PK": tournament_pk(tid), "SK": status_sk()}).get(
        "Item"
    )
    assert status_item is not None
    assert status_item["status"] == "PROCESSED"

    # Check bidirectional partner co-occurrences:
    # Team 1 has Swampert and Incineroar => PARTNER#Swampert#Incineroar
    # AND PARTNER#Incineroar#Swampert
    swampert_incin = dynamodb_table.get_item(
        Key={"PK": regulation_pk("M-C"), "SK": "PARTNER#Swampert#Incineroar"}
    ).get("Item")
    assert swampert_incin is not None
    assert swampert_incin["count"] == 1

    incin_swampert = dynamodb_table.get_item(
        Key={"PK": regulation_pk("M-C"), "SK": "PARTNER#Incineroar#Swampert"}
    ).get("Item")
    assert incin_swampert is not None
    assert incin_swampert["count"] == 1

    # Swampert and Garchomp appear in BOTH Team 1 and Team 2 => count must be 2
    swampert_garchomp = dynamodb_table.get_item(
        Key={"PK": regulation_pk("M-C"), "SK": "PARTNER#Swampert#Garchomp"}
    ).get("Item")
    assert swampert_garchomp is not None
    assert swampert_garchomp["count"] == 2

    # -------------------------------------------------------------
    # 3. REST API Queries
    # -------------------------------------------------------------
    # /health
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"
    assert health_resp.json()["regulation"] == "M-C"

    # /v1/teams
    teams_resp = client.get(f"/v1/teams?tournament_id={tid}&limit=10")
    assert teams_resp.status_code == 200
    teams_data = teams_resp.json()
    assert len(teams_data["data"]) == 2
    assert teams_data["data"][0]["player_name"] == "Riley Reed"

    # /v1/meta?metric=partner
    partner_resp = client.get("/v1/meta?regulation=M-C&metric=partner&species=Swampert")
    assert partner_resp.status_code == 200
    partners = partner_resp.json()["data"]
    partner_names = {p["partner"] for p in partners}
    assert "Incineroar" in partner_names
    assert "Garchomp" in partner_names

    # /v1/meta?metric=moves
    moves_resp = client.get("/v1/meta?regulation=M-C&metric=moves&species=Swampert")
    assert moves_resp.status_code == 200
    moves_data = moves_resp.json()["data"]["moves"]
    assert moves_data["Wave Crash"] == 2
    assert moves_data["Protect"] == 2

    # /v1/meta?metric=usage
    usage_resp = client.get("/v1/meta?regulation=M-C&metric=usage&species=Swampert")
    assert usage_resp.status_code == 200
    assert usage_resp.json()["data"]["count"] == 2

    # /v1/players/{id}
    player_resp = client.get("/v1/players/P_E2E_1")
    assert player_resp.status_code == 200
    assert player_resp.json()["player_id"] == "P_E2E_1"
    assert len(player_resp.json()["history"]) == 1
    assert player_resp.json()["history"][0]["standing"] == 1

    # -------------------------------------------------------------
    # 4. Rate Limiting Tests (Fail-Open & 429 Enforcement)
    # -------------------------------------------------------------
    rate_limiter = DynamoDBRateLimitMiddleware(app, table=dynamodb_table, rate_limit=2)

    # Verify throttling exception fails open
    throttled_mock = MagicMock(
        side_effect=ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
        )
    )
    monkeypatch.setattr(dynamodb_table, "update_item", throttled_mock)
    allowed, _, _ = rate_limiter.check_rate_limit("198.51.100.88")
    assert allowed is True, "Rate limiter must fail open when DynamoDB throttles"

    # Restore unthrottled and verify 429
    monkeypatch.undo()
    test_client_ip = "198.51.100.99"
    # First 2 allowed
    assert rate_limiter.check_rate_limit(test_client_ip)[0] is True
    assert rate_limiter.check_rate_limit(test_client_ip)[0] is True
    # 3rd request blocked
    allowed, remaining, _ = rate_limiter.check_rate_limit(test_client_ip)
    assert allowed is False
    assert remaining == 0

    # -------------------------------------------------------------
    # 5. S3 Quarantine & Loop Prevention
    # -------------------------------------------------------------
    bad_payload = b"not a valid json string {"
    s3_bucket.put_object(Key="raw/corrupt_tournament.json", Body=bad_payload)

    corrupt_event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": s3_bucket.name},
                    "object": {"key": "raw/corrupt_tournament.json"},
                }
            }
        ]
    }
    quarantine_result = handle_s3_event(corrupt_event)
    assert quarantine_result["results"][0]["status"] == "quarantined"

    # Verify corrupt payload was written to quarantine/
    quarantined_objs = list(s3_bucket.objects.filter(Prefix="quarantine/"))
    assert len(quarantined_objs) >= 1
    quarantined_key = quarantined_objs[0].key

    # Re-sending quarantine key MUST be ignored to guarantee zero trigger loop
    loop_event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": s3_bucket.name},
                    "object": {"key": quarantined_key},
                }
            }
        ]
    }
    loop_result = handle_s3_event(loop_event)
    assert loop_result["status"] == "skipped"
    assert loop_result["reason"] == "quarantine_prefix_ignored"
