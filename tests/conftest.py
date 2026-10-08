import os
from typing import Generator

import boto3
import pytest
from moto import mock_aws

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "mock_key"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock_secret"
os.environ["DYNAMODB_TABLE_NAME"] = "vgc-telemetry-dev"
os.environ["S3_RAW_BUCKET_NAME"] = "vgc-telemetry-raw-dev"
os.environ["S3_QUARANTINE_PREFIX"] = "quarantine/"


@pytest.fixture(autouse=True)
def release_moto_transaction_dashboard_copies(monkeypatch):
    """Keep Moto's real rollback implementation; discard dashboard-only copies.

    Moto 5 records every BaseModel copy in a strong global dashboard list. Its
    transaction backups otherwise retain entire table copies until mock shutdown.
    Backend tables and rollback state retain their own live references.
    """
    from moto.core.model_instances import reset_model_data
    from moto.dynamodb.models import DynamoDBBackend

    original = DynamoDBBackend.transact_write_items

    def transact(self, actions):
        try:
            return original(self, actions)
        finally:
            reset_model_data()

    monkeypatch.setattr(DynamoDBBackend, "transact_write_items", transact)


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def dynamodb_table(aws_credentials) -> Generator:
    """Create a mock DynamoDB single-table with GSIs in Provisioned mode."""
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        table_name = os.environ["DYNAMODB_TABLE_NAME"]

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 25,
                        "WriteCapacityUnits": 25,
                    },
                },
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 25,
                        "WriteCapacityUnits": 25,
                    },
                },
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={
                "ReadCapacityUnits": 25,
                "WriteCapacityUnits": 25,
            },
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)


@pytest.fixture
def s3_bucket(aws_credentials) -> Generator:
    """Create a mock S3 raw bucket."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = os.environ["S3_RAW_BUCKET_NAME"]
        s3.create_bucket(Bucket=bucket_name)
        yield boto3.resource("s3", region_name="us-east-1").Bucket(bucket_name)
