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
