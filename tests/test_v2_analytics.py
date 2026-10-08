"""Literal real expectations and synthetic bounded analytics windows."""
# ruff: noqa: F811

from datetime import date

import pytest

from app.models.v2 import SnapshotInfo
from app.models.v2_queries import AnalyticsQuery, CoreQuery
from app.v2.config import V2ReadConfig
from tests.phase2_helpers import public_bundle


class BundleReader:
    def __init__(self, bundles, **config):
        self.config = V2ReadConfig(secret=b"secret" * 8, clock=lambda: 100, **config)
        self.rows = [r for b in bundles for r in b.rows]
        self.calls = []

    def query(self, snapshot, section, *, position=None, limit=100, prefix=None):
        self.calls.append((section, limit))
        rows = sorted((r for r in self.rows if r["section"] == section), key=lambda r: r["ordinal"])
        rows = [
            dict(r, SK=r["ordinal"]) for r in rows if position is None or r["ordinal"] > position
        ]
        if prefix is not None:
            rows = [r for r in rows if r["SK"].startswith(prefix)]
        result = {"Items": rows[:limit]}
        if len(rows) > limit:
            result["LastEvaluatedKey"] = {"SK": rows[limit - 1]["ordinal"]}
        return result

    def get(self, key):
        return {"latest_event_day": "2026-10-04", "coverage": {}}


def snapshot():
    return SnapshotInfo(snapshot_id="synthetic-retained", expires_at=86500)


def test_real_combined_usage_cores_and_all_attribute_families():
    from app.v2.analytics import read_analytics

    reader = BundleReader([public_bundle(), public_bundle(True)])
    query = AnalyticsQuery(date_from="2026-10-01", date_to="2026-10-04", limit=100)
    rows = read_analytics(snapshot(), query, reader=reader)
    rilla = next(r for r in rows if r.identity == "rillaboom/base")
    assert (rilla.count, rilla.denominator, rilla.percentage) == (8, 10, 80)
    assert sum(r.denominator == 10 for r in rows) == len(rows)
    for metric, value in [
        ("ability", "flowerveil"),
        ("item", "floettite"),
        ("nature", "modest"),
        ("move", "protect"),
        ("registered_mega", "floettemega"),
    ]:
        rows = read_analytics(
            snapshot(),
            query.model_copy(
                update={"metric": metric, "species_id": "floette", "form_id": "floetteeternal"}
            ),
            reader=reader,
        )
        result = next(r for r in rows if r.value == value)
        assert (result.count, result.denominator, result.percentage) == (3, 3, 100)
        if metric == "nature":
            assert result.nature_alignment == {"plus": "spa", "minus": "atk"}
    private = read_analytics(
        snapshot(), query.model_copy(update={"metric": "stat_points"}), reader=reader
    )
    assert sum(r.unknown_count for r in private) == 60
    assert all(r.percentage is None and r.denominator == 0 for r in private)
    for size, identity, count in [
        (2, "incineroar/base+rillaboom/base", 7),
        (3, "gholdengo/base+incineroar/base+rillaboom/base", 6),
    ]:
        rows = read_analytics(
            snapshot(),
            CoreQuery(size=size, date_from="2026-10-01", date_to="2026-10-04"),
            reader=reader,
        )
        core = next(r for r in rows if r.identity == identity)
        assert (core.count, core.denominator) == (count, 10)
        assert core.members == identity.split("+")


def test_population_boundaries_require_selection_and_defaults_fail_visibly():
    from app.v2.analytics import read_analytics
    from app.v2.errors import V2Error

    bundle = public_bundle(True)
    extra = bundle.model_copy(deep=True)
    for row in extra.rows:
        if row["kind"] == "population":
            row["data"]["division_id"] = "masters"
    reader = BundleReader([bundle, extra])
    with pytest.raises(V2Error) as failure:
        read_analytics(snapshot(), AnalyticsQuery(), reader=reader)
    assert failure.value.status == 409
    choices = failure.value.details["choices"]
    assert len(choices) == 2
    assert (
        read_analytics(
            snapshot(), AnalyticsQuery(population_id=choices[0]["population_id"]), reader=reader
        )
        is not None
    )
    with pytest.raises(V2Error) as failure:
        read_analytics(
            snapshot(),
            AnalyticsQuery(),
            reader=BundleReader([], default_catalog_revision="missing"),
        )
    assert failure.value.status == 503
    assert read_analytics(snapshot(), AnalyticsQuery(), reader=BundleReader([])) == []


