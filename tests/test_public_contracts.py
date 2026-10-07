import pytest
from pydantic import ValidationError


def key(**changes):
    from core.models.public_read import PopulationKey

    return PopulationKey(
        source="limitless",
        game_id="pokemon-champions",
        battle_format="doubles",
        rules_id="M-C",
        catalog_revision="champions-mc-3661ce4",
        game_data_revision="showdown-3661ce4",
        **changes,
    )


def test_population_dimensions_never_silently_merge():
    from core.models.public_read import population_id

    base = population_id(key())
    for change in [
        {"division_id": "masters"},
        {"competition_id": "series"},
        {"custom_rules": ["rule"]},
        {"match_formats": ["BO3"]},
    ]:
        assert population_id(key(**change)) != base
    for name in ["rules_id", "catalog_revision", "game_data_revision"]:
        assert population_id(key().model_copy(update={name: "other"})) != base
    assert population_id(key(match_formats=["BO3", "BO1"])) == population_id(
        key(match_formats=["BO1", "BO3"])
    )
    assert key().division_id is None


def test_daily_contribution_rejects_negative_counts():
    from core.models.public_read import DailyContribution

    with pytest.raises(ValidationError):
        DailyContribution(
            event_id="limitless:e",
            entry_id="limitless:e:p",
            population_id="p",
            metric="species",
            identity="incineroar",
            count=-1,
        )
