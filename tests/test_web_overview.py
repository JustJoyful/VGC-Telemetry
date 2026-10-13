# ruff: noqa: F811
from dataclasses import replace
from datetime import UTC, datetime

from botocore.exceptions import ClientError

from tests.v2_helpers import v2_client  # noqa: F401


def test_manual_collection_renders_snapshot_status(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer")
    assert page.status_code == 200
    assert "Fresh" in page.text and "How to read this" in page.text
    assert ">Live<" not in page.text
    assert "2 rosters" in page.text
    assert "Collection disabled" not in page.text


def test_stale_and_dirty_snapshot_state_is_visible(v2_client):
    from app.db.public_queries import PublicReader, get_public_reader
    from app.main import app
    from db.schema_public import public_state_key

    client, _, table, _ = v2_client
    original = app.dependency_overrides[get_public_reader]()
    config = replace(
        original.config, clock=lambda: int(datetime(2026, 10, 12, tzinfo=UTC).timestamp())
    )
    app.dependency_overrides[get_public_reader] = lambda: PublicReader(table, config=config)
    table.update_item(
        Key=public_state_key(),
        UpdateExpression="SET dirty = :yes",
        ExpressionAttributeValues={":yes": True},
    )
    page = client.get("/explorer")
    assert page.status_code == 200
    assert "Stale" in page.text and "update pending" in page.text
    assert "Collection disabled" not in page.text


def test_missing_snapshot_and_coverage_remain_unknown(v2_client):
    from db.schema_public import public_state_key

    client, _, table, _ = v2_client
    table.delete_item(Key=public_state_key())
    page = client.get("/explorer")
    assert page.status_code == 200
    assert "Unavailable" in page.text and "Unknown" in page.text
    assert "0 eligible complete rosters" not in page.text
    assert "No published snapshot" in page.text


def test_storage_failure_returns_useful_html(v2_client, monkeypatch):
    client, _, table, _ = v2_client

    def fail(**kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "private secret"}}, "GetItem"
        )

    monkeypatch.setattr(table, "get_item", fail)
    page = client.get("/explorer")
    assert page.status_code == 503
    assert page.headers["content-type"].startswith("text/html")
    assert "Unavailable" in page.text and "private secret" not in page.text


def test_overview_values_and_context_match_bounded_api(v2_client, monkeypatch):
    client, _, table, _ = v2_client

    def forbidden(**kwargs):
        raise AssertionError("No web scans")

    monkeypatch.setattr(table, "scan", forbidden)
    api = client.get("/v2/meta?limit=3").json()
    page = client.get("/explorer?limit=3")
    assert page.status_code == 200
    assert api["snapshot"] in page.text
    assert "2 rosters" in page.text
    assert "champions-mc-3661ce4" in page.text
    assert "Common cores" in page.text
    assert "Source" in page.text
    for row in api["items"]:
        assert row["identity"].split("/")[0] in page.text.lower()


def test_empty_window_known_zero_is_distinct_from_unknown(v2_client):
    client, _, _, _ = v2_client
    api = client.get("/v2/meta?date_from=2026-09-01&date_to=2026-09-02")
    assert api.status_code == 200 and api.json()["items"] == []
    page = client.get("/explorer?date_from=2026-09-01&date_to=2026-09-02")
    assert page.status_code == 200, page.text
    assert page.status_code == 200
    assert "0 rosters" in page.text
    assert "No matching observations" in page.text
    assert "0%" not in page.text


def test_overview_default_is_one_bounded_25_entry_page(v2_client):
    from tests.web_helpers import Document

    client, _, _, _ = v2_client
    api = client.get("/v2/meta?limit=25").json()
    page = client.get("/explorer")
    entries = [
        a
        for t, a in Document(page.text).tags
        if t == "article" and "usage-card" in a.get("class", "")
    ]
    assert len(entries) == len(api["items"]) <= 25
    assert "Next Pokémon page" not in page.text
    if api["next_cursor"]:
        next_url = Document(page.text).link("Show more Pokémon")
        assert "limit=25" in next_url and client.get(next_url).status_code == 200


