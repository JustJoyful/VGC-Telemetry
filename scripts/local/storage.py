"""Storage initialization, exact demo reset and executable S3 contract."""

import time

from botocore.exceptions import BotoCoreError, ClientError

from core.aws import DEMO_NAME, aws_client, validate_local


def initialize():
    validate_local()
    db, s3 = aws_client("dynamodb"), aws_client("s3")
    for attempt in range(60):
        try:
            db.list_tables()
            s3.list_buckets()
            break
        except (BotoCoreError, ClientError):
            if attempt == 59:
                raise
            time.sleep(1)
    try:
        db.describe_table(TableName=DEMO_NAME)
    except db.exceptions.ResourceNotFoundException:
        db.create_table(
            TableName=DEMO_NAME,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": name,
                    "KeySchema": [
                        {"AttributeName": name + "PK", "KeyType": "HASH"},
                        {"AttributeName": name + "SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for name in ("GSI1", "GSI2")
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        db.get_waiter("table_exists").wait(TableName=DEMO_NAME)
    if not any(b["Name"] == DEMO_NAME for b in s3.list_buckets()["Buckets"]):
        s3.create_bucket(Bucket=DEMO_NAME)
    s3.put_bucket_versioning(Bucket=DEMO_NAME, VersioningConfiguration={"Status": "Enabled"})
    return {"table": DEMO_NAME, "bucket": DEMO_NAME, "versioning": "Enabled"}


def reset_demo(*, confirm=False):
    validate_local()
    if not confirm:
        raise ValueError("reset-demo requires explicit confirmation")
    db = aws_client("dynamodb")
    try:
        db.delete_table(TableName=DEMO_NAME)
        db.get_waiter("table_not_exists").wait(TableName=DEMO_NAME)
    except db.exceptions.ResourceNotFoundException:
        pass
    # Rebuild disposable table state; immutable S3 evidence and exact versions survive.
    return initialize()


def s3_contract(s3, bucket):
    """Retain the small test versions as evidence; never delete data implicitly."""
    key = "demo-contract/versioned.txt"
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    first = s3.put_object(Bucket=bucket, Key=key, Body=b"first", Metadata={"proof": "phase4a"})
    second = s3.put_object(Bucket=bucket, Key=key, Body=b"second")
    assert first.get("VersionId") and second.get("VersionId")
    assert first["VersionId"] != second["VersionId"]
    old = s3.get_object(Bucket=bucket, Key=key, VersionId=first["VersionId"])
    assert old["Body"].read() == b"first" and old["Metadata"] == {"proof": "phase4a"}
    assert s3.get_object(Bucket=bucket, Key=key)["Body"].read() == b"second"
    assert s3.head_object(Bucket=bucket, Key=key)["VersionId"] == second["VersionId"]
    assert any(o["Key"] == key for o in s3.list_objects_v2(Bucket=bucket)["Contents"])
    versions = s3.list_object_versions(Bucket=bucket, Prefix=key)["Versions"]
    assert first["VersionId"] in {o["VersionId"] for o in versions}
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=b"overwrite", IfNoneMatch="*")
    except ClientError as error:
        assert error.response["ResponseMetadata"]["HTTPStatusCode"] == 412
    else:
        raise AssertionError("S3 ignored IfNoneMatch; immutable evidence unsafe")
    assert s3.get_object(Bucket=bucket, Key=key)["Body"].read() == b"second"
    return {"versioned_reads": True, "metadata": True, "lists": True, "conditional_writes": True}


def require_empty(table, s3, bucket):
    """Recovery cursors and request counters are operational, not fixture data."""
    position = None
    while True:
        args = {"ConsistentRead": True, "Limit": 100}
        if position:
            args["ExclusiveStartKey"] = position
        page = table.scan(**args)
        for row in page["Items"]:
            key = (row["PK"], row["SK"])
            if key == ("V2#LOCAL#DEMO", "LEASE"):
                continue
            if row["PK"].startswith("RATELIMIT#"):
                continue
            if (
                key in {("V2#RECOVERY", "DIRTY"), ("V2#RECOVERY", "native-recovery")}
                and row.get("cursor") is None
            ):
                continue
            raise ValueError("seed-demo requires empty tournament storage; existing data retained")
        position = page.get("LastEvaluatedKey")
        if not position:
            break
    versions = s3.list_object_versions(Bucket=bucket, MaxKeys=1)
    if versions.get("Versions") or versions.get("DeleteMarkers"):
        raise ValueError("seed-demo requires empty object storage; retained versions preserved")
