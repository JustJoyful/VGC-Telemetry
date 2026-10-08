import pytest
from fastapi.testclient import TestClient

from tests.phase2_helpers import publish_input
from tests.test_public_snapshot import finish, held


@pytest.fixture
def v2_client(dynamodb_table, s3_bucket, monkeypatch):
    from app.db.public_queries import PublicReader, get_public_reader
    from app.main import app
    from app.middleware.rate_limit import DynamoDBRateLimitMiddleware
    from app.v2.config import V2ReadConfig

    value, s3 = publish_input(dynamodb_table, s3_bucket)
    lease = held(dynamodb_table)
    finish(dynamodb_table, s3, lease)
    from db.leases import release_lease

    release_lease(lease, table=dynamodb_table)
    monkeypatch.setattr(DynamoDBRateLimitMiddleware, "check_rate_limit", lambda *_: (True, 60, 60))
    config = V2ReadConfig(secret=b"test-only-config-secret" * 2, clock=lambda: 100)
    app.dependency_overrides[get_public_reader] = lambda: PublicReader(
        dynamodb_table, config=config
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, value, dynamodb_table, s3
    app.dependency_overrides.pop(get_public_reader)
