import boto3
import pytest

from tests.publication_helpers import revision


def test_bounded_repeatable_rows_and_ready_manifest(s3_bucket, dynamodb_table):
    from ingestion.contributions import build_contributions, stage_revision

    value = revision(s3_bucket)
    bundle = build_contributions(value)
    assert bundle == build_contributions(value)
    teams = [r for r in bundle.rows if r["kind"] == "team"]
    assert len(teams) == 31
    assert all(r["top_cut_eligibility"] == "unknown" for r in teams)
    assert all(r["PK"].startswith("V2#REV#") for r in bundle.rows)
    manifest = stage_revision(
        bundle, table=dynamodb_table, s3_client=boto3.client("s3"), bucket=s3_bucket.name
    )
    assert manifest.ready and manifest.row_count == len(bundle.rows)
    assert manifest == stage_revision(
        bundle, table=dynamodb_table, s3_client=boto3.client("s3"), bucket=s3_bucket.name
    )
    assert len([r for r in bundle.rows if r["kind"] == "match"]) == len(
        value.result.tournament.matches
    )


def test_partial_unknown_empty_mega_and_population_boundaries(s3_bucket):
    from ingestion.contributions import build_contributions

    def change(t):
        t.metadata.starts_at = None
        t.teams = t.teams[:2]
        t.teams[0].division_id = "masters"
        t.teams[1].pokemon = t.teams[1].pokemon[:1]
        t.teams[1].publication_status = "partial"
        t.teams[0].pokemon[0].item.state = "empty"
        t.teams[0].pokemon[0].item.item_id = None

    bundle = build_contributions(revision(s3_bucket, mutate=change))
    assert len(bundle.populations) == 2
    metrics = [r for r in bundle.rows if r["kind"] == "metric"]
    assert any(r["metric"] == ["coverage", "partial"] and r["count"] == 1 for r in metrics)
    assert any(r["metric"][0] == "stat_points" and r["unknown"] > 0 for r in metrics)
    assert any(r["metric"][0] == "item" and r["metric"][-1] == "empty" for r in metrics)
    assert any(r["metric"][0] == "registered_mega" for r in metrics)
    assert all(r["day"] is None for r in metrics)
    assert any(r["metric"][0] == "core3" for r in metrics)


def test_large_distribution_and_interrupted_staging(s3_bucket, dynamodb_table, monkeypatch):
    from ingestion.contributions import build_contributions, stage_revision

    def change(t):
        original = t.teams[0]
        t.teams = [original.model_copy(deep=True) for _ in range(1200)]
        for i, team in enumerate(t.teams):
            team.entry_id = f"entry:{i}"
            team.pokemon[0].nature_id = f"nature:{i}"

    bundle = build_contributions(revision(s3_bucket, mutate=change))
    assert len([r for r in bundle.rows if r["kind"] == "metric"]) > 1200
    original = dynamodb_table.put_item
    calls = 0

    def fail(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("crash")
        return original(**kwargs)

    monkeypatch.setattr(dynamodb_table, "put_item", fail)
    with pytest.raises(RuntimeError):
        stage_revision(
            bundle, table=dynamodb_table, s3_client=boto3.client("s3"), bucket=s3_bucket.name
        )
    assert not any(r["SK"] == "MANIFEST" for r in dynamodb_table.scan()["Items"])
    monkeypatch.setattr(dynamodb_table, "put_item", original)
    manifest = stage_revision(
        bundle, table=dynamodb_table, s3_client=boto3.client("s3"), bucket=s3_bucket.name
    )
    assert manifest.ready


def test_oversized_row_cannot_be_ready(s3_bucket, dynamodb_table):
    from ingestion.contributions import build_contributions, stage_revision

    value = revision(s3_bucket, mutate=lambda t: setattr(t.metadata, "name", "x" * 410_000))
    with pytest.raises(ValueError, match="size"):
        stage_revision(
            build_contributions(value),
            table=dynamodb_table,
            s3_client=boto3.client("s3"),
            bucket=s3_bucket.name,
        )