def test_undated_only_explicit_event_and_focal_placement_preserves_opponent():
    from app.v2.analytics import read_analytics

    bundle = public_bundle(True)
    event = bundle.input.event_id
    dated = BundleReader([bundle])
    rows = read_analytics(snapshot(), AnalyticsQuery(placement_max=1), reader=dated)
    assert all(r.denominator == 1 for r in rows)
    assert any(r.performance.mirrors > 0 for r in rows)
    for row in bundle.rows:
        if row["kind"] == "contribution":
            row["data"]["day"] = None
            row["section"] = "UNDATED#" + row["data"]["population_id"]
    undated = BundleReader([bundle])
    assert read_analytics(snapshot(), AnalyticsQuery(), reader=undated) == []
    assert read_analytics(snapshot(), AnalyticsQuery(tournament_id=event), reader=undated)
    assert (
        read_analytics(
            snapshot(),
            AnalyticsQuery(tournament_id=event, date_from=date(2026, 10, 3)),
            reader=undated,
        )
        == []
    )


@pytest.mark.parametrize("row_count,passes", [(5000, True), (5001, False)])
def test_exact_contribution_budget_never_returns_partial_success(row_count, passes):
    from app.v2.analytics import read_analytics
    from app.v2.errors import V2Error

    bundle = public_bundle(True)
    template = next(r for r in bundle.rows if r["kind"] == "contribution")
    template["data"].update(metric="species", identity="synthetic/base", count=1)
    bundle.rows = [r for r in bundle.rows if r["kind"] != "contribution"]
    bundle.rows += [dict(template, ordinal=f"{i:05d}") for i in range(row_count)]
    reader = BundleReader([bundle])
    query = AnalyticsQuery(date_from="2026-10-03", date_to="2026-10-03")
    if passes:
        assert read_analytics(snapshot(), query, reader=reader)
    else:
        with pytest.raises(V2Error) as failure:
            read_analytics(snapshot(), query, reader=reader)
        assert failure.value.code == "query_too_broad"
    assert len(reader.calls) <= 50
    assert all(limit <= 5000 for _, limit in reader.calls)


def test_unsupported_analytics_filters_are_not_silently_ignored():
    from pydantic import ValidationError

    for extra in (
        {"player_id": "limitless:p"},
        {"eligibility": "all"},
        {"mega_variant_id": "floettemega"},
    ):
        with pytest.raises(ValidationError):
            AnalyticsQuery(**extra)


def test_empty_historical_regulation_selection_has_no_legacy_fallback():
    from app.v2.analytics import read_analytics

    assert (
        read_analytics(snapshot(), AnalyticsQuery(regulation="M-B"), reader=BundleReader([])) == []
    )


def test_single_date_bound_uses_pinned_latest_event_anchor():
    from app.v2.analytics import read_analytics
    from app.v2.errors import V2Error

    with pytest.raises(V2Error) as failure:
        read_analytics(
            snapshot(),
            AnalyticsQuery(date_from="2026-01-01"),
            reader=BundleReader([public_bundle(True)]),
        )
    assert failure.value.code == "query_too_broad"


def test_event_selector_disambiguates_divisions_and_rejects_conflicts():
    from app.v2.analytics import read_analytics
    from app.v2.errors import V2Error

    first, second = public_bundle(), public_bundle(True)
    for row in first.rows:
        if row["kind"] == "population":
            row["data"]["division_id"] = "masters"
    reader = BundleReader([first, second])
    rows = read_analytics(
        snapshot(), AnalyticsQuery(tournament_id=second.input.event_id), reader=reader
    )
    assert rows and all(r.denominator == 2 for r in rows)
    with pytest.raises(V2Error) as failure:
        read_analytics(
            snapshot(),
            AnalyticsQuery(population_id=rows[0].population_id, division_id="masters"),
            reader=reader,
        )
    assert failure.value.status == 422


