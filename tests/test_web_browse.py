# ruff: noqa: F811
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from tests.v2_helpers import v2_client  # noqa: F401
from tests.web_helpers import Document


def test_filter_submission_and_chip_removal_preserve_other_query_values(v2_client):
    client, value, _, _ = v2_client
    response = client.get(
        "/explorer/teams",
        params=[
            ("tournament_id", value.event_id),
            ("members", "form:incineroar"),
            ("members", "form:rillaboom"),
            ("limit", "1"),
        ],
    )
    assert response.status_code == 200
    doc = Document(response.text)
    chip = next(
        attrs["href"]
        for tag, attrs in doc.tags
        if attrs.get("data-remove-filter") == "members:form:incineroar"
    )
    query = parse_qs(urlsplit(chip).query)
    assert query["members"] == ["form:rillaboom"]
    assert query["tournament_id"] == [value.event_id] and query["limit"] == ["1"]
    assert "cursor" not in query and "snapshot" in query
    assert client.get(chip).status_code == 200

    updated = client.get(
        "/explorer/teams/feed",
        params={"members": "form:incineroar", "tournament_id": value.event_id},
        headers={"HX-Request": "true"},
    )
    updated_doc = Document(updated.text)
    assert any(
        attrs.get("id") == "team-filter-chips" and attrs.get("hx-swap-oob") == "outerHTML"
        for tag, attrs in updated_doc.tags
    )
    removals = [attrs for tag, attrs in updated_doc.tags if "data-remove-filter" in attrs]
    assert not any(a["data-remove-filter"] == "members:form:rillaboom" for a in removals)
    remaining = next(a for a in removals if a["data-remove-filter"] == "members:form:incineroar")
    assert "members" not in parse_qs(urlsplit(remaining["href"]).query)


def test_filter_form_works_without_htmx_and_returns_partial_with_htmx(v2_client):
    client, _, _, _ = v2_client
    path = "/explorer/teams?species_id=incineroar&limit=1"
    full = client.get(path)
    assert full.status_code == 200 and "<html" in full.text
    assert 'method="get"' in full.text and 'action="/explorer/teams"' in full.text
    partial = client.get(path, headers={"HX-Request": "true"})
    assert partial.status_code == 200 and "<html" not in partial.text
    assert 'id="results"' in partial.text and 'class="team-feed"' in partial.text
    restored = client.get(
        path, headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    )
    assert "<html" in restored.text


def test_next_page_preserves_filters_population_catalog_and_snapshot(v2_client):
    client, _, _, _ = v2_client
    meta = client.get("/v2/meta?limit=1").json()
    row = meta["items"][0]
    path = "/explorer/teams"
    query = {
        "limit": 1,
        "regulation": "M-C",
        "population_id": row["population_id"],
        "catalog_revision": row["catalog_revision"],
        "snapshot": meta["snapshot"],
    }
    page = client.get(path, params=query)
    assert page.status_code == 200
    next_url = Document(page.text).link("Show more teams")
    params = parse_qs(urlsplit(next_url).query)
    for key, value in query.items():
        assert params[key] == [str(value)]
    assert params["cursor"][0]
    assert client.get(next_url).status_code == 200
    changed = client.get(next_url + "&species_id=rillaboom")
    assert changed.status_code == 400 and "Invalid continuation" in changed.text


def test_expired_or_ambiguous_query_has_safe_recovery(v2_client):
    from app.db.public_queries import PublicReader, get_public_reader
    from app.main import app

    client, _, table, _ = v2_client
    page = client.get("/explorer/teams?regulation=M-C&limit=1")
    assert page.status_code == 200
    next_url = Document(page.text).link("Show more teams")
    reader = app.dependency_overrides[get_public_reader]()
    config = replace(reader.config, clock=lambda: 100000)
    app.dependency_overrides[get_public_reader] = lambda: PublicReader(table, config=config)
    expired = client.get(next_url)
    assert expired.status_code == 410 and "Query expired" in expired.text
    recovery = Document(expired.text).link("Recover query")
    values = parse_qs(urlsplit(recovery).query)
    assert values["regulation"] == ["M-C"] and values["limit"] == ["1"]
    assert "snapshot" not in values and "cursor" not in values
    assert client.get(recovery).status_code == 200


