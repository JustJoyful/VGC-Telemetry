import json
from pathlib import Path

from tests.phase2_helpers import public_bundle, real_public


def test_large_field_evidence_is_retained_in_bounded_children():
    from ingestion.public_contributions import derive_public_event

    input, capture, catalog = real_public()
    mon = input.result.tournament.teams[0].pokemon[0]
    refs = next(iter(mon.field_evidence.values()))
    mon.field_evidence["species_id"] = refs * 1500
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    evidence = [
        r
        for r in bundle.rows
        if r["kind"] == "evidence"
        and r["section"] == "EVIDENCE#TEAM#" + input.result.tournament.teams[0].entry_id
        and "pokemon.0.species_id" in r["ordinal"]
    ]
    assert len(evidence) == 1500 * len(refs)
    assert all(len(json.dumps(r).encode()) < 350_000 for r in bundle.rows)


def test_large_team_identity_evidence_uses_child_rows_not_unbounded_summary():
    from ingestion.public_contributions import derive_public_event

    input, capture, catalog = real_public(True)
    team = input.result.tournament.teams[0]
    refs = team.field_evidence["entry_id"]
    team.field_evidence["entry_id"] = refs * 2000
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    view = next(
        r["data"]
        for r in bundle.rows
        if r["kind"] == "team_detail" and r["data"]["team_id"] == team.entry_id
    )
    assert len(view["provenance"]) <= 4
    assert view["provenance_count"] >= 2000
    children = [
        r
        for r in bundle.rows
        if r["kind"] == "evidence"
        and r["section"] == "EVIDENCE#TEAM#" + team.entry_id
        and r["ordinal"].startswith("team.entry_id:")
    ]
    assert len(children) == 2000 * len(refs)


def test_invalid_source_cut_roster_retains_standings_identity_without_build():
    from ingestion.public_contributions import derive_public_event

    input, capture, catalog = real_public(second=True)
    import hashlib

    from ingestion.normalize import normalize_capture
    from tests.source_helpers import real_inputs

    raw = json.loads(capture.documents["standings"].body)
    raw[0]["decklist"] = {"synthetic_invalid_shape": True}
    capture.documents["standings"].body = json.dumps(raw)
    capture.documents["standings"].content_hash = hashlib.sha256(
        capture.documents["standings"].body.encode()
    ).hexdigest()
    _, admission, _ = real_inputs("limitless")
    input.result = normalize_capture(capture, admission=admission, catalog=catalog)
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    context = [r["data"] for r in bundle.rows if r["kind"] == "standing"]
    assert len(context) == 6
    missing = next(t for t in context if t["player_id"] == "limitless:axtheb")
    assert missing["eligibility"]["status"] == "included"
    assert missing["pokemon"] == []
    assert missing["publication_status"] == "absent"
    assert missing["diagnostics"]
    event = next(r["data"] for r in bundle.rows if r["kind"] == "event")
    assert event["coverage"]["invalid_rosters"] == 1
    assert event["coverage"]["eligible_complete_rosters"] == 1


def test_duplicate_source_entry_is_counted_invalid_without_second_identity():
    import hashlib

    from ingestion.normalize import normalize_capture
    from ingestion.public_contributions import derive_public_event
    from tests.source_helpers import real_inputs

    input, capture, catalog = real_public(True)
    raw = json.loads(capture.documents["standings"].body)
    raw.append(dict(raw[0]))
    document = capture.documents["standings"]
    document.body = json.dumps(raw)
    document.content_hash = hashlib.sha256(document.body.encode()).hexdigest()
    _, admission, _ = real_inputs("limitless")
    input.result = normalize_capture(capture, admission=admission, catalog=catalog)
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    event = next(r["data"] for r in bundle.rows if r["kind"] == "event")
    assert event["coverage"]["standings_entries"] == 7
    assert event["coverage"]["invalid_rosters"] == 1
    assert len([r for r in bundle.rows if r["kind"] == "team_detail"]) == 6


def test_unknown_date_partial_and_duplicate_group_rollup():
    from ingestion.public_contributions import derive_public_event

    input, capture, catalog = real_public()
    teams = input.result.tournament.teams
    input.result.tournament.metadata.starts_at = None
    included = [
        t
        for t in teams
        if t.player_id in {"limitless:azelle", "limitless:martinb", "limitless:giac28"}
    ]
    for team in teams:
        if team not in included:
            team.publication_status, team.pokemon = "absent", []
    included[2].pokemon = included[2].pokemon[:3]
    included[2].publication_status = "partial"
    included[0].standing = None
    included[0].pokemon[-1] = included[0].pokemon[0].model_copy(update={"form_id": None})
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    event = next(r["data"] for r in bundle.rows if r["kind"] == "event")
    assert event["coverage"]["eligible_complete_rosters"] == 2
    assert event["coverage"]["partial_rosters"] == 1
    grouped = [
        r["data"]
        for r in bundle.rows
        if r["kind"] == "contribution"
        and r["data"]["metric"] == "species"
        and r["data"]["identity"] == "floette/base"
        and r["data"]["group_by"] == "species"
    ]
    assert sum(r["count"] for r in grouped) == 1
    assert all(r["day"] is None for r in grouped)


