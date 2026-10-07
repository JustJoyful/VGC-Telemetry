import pytest

from core.top_cut import derive_cut
from tests.phase2_helpers import real_public


@pytest.mark.parametrize(
    "outcome,win,loss,tie,double,played",
    [
        ("win", 1, 1, 0, 0, 2),
        ("tie", 0, 0, 2, 0, 2),
        ("double_loss", 0, 2, 0, 2, 2),
        ("pending", 0, 0, 0, 0, 0),
        ("unknown", 0, 0, 0, 0, 0),
    ],
)
def test_synthetic_match_sides_and_mirrors(outcome, win, loss, tie, double, played):
    from ingestion.public_contributions import match_associations, population_for

    input, capture, _ = real_public(second=True)
    t = input.result.tournament
    cut = derive_cut(capture, input.result)
    match = next(m for m in t.matches if m.phase == 2)
    t.matches = [match]
    match.outcome = outcome
    match.winner_id = "limitless:axtheb" if outcome == "win" else None
    populations = {team.entry_id: population_for(t.metadata, team) for team in t.teams}
    rows = match_associations(t, cut, populations=populations)
    team = [r for r in rows if r.metric == "performance:team"]
    assert sum(r.wins for r in team) == win
    assert sum(r.losses for r in team) == loss
    assert sum(r.ties for r in team) == tie
    assert sum(r.double_losses for r in team) == double
    assert sum(r.wins + r.losses + r.ties for r in team) == played
    mirrors = [
        r
        for r in rows
        if r.metric == "performance:species"
        and r.identity == "incineroar/base"
        and r.group_by == "form"
    ]
    assert sum(r.mirrors for r in mirrors) == (2 if played else 0)
    assert sum(r.wins + r.losses + r.ties for r in mirrors) == 0


def test_win_formula_and_zero_denominator():
    from ingestion.public_contributions import performance_percentage

    assert performance_percentage(2, 1, 1) == 62.5
    assert performance_percentage(0, 0, 0) is None


@pytest.mark.parametrize("outcome", ["bye", "automatic_loss", "unlinked"])
def test_excluded_outcomes_and_unlinked_sides_never_become_losses(outcome):
    from ingestion.public_contributions import match_associations, population_for

    input, capture, _ = real_public(second=True)
    t = input.result.tournament
    cut = derive_cut(capture, input.result)
    match = next(m for m in t.matches if m.phase == 2)
    t.matches = [match]
    if outcome == "unlinked":
        match.player2_id = "limitless:missing"
    else:
        match.player2_id = None
        match.outcome = outcome
        match.winner_id = match.player1_id if outcome == "bye" else None
    rows = match_associations(
        t, cut, populations={team.entry_id: population_for(t.metadata, team) for team in t.teams}
    )
    assert sum(r.wins + r.losses + r.ties for r in rows) == 0
    assert sum(r.unlinked for r in rows) == 1


def test_team_details_expose_cut_outcomes_even_with_absent_roster():
    from ingestion.public_contributions import derive_public_event
    from tests.phase2_helpers import real_public

    input, capture, catalog = real_public(True)
    loser = next(t for t in input.result.tournament.teams if t.player_id == "limitless:red5")
    loser.publication_status = "absent"
    loser.pokemon = []
    bundle = derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
    teams = {r["data"]["player_id"]: r["data"] for r in bundle.rows if r["kind"] == "team_detail"}
    assert teams["limitless:axtheb"]["performance"]["wins"] == 1
    assert teams["limitless:red5"]["performance"]["losses"] == 1
    assert teams["limitless:red5"]["performance"]["played"] == 1
    assert teams["limitless:red5"]["performance"]["percentage"] == 0
    assert teams["limitless:red5"]["publication_status"] == "absent"
