"""One-pass regressions reproducing the fresh whole-change review findings."""

from dataclasses import replace

import boto3
import pytest

from app.models.v2_queries import AnalyticsQuery
from app.v2.errors import V2Error
from tests.phase2_helpers import public_bundle, real_public
from tests.test_public_snapshot import finish, held
from tests.test_v2_analytics import BundleReader, snapshot


@pytest.mark.parametrize(
    "selectors",
    [
        {"game_data_revision": "not-real", "match_mode": "BO3"},
        {"catalog_revision": "champions-mc-3661ce4", "regulation": "M-B", "match_mode": "BO3"},
    ],
)
def test_conflicting_empty_population_selectors_never_return_other_metrics(selectors):
    from app.v2.analytics import read_analytics

    with pytest.raises(V2Error) as failure:
        read_analytics(
            snapshot(), AnalyticsQuery(**selectors), reader=BundleReader([public_bundle(True)])
        )
    assert failure.value.status == 422


def test_nonadjacent_rollback_recovery_converges_without_rewinding_inputs(dynamodb_table):
    from db.publication import get
    from db.schema_public import public_state_key, snapshot_row_key
    from ingestion.public_snapshot import invalidate_public_inputs, rollback_public_snapshot

    table, s3 = dynamodb_table, boto3.client("s3")
    lease = held(table)
    first = finish(table, s3, lease)
    invalidate_public_inputs(table=table, policy_version="limitless-single-bracket-v1")
    second = finish(table, s3, lease)
    invalidate_public_inputs(table=table, policy_version="limitless-single-bracket-v1")
    third = finish(table, s3, lease)
    epoch = get(table, public_state_key())["epoch"]
    assert rollback_public_snapshot(
        first.snapshot_id,
        table=table,
        lease=lease,
        expected_snapshot_id=third.snapshot_id,
        clock=lambda: 100,
    )
    assert get(table, public_state_key())["dirty"] is True
    recovered = finish(table, s3, lease, max_steps=1)
    state = get(table, public_state_key())
    assert state["epoch"] == epoch and state["dirty"] is False
    assert state["snapshot_id"] == recovered.snapshot_id
    for previous in (first, second, third):
        assert get(table, snapshot_row_key(previous.snapshot_id, "META", "COMPLETE"))


def test_malformed_noncut_identity_preserves_valid_cut_and_rosters():
    from ingestion.normalize import normalize_capture
    from ingestion.public_contributions import derive_public_event
    from tests.source_helpers import real_inputs
    from tests.test_top_cut import mutate

    input, capture, catalog = real_public(True)

    def change(rows):
        row = next(r for r in rows if r["player"] not in ("axtheb", "red5"))
        row["player"] = {"not": "a scalar identity"}

    capture = mutate(capture, "standings", change)
    _, admission, _ = real_inputs("limitless")
    input.result = normalize_capture(capture, admission=admission, catalog=catalog)
    assert input.result.admission_status == "verified"
    assert len(input.result.tournament.teams) == 5
    assert any(d.code == "invalid_entry" for d in input.result.diagnostics)
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    assert bundle.cut.status == "verified_complete"
    event = next(r["data"] for r in bundle.rows if r["kind"] == "event")
    assert event["coverage"]["invalid_rosters"] == 1
    assert event["coverage"]["eligible_complete_rosters"] == 2
    assert len([r for r in bundle.rows if r["kind"] == "team"]) == 5


def test_date_window_removes_out_of_window_population_before_ambiguity():
    from app.v2.analytics import read_analytics

    first = public_bundle(True)
    other = first.model_copy(deep=True)
    for row in other.rows:
        if row["kind"] == "population":
            row["data"]["division_id"] = "masters"
            row["event_day"] = "2026-10-04"
    other.rows = [row for row in other.rows if row["kind"] == "population"]
    rows = read_analytics(
        snapshot(),
        AnalyticsQuery(date_from="2026-10-03", date_to="2026-10-03"),
        reader=BundleReader([first, other]),
    )
    assert rows and all(r.denominator == 2 for r in rows)


def test_tournament_filter_is_a_key_bound_before_contribution_budget():
    from app.v2.analytics import read_analytics

    bundle = public_bundle(False)
    event_id = bundle.input.event_id
    other = []
    for row in bundle.rows:
        if row["kind"] == "contribution":
            copy = {
                **row,
                "ordinal": "limitless:unrelated:" + row["ordinal"],
                "data": {**row["data"], "event_id": "limitless:unrelated"},
            }
            other.append(copy)
    assert len(other) * 2 > 5000 and len(other) < 5000
    bundle.rows.extend(other)
    rows = read_analytics(
        snapshot(), AnalyticsQuery(tournament_id=event_id), reader=BundleReader([bundle])
    )
    assert rows and all(r.denominator == 8 for r in rows)


def test_analytics_cursor_pins_resolved_default_catalog():
    from app.db.public_queries import PublicReader
    from app.v2.analytics import analytics_page

    class TokenReader(BundleReader):
        token = PublicReader.token
        _bounded = PublicReader._bounded

        def resolve_snapshot(self, snapshot_id):
            return snapshot()

    reader = TokenReader([public_bundle(True)])
    first = analytics_page(AnalyticsQuery(limit=1), reader=reader, endpoint="/v2/meta")
    assert first.next_cursor
    reader.config = replace(reader.config, default_catalog_revision="champions-mb-3661ce4")
    next_page = analytics_page(
        AnalyticsQuery(limit=1, cursor=first.next_cursor), reader=reader, endpoint="/v2/meta"
    )
    assert next_page.snapshot == first.snapshot
    assert next_page.items[0].denominator == 2
    assert next_page.items[0].catalog_revision == "champions-mc-3661ce4"
    with pytest.raises(V2Error) as failure:
        analytics_page(
            AnalyticsQuery(limit=1, cursor=first.next_cursor, metric="item"),
            reader=reader,
            endpoint="/v2/meta",
        )
    assert failure.value.status == 400