def metrics(bundle, metric, identity=None, value=None):
    return [
        r["data"]
        for r in bundle.rows
        if r["kind"] == "contribution"
        and r["data"]["metric"] == metric
        and r["data"]["group_by"] == "form"
        and (identity is None or r["data"]["identity"] == identity)
        and r["data"]["value"] == value
    ]


def test_real_cut_coverage_and_independent_metrics():
    expected = json.loads(Path("tests/fixtures/phase2/expected-real.json").read_text())
    for second, name in [(False, "gambit"), (True, "nice")]:
        bundle = public_bundle(second)
        event = next(r["data"] for r in bundle.rows if r["kind"] == "event")
        assert event["coverage"]["standings_entries"] == expected[name]["context_rosters"]
        assert (
            event["coverage"]["eligible_complete_rosters"]
            == expected[name]["eligible_complete_rosters"]
        )
        assert event["coverage"]["unknown_stat_points"] == expected[name]["unknown_stat_points"]
        assert sum(r["count"] for r in metrics(bundle, "species", "rillaboom/base")) == (
            6 if not second else 2
        )
        assert sum(r["count"] for r in metrics(bundle, "species", "floette/floetteeternal")) == (
            2 if not second else 1
        )
        assert sum(
            r["count"] for r in metrics(bundle, "item", "floette/floetteeternal", "floettite")
        ) == (2 if not second else 1)
        assert sum(
            r["count"] for r in metrics(bundle, "nature", "floette/floetteeternal", "modest")
        ) == (2 if not second else 1)
        assert sum(
            r["count"]
            for r in metrics(bundle, "registered_mega", "floette/floetteeternal", "floettemega")
        ) == (2 if not second else 1)
        assert sum(
            r["count"] for r in metrics(bundle, "move", "floette/floetteeternal", "protect")
        ) == (2 if not second else 1)
        for row in bundle.rows:
            assert len(json.dumps(row).encode()) <= 350_000
        team = next(
            r["data"]
            for r in bundle.rows
            if r["kind"] == "team"
            and r["data"]["player_id"] == ("limitless:axtheb" if second else "limitless:azelle")
        )
        assert team["pokemon"][0]["field_evidence"]
        assert "document_key" not in json.dumps(team["pokemon"])


def test_canonical_cores_deduplicate_order_and_species_grouping():
    from ingestion.public_contributions import canonical_cores

    assert canonical_cores([("a", "base"), ("b", "base"), ("a", "base")], size=2) == [
        (("a", "base"), ("b", "base"))
    ]
    assert canonical_cores([("c", "base"), ("a", "base"), ("b", "base")], size=3) == [
        (("a", "base"), ("b", "base"), ("c", "base"))
    ]


def test_synthetic_partial_private_and_incomplete_moves_are_excluded_honestly():
    from core.models.champions import ItemSlot
    from ingestion.public_contributions import derive_public_event

    input, capture, catalog = real_public()
    teams = input.result.tournament.teams
    a, b = [
        next(t for t in teams if t.player_id == "limitless:" + p) for p in ("azelle", "martinb")
    ]
    for t in teams:
        if t not in (a, b):
            t.publication_status, t.pokemon = "absent", []
    b.pokemon = [p.model_copy(deep=True) for p in reversed(a.pokemon)]
    b.pokemon[-1].item = ItemSlot()
    a.pokemon[0].item = ItemSlot(state="empty")
    a.pokemon[0].mega_variant_ids = []
    b.pokemon[-1].mega_variant_ids = None
    a.pokemon[0].moves_complete = False
    b.pokemon[-1].moves = None
    b.pokemon[-1].nature_id = None
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    assert sum(r["count"] for r in metrics(bundle, "roster")) == 2
    den = metrics(bundle, "item", "floette/floetteeternal")
    assert sum(r["denominator"] for r in den) == 1
    assert sum(r["unknown_count"] for r in den) == 1
    assert sum(r["count"] for r in metrics(bundle, "item", "floette/floetteeternal", "empty")) == 1
    assert sum(r["denominator"] for r in metrics(bundle, "move", "floette/floetteeternal")) == 0
    assert sum(r["unknown_count"] for r in metrics(bundle, "move", "floette/floetteeternal")) == 2
    assert (
        sum(r["unknown_count"] for r in metrics(bundle, "stat_points", "floette/floetteeternal"))
        == 2
    )
