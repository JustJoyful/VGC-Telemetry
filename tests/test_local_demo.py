from scripts.local import demo


def test_real_evidence_stage_native_duplicate_correction_fault_recovery_rollback(
    dynamodb_table, s3_bucket, monkeypatch
):
    monkeypatch.setattr(demo.time, "time", lambda: 100)
    report = demo.demonstrate(dynamodb_table, s3_bucket.meta.client, s3_bucket.name)
    assert report["baseline"] == {"rosters": 37, "eligible_rosters": 10, "unknown_stat_points": 60}
    assert report["duplicate_unchanged"]
    assert report["corrected"]["eligible_rosters"] == 9
    assert report["correction_delta"] == -1
    assert report["fault"]["name"] == "synthetic-after-first-public-team-write"
    assert report["fault"]["current_unchanged"]
    assert report["rollback"]["eligible_rosters"] == 10
    assert report["replayed"]["eligible_rosters"] == 9
    assert len(report["fixtures"]) == 2


def test_seed_refuses_existing_table_and_retained_s3_versions(dynamodb_table, s3_bucket):
    import pytest

    from scripts.local.storage import require_empty

    require_empty(dynamodb_table, s3_bucket.meta.client, s3_bucket.name)
    dynamodb_table.put_item(Item={"PK": "V2#RECOVERY", "SK": "DIRTY", "cursor": None})
    require_empty(dynamodb_table, s3_bucket.meta.client, s3_bucket.name)
    dynamodb_table.put_item(Item={"PK": "saved", "SK": "data", "value": 42})
    with pytest.raises(ValueError, match="empty"):
        demo.seed(dynamodb_table, s3_bucket.meta.client, s3_bucket.name)
    assert dynamodb_table.get_item(Key={"PK": "saved", "SK": "data"})["Item"]["value"] == 42
    dynamodb_table.delete_item(Key={"PK": "saved", "SK": "data"})
    s3_bucket.Versioning().enable()
    version = s3_bucket.meta.client.put_object(
        Bucket=s3_bucket.name, Key="saved", Body=b"retained"
    )["VersionId"]
    s3_bucket.meta.client.delete_object(Bucket=s3_bucket.name, Key="saved")
    with pytest.raises(ValueError, match="empty"):
        demo.seed(dynamodb_table, s3_bucket.meta.client, s3_bucket.name)
    assert (
        s3_bucket.meta.client.get_object(Bucket=s3_bucket.name, Key="saved", VersionId=version)[
            "Body"
        ].read()
        == b"retained"
    )
