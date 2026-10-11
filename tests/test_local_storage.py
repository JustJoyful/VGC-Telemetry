import pytest

from tests.test_local_clients import local_env  # noqa: F401


def test_reset_requires_explicit_confirmation(local_env):  # noqa: F811
    from scripts.local.storage import reset_demo

    with pytest.raises(ValueError, match="confirmation"):
        reset_demo(confirm=False)


def test_reset_rejects_wrong_resources_before_clients(local_env, monkeypatch):  # noqa: F811
    from scripts.local.storage import reset_demo

    monkeypatch.setenv("S3_RAW_BUCKET_NAME", "production")
    with pytest.raises(ValueError, match="S3_RAW_BUCKET_NAME"):
        reset_demo(confirm=True)


def test_storage_contract_checks_versions_metadata_and_conditional_write(
    dynamodb_table, s3_bucket, monkeypatch
):
    from scripts.local.storage import s3_contract

    report = s3_contract(s3_bucket.meta.client, s3_bucket.name)
    assert report["versioned_reads"]
    assert report["conditional_writes"]
    assert report["metadata"]
    assert report["lists"]


def test_reset_preserves_all_retained_object_versions(local_env, monkeypatch):  # noqa: F811
    import boto3
    from moto import mock_aws

    from core.aws import DEMO_NAME
    from scripts.local import storage

    with mock_aws():
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "localdemo")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "local-demo-secret")
        db = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        monkeypatch.setattr(storage, "aws_client", lambda name: {"dynamodb": db, "s3": s3}[name])
        storage.initialize()
        retained = []
        for key in (
            "staging/v2/captures/original",
            "staging/v2/catalog/candidate",
            "other/evidence",
        ):
            for body in (b"original", b"later"):
                result = s3.put_object(Bucket=DEMO_NAME, Key=key, Body=body)
                retained.append((key, result["VersionId"], body))
        storage.reset_demo(confirm=True)
        for key, version, body in retained:
            assert (
                s3.get_object(Bucket=DEMO_NAME, Key=key, VersionId=version)["Body"].read() == body
            )


def test_bootstrap_is_idempotent_and_preserves_existing_data(local_env, monkeypatch):  # noqa: F811
    import boto3
    from moto import mock_aws

    from core.aws import DEMO_NAME
    from scripts.local import storage

    with mock_aws():
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "localdemo")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "local-demo-secret")
        db, s3 = boto3.client("dynamodb"), boto3.client("s3")
        monkeypatch.setattr(storage, "aws_client", lambda name: {"dynamodb": db, "s3": s3}[name])
        storage.initialize()
        db.put_item(TableName=DEMO_NAME, Item={"PK": {"S": "existing"}, "SK": {"S": "row"}})
        version = s3.put_object(Bucket=DEMO_NAME, Key="existing", Body=b"retained")["VersionId"]
        storage.initialize()
        assert db.get_item(TableName=DEMO_NAME, Key={"PK": {"S": "existing"}, "SK": {"S": "row"}})[
            "Item"
        ]
        assert (
            s3.get_object(Bucket=DEMO_NAME, Key="existing", VersionId=version)["Body"].read()
            == b"retained"
        )