@pytest.mark.parametrize("contribution_pages,passes", [(48, True), (49, False)])
def test_query_budget_exactly_fifty_and_one_over(contribution_pages, passes):
    from app.v2.analytics import read_analytics
    from app.v2.errors import V2Error

    bundle = public_bundle(True)
    template = next(r for r in bundle.rows if r["kind"] == "contribution")
    template["data"].update(metric="species", identity="synthetic/base", count=1)
    bundle.rows = [r for r in bundle.rows if r["kind"] != "contribution"]
    bundle.rows += [dict(template, ordinal=f"{i:05d}") for i in range(contribution_pages)]
    reader = BundleReader([bundle])
    original = reader.query

    def small_pages(snapshot, section, **kwargs):
        if section.startswith("DAY#"):
            kwargs["limit"] = 1
        return original(snapshot, section, **kwargs)

    reader.query = small_pages
    if passes:
        assert read_analytics(snapshot(), AnalyticsQuery(), reader=reader)
    else:
        with pytest.raises(V2Error) as failure:
            read_analytics(snapshot(), AnalyticsQuery(), reader=reader)
        assert failure.value.code == "query_too_broad"
    assert len(reader.calls) == 50


def test_partial_cut_rosters_excluded_from_both_groupings():
    from app.v2.analytics import read_analytics
    from ingestion.public_contributions import derive_public_event
    from tests.phase2_helpers import real_public

    input, capture, catalog = real_public(True)
    team = next(t for t in input.result.tournament.teams if t.player_id == "limitless:red5")
    team.publication_status = "partial"
    team.pokemon = team.pokemon[:3]
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    for group in ("form", "species"):
        rows = read_analytics(
            snapshot(), AnalyticsQuery(group_by=group), reader=BundleReader([bundle])
        )
        assert rows and all(r.denominator == 1 and r.excluded_count == 1 for r in rows)


def test_analytics_correction_keeps_full_original_window(v2_client):
    from datetime import UTC, datetime

    import boto3

    from tests.phase2_helpers import publish_input
    from tests.test_public_snapshot import finish, held

    client, value, table, s3 = v2_client
    first = client.get("/v2/meta", params={"limit": 1}).json()

    def partial(tournament):
        team = next(t for t in tournament.teams if t.player_id == "limitless:red5")
        team.publication_status = "partial"
        team.pokemon = team.pokemon[:3]

    publish_input(
        table,
        boto3.resource("s3").Bucket(value.bucket),
        mutate=partial,
        captured_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    finish(table, s3, held(table))
    last = client.get("/v2/meta", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert last["snapshot"] == first["snapshot"]
    assert last["items"][0]["denominator"] == 2
    assert all(r["denominator"] == 1 for r in client.get("/v2/meta").json()["items"])


def test_complete_window_pagination_pins_denominators_and_expiry(v2_client):
    client, value, _, _ = v2_client
    first = client.get("/v2/meta", params={"limit": 1})
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["items"][0]["denominator"] == 2
    selected = client.get("/v2/meta", params={"placement_max": 1}).json()
    assert selected["coverage"]["eligible_complete_rosters"] == 1
    assert first["next_cursor"]
    last = client.get("/v2/meta", params={"limit": 1, "cursor": first["next_cursor"]})
    assert last.status_code == 200, last.text
    assert last.json()["snapshot"] == first["snapshot"]
    assert last.json()["items"][0]["denominator"] == 2
    assert last.json()["items"][0]["identity"] > first["items"][0]["identity"]
    assert (
        client.get("/v2/cores", params={"limit": 1, "cursor": first["next_cursor"]}).status_code
        == 400
    )
    assert (
        client.get(
            "/v2/meta", params={"limit": 1, "metric": "item", "cursor": first["next_cursor"]}
        ).status_code
        == 400
    )
    assert client.get("/v2/meta", params={"player_id": "limitless:axtheb"}).status_code == 422
    for size in (2, 3):
        response = client.get("/v2/cores", params={"size": size})
        assert response.status_code == 200, response.text
        assert response.json()["items"]
    assert (
        client.get(
            "/v2/meta", params={"date_from": "2026-01-01", "date_to": "2026-03-31"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/v2/meta", params={"date_from": "2026-01-01", "date_to": "2026-04-01"}
        ).status_code
        == 422
    )


# pytest imports the fixture by name, not through ordinary Python references.
from tests.v2_helpers import v2_client  # noqa: E402, F401
