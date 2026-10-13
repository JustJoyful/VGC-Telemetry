# ruff: noqa: F811
from tests.v2_helpers import v2_client  # noqa: F401


def test_correction_between_pages_retains_old_teams(v2_client):
    from datetime import UTC, datetime

    from tests.phase2_helpers import publish_input
    from tests.test_public_snapshot import finish, held

    client, value, table, s3 = v2_client
    first = client.get("/v2/teams", params={"limit": 1}).json()
    removed = first["items"][0]["team_id"]
    bucket = __import__("boto3").resource("s3").Bucket(value.bucket)
    publish_input(
        table,
        bucket,
        mutate=lambda t: setattr(t, "teams", []),
        captured_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    finish(table, s3, held(table))
    last = client.get("/v2/teams", params={"limit": 1, "cursor": first["next_cursor"]})
    assert last.status_code == 200, last.text
    assert last.json()["snapshot"] == first["snapshot"]
    assert len(last.json()["items"]) == 1
    assert last.json()["next_cursor"] is None
    assert (
        client.get("/v2/teams/" + removed, params={"snapshot": first["snapshot"]}).status_code
        == 200
    )
    assert client.get("/v2/teams/" + removed).status_code == 404
    assert client.get("/v2/teams").json()["items"] == []


def test_teams_filters_partial_units_pagination_and_read_only(v2_client, monkeypatch):
    client, value, table, _ = v2_client

    def forbidden(**kwargs):
        raise AssertionError("HTTP must not scan or write public data")

    monkeypatch.setattr(table, "scan", forbidden)
    monkeypatch.setattr(table, "put_item", forbidden)
    monkeypatch.setattr(table, "update_item", forbidden)
    first = client.get("/v2/teams", params={"limit": 1}).json()
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    last = client.get("/v2/teams", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert len(last["items"]) == 1
    assert last["next_cursor"] is None
    assert first["items"][0]["team_id"] != last["items"][0]["team_id"]
    assert (
        client.get(
            "/v2/teams", params={"limit": 1, "cursor": first["next_cursor"], "eligibility": "all"}
        ).status_code
        == 400
    )
    assert len(client.get("/v2/teams", params={"eligibility": "all"}).json()["items"]) == 6
    for params in [
        {"player_id": "limitless:axtheb"},
        {"species_id": "floette", "form_id": "floetteeternal"},
        {"mega_variant_id": "floettemega"},
        {"placement_max": 1},
        {"tournament_id": value.event_id, "placement_min": 1, "placement_max": 1},
    ]:
        response = client.get("/v2/teams", params=params)
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 1
    team = client.get(
        "/v2/teams/" + first["items"][0]["team_id"], params={"snapshot": first["snapshot"]}
    ).json()
    assert team["pokemon"][0]["stat_points"] is None
    assert "PK" not in team and "SK" not in team
    assert client.get("/v2/teams", params={"form_id": "rotomwash"}).status_code == 422
    assert (
        client.get(
            "/v2/teams", params={"species_id": "incineroar", "form_id": "floetteeternal"}
        ).status_code
        == 422
    )


def test_member_selectors_match_same_roster_and_reject_bad_queries(v2_client):
    client, _, _, _ = v2_client
    params = [("members", "species:floette"), ("members", "species:rillaboom")]
    response = client.get("/v2/teams", params=params)
    assert response.status_code == 200, response.text
    teams = response.json()["items"]
    assert len(teams) == 1
    for selector in ("form:floetteeternal", "mega:floettemega"):
        response = client.get("/v2/teams", params=[("members", selector), params[1]])
        assert response.status_code == 200, response.text
        assert [t["team_id"] for t in response.json()["items"]] == [teams[0]["team_id"]]
    assert client.get("/v2/teams", params={"members": "form:floettemega"}).status_code == 422
    assert client.get("/v2/teams", params={"members": "species:missing"}).status_code == 422
    for selectors in [
        ["rillaboom"],
        ["species:Rillaboom"],
        ["species:rillaboom"] * 2,
        ["species:floette", "mega:floettemega"],
        ["form:rotomwash", "form:rotomheat"],
        ["species:rillaboom", "species:incineroar", "species:floette", "species:gholdengo"],
    ]:
        response = client.get("/v2/teams", params=[("members", s) for s in selectors])
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_query"
    assert (
        client.get("/v2/teams", params={"members": "species:floette", "unknown": 1}).status_code
        == 422
    )
    assert client.get("/v2/teams", params={"population_id": "missing"}).json()["items"] == []


def test_members_require_observed_fields_even_on_partial_rosters():
    from app.db.public_queries import PublicReader
    from app.models.v2_queries import TeamQuery
    from app.v2.config import V2ReadConfig

    reader = PublicReader(None, config=V2ReadConfig())
    data = {"pokemon": [{"species_id": "floette", "form_id": None, "mega_variant_ids": None}]}
    assert reader._matches(data, TeamQuery(members=["species:floette"], eligibility="all"))
    assert not reader._matches(data, TeamQuery(members=["form:floetteeternal"], eligibility="all"))
    assert not reader._matches(data, TeamQuery(members=["mega:floettemega"], eligibility="all"))
    assert not reader._matches(
        data, TeamQuery(members=["species:floette", "species:rillaboom"], eligibility="all")
    )
    assert not reader._matches(
        {"pokemon": []}, TeamQuery(members=["species:floette"], eligibility="all")
    )


def test_member_candidate_pages_continue_when_empty_and_bind_filters():
    import pytest

    from app.db.public_queries import PublicReader
    from app.models.v2_queries import TeamQuery
    from app.v2.cursors import decode_cursor
    from app.v2.errors import V2Error
    from tests.phase2_helpers import public_bundle
    from tests.test_v2_analytics import BundleReader, snapshot

    stream = BundleReader(
        [public_bundle(True)], listing_queries=1, listing_rows=1, candidate_page_size=1
    )
    reader = PublicReader(None, config=stream.config)
    reader.query, reader.get = stream.query, stream.get
    query = TeamQuery(members=["species:floette"], limit=1)
    position, found, empty = None, [], 0
    for _ in range(10):
        page = reader.page(snapshot(), section="TEAMS", query=query, position=position, limit=1)
        found.extend(page.items)
        empty += int(not page.items)
        assert len(page.model_dump_json().encode()) <= stream.config.response_bytes
        if not page.next_cursor:
            break
        from app.models.v2_queries import canonical_query

        token = decode_cursor(
            page.next_cursor,
            endpoint="/v2/teams",
            query=canonical_query(query),
            limit=1,
            now=100,
            secret=stream.config.secret,
        )
        assert token["position"] != position
        position = token["position"]
        with pytest.raises(V2Error):
            decode_cursor(
                page.next_cursor,
                endpoint="/v2/teams",
                query=canonical_query(TeamQuery(members=["species:rillaboom"], limit=1)),
                limit=1,
                now=100,
                secret=stream.config.secret,
            )
    assert empty and len(found) == 1
    assert all(limit == 1 for _, limit in stream.calls)


def test_member_population_selects_its_catalog_instead_of_new_default():
    from datetime import UTC, datetime

    from app.db.public_queries import PublicReader
    from app.models.v2_queries import TeamQuery
    from app.v2.cursors import encode_snapshot
    from tests.phase2_helpers import public_bundle
    from tests.test_v2_analytics import BundleReader, snapshot

    stream = BundleReader([public_bundle(True)], default_catalog_revision="champions-mb-3661ce4")
    reader = PublicReader(None, config=stream.config)
    reader.query, reader.get = stream.query, stream.get
    reader.resolve_snapshot = lambda _: snapshot().model_copy(
        update={"generated_at": datetime(2026, 10, 1, tzinfo=UTC)}
    )
    pid = next(r["data"]["population_id"] for r in stream.rows if r["kind"] == "contribution")
    query = TeamQuery(
        members=["species:incineroar", "species:rillaboom"],
        population_id=pid,
        snapshot=encode_snapshot(
            "synthetic-retained", expires_at=80000, secret=stream.config.secret
        ),
    )
    snap, position = reader.request_snapshot(query, "/v2/teams")
    page = reader.page(snap, section="TEAMS", query=query, position=position, limit=25)
    assert len(page.items) == 2
    assert query.catalog_revision == "champions-mc-3661ce4"
    assert len(stream.calls) <= stream.config.listing_queries