def test_query_bounds_and_unsupported_filters_are_not_silently_ignored(v2_client):
    client, _, _, _ = v2_client
    for path in [
        "/explorer?limit=101",
        "/explorer?player_id=limitless:someone",
        "/explorer?date_from=2026-01-01&date_to=2026-10-01",
        "/explorer/teams?members=species:missing",
        "/explorer/teams?metric=item",
    ]:
        assert client.get(path).status_code == 422, path


def test_ambiguous_population_offers_real_choices(v2_client):
    from boto3.dynamodb.conditions import Key

    from core.models.public_read import PopulationKey, population_id
    from db.schema_public import public_state_key, snapshot_pk

    client, _, table, _ = v2_client
    snapshot_id = table.get_item(Key=public_state_key())["Item"]["snapshot_id"]
    row = table.query(KeyConditionExpression=Key("PK").eq(snapshot_pk(snapshot_id, "POPULATIONS")))[
        "Items"
    ][0]
    row["data"]["division_id"] = "masters"
    population = PopulationKey.model_validate(row["data"])
    row["SK"] = population_id(population) + ":" + row["SK"].partition(":")[2]
    table.put_item(Item=row)
    response = client.get("/explorer")
    assert response.status_code == 409 and "Select one exact population" in response.text
    choices = [
        attrs["href"]
        for attrs, text in Document(response.text).links
        if "population_id=" in attrs.get("href", "")
    ]
    assert len(choices) == 2
    for choice in choices:
        assert client.get(choice).status_code == 200


def test_team_detail_rejects_a_listing_cursor(v2_client):
    client, _, _, _ = v2_client
    first = client.get("/v2/teams?limit=1").json()
    page = client.get(
        "/explorer/teams/" + first["items"][0]["team_id"],
        params={"cursor": first["next_cursor"], "limit": 1},
    )
    assert page.status_code == 400
    assert "Invalid continuation" in page.text


def test_form_view_rejects_species_grouping(v2_client):
    client, _, _, _ = v2_client
    assert client.get("/explorer?group_by=species").status_code == 422
    assert client.get("/explorer/pokemon/incineroar?group_by=species").status_code == 422
    assert client.get("/v2/meta?group_by=species").status_code == 200


def test_enhanced_overview_updates_counts_chips_and_snapshot_together(v2_client):
    client, _, _, _ = v2_client
    page = client.get(
        "/explorer?date_from=2026-09-01&date_to=2026-09-02", headers={"HX-Request": "true"}
    )
    assert page.status_code == 200
    assert "0 rosters" in page.text
    assert 'data-remove-filter="date_from"' in page.text
    assert 'data-remove-filter="date_to"' in page.text
    assert "champions-mc-3661ce4" in page.text
    assert "<html" not in page.text


