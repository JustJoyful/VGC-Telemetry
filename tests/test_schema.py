from db.client import batch_write_with_retry
from db.schema import (
    ability_usage_sk,
    evspread_usage_sk,
    match_sk,
    moves_usage_sk,
    partner_sk,
    rate_limit_pk,
    rate_limit_sk,
    regulation_gsi2,
    regulation_pk,
    species_usage_sk,
    status_sk,
    team_gsi1,
    team_sk,
    tournament_metadata_sk,
    tournament_pk,
)


def test_tournament_key_builders():
    assert tournament_pk("tid-100") == "TOURNAMENT#tid-100"
    assert tournament_metadata_sk() == "METADATA"
    assert team_sk("player_abc") == "TEAM#player_abc"
    assert match_sk(1, "match-999") == "MATCH#1#match-999"
    assert status_sk() == "STATUS"


def test_usage_key_builders():
    assert species_usage_sk("Incineroar") == "USAGE#Incineroar"
    assert moves_usage_sk("Incineroar") == "MOVES#Incineroar"
    assert ability_usage_sk("Incineroar") == "ABILITY#Incineroar"
    assert evspread_usage_sk("Incineroar") == "EVSPREAD#Incineroar"


def test_partner_canonical_ordering():
    # Alphabetical order: Amoonguss < Incineroar
    assert partner_sk("Incineroar", "Amoonguss") == "PARTNER#Amoonguss#Incineroar"
    assert partner_sk("Amoonguss", "Incineroar") == "PARTNER#Amoonguss#Incineroar"


def test_gsi_key_builders():
    gsi1_pk, gsi1_sk = team_gsi1("player_abc", "2026-09-15", "tid-100")
    assert gsi1_pk == "PLAYER#player_abc"
    assert gsi1_sk == "TOURNAMENT#2026-09-15#tid-100"

    gsi2_pk, gsi2_sk = regulation_gsi2("M-C", "2026-09-15", "tid-100")
    assert gsi2_pk == "REGULATION#M-C"
    assert gsi2_sk == "DATE#2026-09-15#tid-100"


def test_regulation_and_rate_limit_keys():
    assert regulation_pk("M-C") == "REGULATION#M-C"
    assert rate_limit_pk("192.168.1.1") == "RATELIMIT#192.168.1.1"
    assert rate_limit_sk(17000000) == "WINDOW#17000000"


def test_batch_write_with_retry(dynamodb_table):
    # Test batch writing 30 items (exceeding DynamoDB's 25-item single call limit)
    items = []
    for i in range(30):
        items.append(
            {
                "PK": tournament_pk("tid-test"),
                "SK": team_sk(f"player-{i:02d}"),
                "standing": i + 1,
            }
        )

    batch_write_with_retry(dynamodb_table, items, batch_size=25)

    # Verify all 30 items were written
    response = dynamodb_table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk_prefix)",
        ExpressionAttributeValues={
            ":pk": tournament_pk("tid-test"),
            ":sk_prefix": "TEAM#",
        },
    )
    assert response["Count"] == 30
