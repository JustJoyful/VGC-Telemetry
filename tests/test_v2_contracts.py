import pytest
from pydantic import ValidationError


def test_native_unknown_and_zero_survive_typed_team_projection():
    from app.models.v2 import TeamView

    data = dict(
        team_id="limitless:e:p",
        event_id="limitless:e",
        player_id="limitless:p",
        eligibility={"status": "unknown", "reason": "unverified"},
        publication_status="partial",
        pokemon=[
            dict(
                species_id="incineroar",
                stat_points={"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            )
        ],
    )
    view = TeamView(**data)
    assert view.pokemon[0].stat_points.canonical() == "0/0/0/0/0/0"
    assert view.pokemon[0].item.state == "unknown"
    assert view.pokemon[0].nature_id is None
    for hidden in ["PK", "SK", "lease", "ledger"]:
        with pytest.raises(ValidationError):
            TeamView(**data, **{hidden: "secret"})
    with pytest.raises(ValidationError):
        TeamView(**{**data, "team_id": "display name"})


@pytest.mark.parametrize(
    "kind,params",
    [
        ("TeamQuery", {"limit": 101}),
        ("TeamQuery", {"typo": "ignored"}),
        ("TeamQuery", {"form_id": "rotomwash"}),
        ("TeamQuery", {"placement_min": 0}),
        ("TeamQuery", {"placement_min": 4, "placement_max": 2}),
        ("TeamQuery", {"tournament_id": "bad"}),
        ("TeamQuery", {"source": "rk9"}),
        ("AnalyticsQuery", {"date_from": "2026-01-01", "date_to": "2026-04-01"}),
        ("AnalyticsQuery", {"metric": "iv"}),
        ("CoreQuery", {"size": 4}),
        ("TournamentQuery", {"date_from": "2026-10-03", "date_to": "2026-10-01"}),
    ],
)
def test_queries_reject_invalid_combinations(kind, params):
    from app.models import v2_queries

    with pytest.raises(ValidationError):
        getattr(v2_queries, kind)(**params)


def test_query_canonicalization_and_bounds():
    from app.models.v2_queries import AnalyticsQuery, CoreQuery, TeamQuery, canonical_query

    assert TeamQuery().limit == 25
    assert CoreQuery(size=2).group_by == "form"
    query = AnalyticsQuery(date_from="2026-01-01", date_to="2026-03-31", metric="stat_points")
    assert canonical_query(query)["metric"] == "stat_points"
    assert "cursor" not in canonical_query(query)
    assert "snapshot" not in canonical_query(query)
