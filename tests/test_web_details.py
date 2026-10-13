# ruff: noqa: F811
import pytest

from tests.v2_helpers import v2_client  # noqa: F401
from tests.web_helpers import Document


def test_pokemon_detail_uses_returned_usage_coverage_and_provenance(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer/pokemon/floetteeternal")
    assert page.status_code == 200
    assert "Floette" in page.text and "champions-mc-3661ce4" in page.text
    assert "Eligible complete rosters" in page.text
    for metric in ["move", "ability", "item", "nature"]:
        result = client.get(
            "/v2/meta",
            params={"metric": metric, "species_id": "floette", "form_id": "floetteeternal"},
        ).json()
        for row in result["items"]:
            if row["value"]:
                assert row["value"] in page.text
    assert "Reference abilities" in page.text and "Observed abilities" in page.text
    assert "Unknown" in page.text and "Known slots" in page.text
    assert "Source evidence" in page.text
    assert client.get(Document(page.text).link("Explore matching teams")).status_code == 200


def test_pokemon_detail_distinguishes_unknown_zero_and_ambiguous_form(v2_client):
    client, _, _, _ = v2_client
    unknown = client.get("/explorer/pokemon/incineroar")
    assert unknown.status_code == 200 and "Unknown" in unknown.text
    zero = client.get("/explorer/pokemon/floetteeternal?metric=move&move_id=thunderbolt")
    assert zero.status_code == 200
    assert "0 of 1" in zero.text and "0.0%" in zero.text
    forms = client.get("/explorer/pokemon/Rotom")
    assert forms.status_code == 200 and "Choose an exact form" in forms.text
    assert "rotomwash" in forms.text and "rotomheat" in forms.text


@pytest.mark.parametrize(
    "publication,eligibility,observed",
    [("absent", "unknown", 0), ("partial", "excluded", 3), ("complete", "included", 6)],
)
def test_team_detail_shows_publication_eligibility_unknowns_and_sources(
    v2_client, publication, eligibility, observed
):
    from app.v2.cursors import decode_snapshot
    from db.schema_public import snapshot_row_key

    client, _, table, _ = v2_client
    original = client.get("/v2/teams?limit=1").json()
    team = original["items"][0]
    snapshot = decode_snapshot(original["snapshot"], now=100, secret=b"test-only-config-secret" * 2)
    key = snapshot_row_key(snapshot.snapshot_id, "TEAM#" + team["team_id"], "DETAIL")
    row = table.get_item(Key=key)["Item"]
    row["data"]["publication_status"] = publication
    row["data"]["eligibility"] = {
        "status": eligibility,
        "reason": "Synthetic state acceptance fixture",
        "evidence": [],
    }
    row["data"]["pokemon"] = row["data"]["pokemon"][:observed]
    row["data"]["player_name"] = "<script>private()</script>"
    table.put_item(Item=row)
    response = client.get(
        "/explorer/teams/" + team["team_id"], params={"snapshot": original["snapshot"]}
    )
    assert response.status_code == 200
    assert f"Roster: {publication.capitalize()}" in response.text
    assert f"Eligibility: {eligibility.capitalize()}" in response.text
    assert "<script>private()</script>" not in response.text
    assert "private()" not in response.text
    roster = [attrs for tag, attrs in Document(response.text).tags if "data-roster-member" in attrs]
    assert len(roster) == observed
    assert "Not published" in response.text if observed == 0 else "Unknown" in response.text
    assert "Source evidence" in response.text and original["snapshot"] in response.text


def test_team_detail_describes_mega_as_registered_capability(v2_client):
    client, _, _, _ = v2_client
    team = client.get("/v2/teams?limit=1").json()["items"][0]
    page = client.get("/explorer/teams/" + team["team_id"])
    assert page.status_code == 200
    assert "Registered Mega capability" in page.text
    assert "floettemega" in page.text
    assert "Mega activated" not in page.text and "battle activation is not observed" in page.text
    assert "Stat Points" in page.text and "Unknown" in page.text


def test_exact_sprite_forms_and_unmapped_fallback(v2_client):
    client, _, _, _ = v2_client
    mega = client.get("/explorer/pokemon/garchompmegaz")
    assert mega.status_code == 200
    assert "/2ecb4eeacd5a1718621fc30f12772e3f60d830b9/sprites/pokemon/10309.png" in mega.text
    assert "/445.png" not in mega.text
    assert "Registered capability" in mega.text


def test_untrusted_source_schemes_are_not_clickable(v2_client):
    from db.schema_public import public_state_key, snapshot_row_key

    client, _, table, _ = v2_client
    first = client.get("/v2/teams?limit=1").json()["items"][0]
    snapshot_id = table.get_item(Key=public_state_key())["Item"]["snapshot_id"]
    row = table.get_item(Key=snapshot_row_key(snapshot_id, "TEAM#" + first["team_id"], "DETAIL"))[
        "Item"
    ]
    row["data"]["provenance"][0]["url"] = "javascript:alert(1)"
    table.put_item(Item=row)
    page = client.get("/explorer/teams/" + first["team_id"])
    assert page.status_code == 200
    assert 'href="javascript:' not in page.text
    assert "Source evidence URL unavailable" in page.text


def test_metric_provenance_preserves_data_and_reference_token_domains(v2_client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from app.db.public_queries import get_public_reader
    from app.main import app
    from app.v2.config import get_v2_config
    from app.v2.cursors import decode_snapshot

    client, _, _, _ = v2_client
    config = app.dependency_overrides[get_public_reader]().config
    monkeypatch.setitem(app.dependency_overrides, get_v2_config, lambda: config)
    page = client.get("/explorer/pokemon/floetteeternal")
    links = [
        attrs["href"] for attrs, label in Document(page.text).links if label == "Metric provenance"
    ]
    assert links
    for link in links:
        path = urlsplit(link).path
        token = parse_qs(urlsplit(link).query)["snapshot"][0]
        info = decode_snapshot(token, now=100, secret=b"test-only-config-secret" * 2)
        assert info.snapshot_id.startswith("reference:") == path.startswith("/v2/pokemon")
        assert client.get(link).status_code == 200