def test_usage_carousel_ranks_top_25_by_usage_with_stable_ties(v2_client, monkeypatch):
    from app.db.public_queries import get_public_reader
    from app.main import app
    from app.web import data
    from app.web.models import ExplorerFilters

    client, _, _, _ = v2_client
    original = data.analytics_page
    baseline = original(
        data.analytics_query(ExplorerFilters()),
        reader=app.dependency_overrides[get_public_reader](),
        endpoint="/v2/meta",
    )
    names = {f"review{i}/base": f"Name {i:02d}" for i in range(27)}
    rows = [
        baseline.items[0].model_copy(
            update={
                "identity": identity,
                "percentage": 50 if i < 3 else 10,
                "count": 2 if i == 2 else 1,
            }
        )
        for i, identity in enumerate(names)
    ]
    calls = []

    def page(query, **kwargs):
        calls.append((kwargs["endpoint"], query.limit))
        if kwargs["endpoint"] == "/v2/meta":
            return baseline.model_copy(update={"items": list(reversed(rows))})
        return original(query, **kwargs)

    monkeypatch.setattr(data, "analytics_page", page)
    monkeypatch.setattr(
        data, "identity_name", lambda identity, catalog: names.get(identity, identity)
    )
    ctx = data.overview_context(app.dependency_overrides[get_public_reader](), ExplorerFilters())
    entries = ctx.data["selected"]
    assert len(entries) == 25
    assert [e["name"] for e in entries[:3]] == ["Name 02", "Name 00", "Name 01"]
    assert [e["rank"] for e in entries] == list(range(1, 26))
    assert calls == [("/v2/meta", 25), ("/v2/cores", 5)]
    assert all(ctx.filters.snapshot in e["url"] for e in entries)


def test_usage_carousel_keeps_unknown_percent_and_denominator_visible(v2_client, monkeypatch):
    from app.web import data

    client, _, _, _ = v2_client
    original = data.analytics_page

    def page(query, **kwargs):
        result = original(query, **kwargs)
        if kwargs["endpoint"] == "/v2/meta":
            rows = [
                r.model_copy(update={"percentage": None if i == 0 else 0})
                for i, r in enumerate(result.items)
            ]
            return result.model_copy(update={"items": rows})
        return result

    monkeypatch.setattr(data, "analytics_page", page)
    response = client.get("/explorer")
    assert response.status_code == 200
    carousel = response.text.split('class="usage-carousel"')[1].split("</section>")[0]
    assert "Unknown" in carousel and "0%" in carousel
    assert "of 2 rosters" in carousel
    assert 'tabindex="0"' in response.text
    assert "Next Pokémon page" not in response.text


def test_cores_render_sprites_and_expand_with_bounded_context(v2_client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from app.web import data
    from tests.web_helpers import Document

    client, _, table, _ = v2_client
    monkeypatch.setattr(
        table, "scan", lambda **kwargs: (_ for _ in ()).throw(AssertionError("No scans"))
    )
    calls = []
    original = data.read_teams

    def read(**kwargs):
        calls.append(kwargs["query"].limit)
        return original(**kwargs)

    monkeypatch.setattr(data, "read_teams", read)
    response = client.get("/explorer")
    tags = Document(response.text).tags
    core_rows = [a for t, a in tags if t == "details" and a.get("class") == "core-row"]
    assert 0 < len(core_rows) <= 5
    assert "Explore core teams" not in response.text
    expansion = next(
        a["href"] for t, a in tags if t == "a" and a.get("hx-target", "").startswith("#core-teams-")
    )
    query = parse_qs(urlsplit(expansion).query)
    assert "snapshot" in query and "members" in query and query["limit"] == ["6"]
    assert calls == [12]
    calls.clear()
    expanded = client.get(expansion, headers={"HX-Request": "true"})
    assert expanded.status_code == 200
    assert calls == [6]
    assert "See all teams with this core" in expanded.text
    assert "core-team-sprites" in expanded.text and 'width="48"' in expanded.text
    all_url = Document(expanded.text).link("See all teams with this core")
    assert parse_qs(urlsplit(all_url).query)["snapshot"] == query["snapshot"]
    assert parse_qs(urlsplit(all_url).query)["members"] == query["members"]

    continuation = next(
        a["href"] for t, a in tags if t == "a" and a.get("href", "").startswith("/explorer/cores?")
    )
    continued = client.get(continuation, headers={"HX-Request": "true"})
    assert continued.status_code == 200
    assert parse_qs(urlsplit(continuation).query)["snapshot"] == query["snapshot"]
    assert "core-row" in continued.text
    assert client.get(expansion).status_code == 200  # Native link works without HTMX.

    assert client.get("/explorer/cores?size=3").status_code == 422


def test_meta_status_strip_uses_warning_state_and_hides_internal_labels(v2_client):
    from app.db.public_queries import PublicReader, get_public_reader
    from app.main import app

    client, _, table, _ = v2_client
    reader = app.dependency_overrides[get_public_reader]()
    app.dependency_overrides[get_public_reader] = lambda: PublicReader(
        table,
        config=replace(
            reader.config, clock=lambda: int(datetime(2026, 10, 12, tzinfo=UTC).timestamp())
        ),
    )
    response = client.get("/explorer")
    assert response.status_code == 200
    strip = response.text.split('class="status-strip"')[1].split("</div>")[0]
    assert "warning" in strip and "Stale" in strip
    assert "Regulation M-C" in strip and "2 rosters" in strip
    assert "How to read this" in strip and "/explorer/about" in strip
    assert "Collection disabled" not in response.text and "champions-mc-3661ce4" not in strip
