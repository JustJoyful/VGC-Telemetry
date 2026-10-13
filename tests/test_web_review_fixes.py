# ruff: noqa: F811
from urllib.parse import parse_qs, urlencode, urlsplit

from tests.v2_helpers import v2_client  # noqa: F401
from tests.web_helpers import Document


def test_retryable_error_and_population_choices_retain_snapshot(v2_client):
    from starlette.requests import Request

    from app.v2.errors import V2Error
    from app.web.routes import error_page

    client, _, _, _ = v2_client
    token = client.get("/v2/meta?limit=1").json()["snapshot"]
    for code, status in [
        ("data_unavailable", 503),
        ("rate_limited", 429),
        ("query_too_broad", 422),
        ("ambiguous_population", 409),
    ]:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/explorer",
                "query_string": urlencode(
                    {"snapshot": token, "cursor": "retained", "limit": "1"}
                ).encode(),
                "headers": [],
            }
        )
        response = error_page(
            request,
            V2Error(
                status,
                code,
                "Try again",
                {"choices": [{"population_id": "chosen", "rules_id": "M-C"}]},
            ),
        )
        doc = Document(response.body.decode())
        link = doc.link("Recover query")
        assert parse_qs(urlsplit(link).query)["snapshot"] == [token]
        assert parse_qs(urlsplit(link).query)["cursor"] == ["retained"]
        if code == "ambiguous_population":
            choice = next(a["href"] for a, _ in doc.links if "population_id=chosen" in a["href"])
            assert parse_qs(urlsplit(choice).query)["snapshot"] == [token]
            assert "cursor" not in parse_qs(urlsplit(choice).query)


def test_overview_cursor_keeps_retained_snapshot_without_current(v2_client):
    from db.schema_public import public_state_key

    client, _, table, _ = v2_client
    page = client.get("/explorer?limit=1")
    values = parse_qs(urlsplit(Document(page.text).link("Show more Pokémon")).query)
    values.pop("snapshot")
    table.delete_item(Key=public_state_key())
    page = client.get("/explorer", params=[(k, v) for k, values in values.items() for v in values])
    assert page.status_code == 200 and "Show more Pokémon" in page.text
    assert "No published snapshot" not in page.text
    assert "How to read this" in page.text and "Unavailable" in page.text


def test_analytics_team_transitions_translate_mega_and_exact_population(v2_client):
    from app.db.public_queries import get_public_reader
    from app.main import app
    from app.v2.catalog import resolve_catalog

    client, _, _, _ = v2_client
    reader = app.dependency_overrides[get_public_reader]()
    catalog = resolve_catalog(reader.config.default_catalog_revision, config=reader.config)
    params = {
        "registered_mega_id": "floettemega",
        "game_data_revision": catalog.game_data_revision,
        "limit": 3,
    }
    for path in ["/explorer", "/explorer/pokemon/incineroar"]:
        page = client.get(path, params=params)
        assert page.status_code == 200
        doc = Document(page.text)
        labels = (
            ["Browse teams", "Show teams with this core"]
            if path == "/explorer"
            else ["Explore matching teams", "Explore core teams"]
        )
        for label in labels:
            links = [a["href"] for a, text in doc.links if label in text]
            assert links
            for link in links:
                query = parse_qs(urlsplit(link).query)
                assert query.get("mega_variant_id") == [
                    "floettemega"
                ] or "mega:floettemega" in query.get("members", [])
                assert query.get("population_id") and query.get("snapshot")
                assert "game_data_revision" not in query and "registered_mega_id" not in query
                assert query.get("publication_status") == ["complete"]
                teams = client.get(link)
                assert teams.status_code == 200, teams.text


def test_species_chip_removal_clears_only_dependent_form(v2_client):
    client, _, _, _ = v2_client
    for path in ["/explorer", "/explorer/teams"]:
        page = client.get(
            path,
            params={
                "species_id": "floette",
                "form_id": "floetteeternal",
                "regulation": "M-C",
                "limit": 3,
            },
        )
        assert page.status_code == 200
        link = next(
            a["href"]
            for t, a in Document(page.text).tags
            if a.get("data-remove-filter") == "species_id"
        )
        query = parse_qs(urlsplit(link).query)
        assert "species_id" not in query and "form_id" not in query
        assert query["regulation"] == ["M-C"] and query.get("snapshot")
        assert client.get(link).status_code == 200


def test_invalid_window_retains_editable_native_and_enhanced_filters(v2_client):
    client, _, _, _ = v2_client
    token = client.get("/v2/meta?limit=1").json()["snapshot"]
    params = {
        "date_from": "2026-01-01",
        "date_to": "2026-10-03",
        "regulation": "M-C",
        "snapshot": token,
        "limit": "3",
    }
    for headers in [{}, {"HX-Request": "true"}]:
        page = client.get("/explorer", params=params, headers=headers)
        assert page.status_code == 422
        doc = Document(page.text)
        fields = {a["name"]: a.get("value") for t, a in doc.tags if t == "input" and "name" in a}
        assert fields["date_from"] == params["date_from"] and fields["date_to"] == params["date_to"]
        assert fields["snapshot"] == token and fields["regulation"] == "M-C"
        assert any(
            t == "form" and a.get("method") == "get" and a.get("action") == "/explorer"
            for t, a in doc.tags
        )
        assert (
            client.get("/explorer", params=params | {"date_from": "2026-09-04"}).status_code == 200
        )


def test_invalid_cursor_discards_only_cursor_when_snapshot_is_valid(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/v2/teams?limit=1").json()
    error = client.get(
        "/explorer",
        params={"snapshot": page["snapshot"], "cursor": page["next_cursor"], "limit": 1},
    )
    assert error.status_code == 400
    retry = Document(error.text).link("Recover query")
    values = parse_qs(urlsplit(retry).query)
    assert values["snapshot"] == [page["snapshot"]] and "cursor" not in values
    assert client.get(retry).status_code == 200


def test_unpublished_analytics_only_scope_does_not_broaden_teams(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer?custom_rule=not-observed")
    assert page.status_code == 200
    assert "No matching population for teams" in page.text
    assert not any(text == "Browse teams" for _, text in Document(page.text).links)