def test_filter_form_retains_selected_catalog_and_population(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer")
    inputs = [attrs for tag, attrs in Document(page.text).tags if tag == "input"]
    for name in ["catalog_revision", "population_id"]:
        field = next(value for value in inputs if value.get("name") == name)
        assert field.get("type") == "hidden"
    assert not any(value.get("name") == "cursor" for value in inputs)


def test_core_drilldown_carries_the_resolved_default_analytics_window(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer")
    link = Document(page.text).link("Show teams with this core")
    values = parse_qs(urlsplit(link).query)
    assert values["date_from"] == ["2026-09-04"]
    assert values["date_to"] == ["2026-10-03"]
    assert client.get(link).status_code == 200


def test_attribute_cursor_keeps_snapshot_when_current_pointer_is_unavailable(v2_client):
    from app.db.public_queries import PublicReader, get_public_reader
    from app.main import app
    from db.schema_public import public_state_key

    client, _, table, _ = v2_client
    page = client.get("/explorer/pokemon/floetteeternal?metric=move&limit=1")
    assert page.status_code == 200
    link = Document(page.text).link("More observed moves")
    values = parse_qs(urlsplit(link).query)
    values.pop("snapshot")
    original = app.dependency_overrides[get_public_reader]()
    config = replace(original.config, default_catalog_revision="champions-mb-3661ce4")
    app.dependency_overrides[get_public_reader] = lambda: PublicReader(table, config=config)
    table.delete_item(Key=public_state_key())
    page = client.get(
        "/explorer/pokemon/floetteeternal",
        params=[(key, value) for key, entries in values.items() for value in entries],
    )
    assert page.status_code == 200
    assert "floetteeternal" in page.text and "Known slots" in page.text
    assert "champions-mc-3661ce4" in page.text


def test_meta_and_teams_share_sprite_led_team_feed(v2_client):
    client, _, _, _ = v2_client
    api = client.get("/v2/teams?limit=12").json()
    for path in ["/explorer", "/explorer/teams"]:
        response = client.get(path)
        assert response.status_code == 200
        feed = response.text.split('class="team-feed"')[1].split("</section>")[0]
        assert "team-card" in feed and "team-roster" in feed
        assert feed.count('width="48"') >= 6
        assert "1st" in feed and "Source" in feed and "Builds" in feed
        for team in api["items"]:
            assert team["player_name"] not in feed
        import re

        visible = re.sub(r"<[^>]*>", "", feed)
        for text in ["Eligibility", "Roster:", "Catalog:", "champions-mc-3661ce4"]:
            assert text not in visible


def test_team_feed_orders_tournaments_by_published_entrant_count_then_placement(
    v2_client, monkeypatch
):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.db.public_queries import get_public_reader
    from app.main import app
    from app.web import data
    from app.web.models import ExplorerFilters

    client, _, _, _ = v2_client
    reader = app.dependency_overrides[get_public_reader]()
    page = data.read_teams(reader=reader, query=data.team_query(ExplorerFilters()))
    rows = [
        page.items[0].model_copy(
            update={"event_id": event, "standing": place, "team_id": "review:" + str(i)}
        )
        for i, (event, place) in enumerate(
            [("review:small", 1), ("review:big", 8), ("review:big", 1), ("review:unknown", 2)]
        )
    ]
    monkeypatch.setattr(
        data, "read_teams", lambda **kwargs: page.model_copy(update={"items": rows})
    )
    monkeypatch.setattr(
        data,
        "read_event",
        lambda event_id, **kwargs: SimpleNamespace(
            event_id=event_id,
            name=event_id,
            entrant_count={"review:big": 100, "review:small": 20, "review:unknown": None}[event_id],
            starts_at=datetime(2026, 9, 1, tzinfo=UTC),
            provenance=[],
            links={},
        ),
    )
    ctx = data.teams_context(reader, ExplorerFilters())
    assert [(e["team"].event_id, e["team"].standing) for e in ctx.data["entries"]] == [
        ("review:big", 1),
        ("review:big", 8),
        ("review:small", 1),
        ("review:unknown", 2),
    ]
    assert "loaded page" in ctx.data["ordering_note"]


def test_team_feed_continuation_preserves_tournament_boundary_and_snapshot(v2_client):
    client, value, _, _ = v2_client
    first = client.get("/explorer/teams", params={"tournament_id": value.event_id, "limit": 1})
    assert first.status_code == 200
    sentinel = next(
        attrs for tag, attrs in Document(first.text).tags if attrs.get("class") == "feed-sentinel"
    )
    assert sentinel.get("hx-target") == "this"
    next_url = Document(first.text).link("Show more teams")
    values = parse_qs(urlsplit(next_url).query)
    assert values["tournament_id"] == [value.event_id]
    assert values["previous_event"] == [value.event_id]
    assert values["snapshot"] and values["cursor"]
    chunk = client.get(next_url, headers={"HX-Request": "true"})
    assert chunk.status_code == 200
    assert "team-card" in chunk.text and "tournament-row" not in chunk.text
    assert first.text.count('class="tournament-row"') == 1
    assert "player_name" not in chunk.text
    invalid = client.get(next_url + "&species_id=rillaboom", headers={"HX-Request": "true"})
    assert invalid.status_code == 400 and "Recover query" in invalid.text
    assert 'class="state feed-error"' in invalid.text and 'id="results"' not in invalid.text
