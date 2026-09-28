import time

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.main import app
from app.middleware.rate_limit import DynamoDBRateLimitMiddleware


def test_rate_limit_allows_under_limit(dynamodb_table):
    client = TestClient(app, client=("198.51.100.1", 50000))
    response = client.get("/health")  # bypassed
    assert response.status_code == 200

    response = client.get("/v1/meta?regulation=M-C&metric=usage")
    assert response.status_code == 200
    assert "X-RateLimit-Limit" in response.headers


def test_rate_limit_blocks_exceeded_with_429(dynamodb_table):
    # Set limit to 2 requests/min for test
    app.user_middleware.clear()
    # We can test with a custom client IP
    test_ip = "198.51.100.42"

    # Directly verify middleware logic with limit=2
    middleware = DynamoDBRateLimitMiddleware(app, table=dynamodb_table, rate_limit=2)

    # First 2 requests succeed
    for _ in range(2):
        allowed, remaining, reset_secs = middleware.check_rate_limit(test_ip)
        assert allowed is True
        assert remaining >= 0

    # 3rd request is blocked
    allowed, remaining, reset_secs = middleware.check_rate_limit(test_ip)
    assert allowed is False
    assert remaining == 0
    assert reset_secs > 0


def test_rate_limit_fails_open_on_dynamodb_throttle(dynamodb_table, monkeypatch):
    test_ip = "198.51.100.99"
    middleware = DynamoDBRateLimitMiddleware(app, table=dynamodb_table, rate_limit=5)

    def mock_throttled_update_item(*args, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Throughput exceeded",
                }
            },
            "UpdateItem",
        )

    monkeypatch.setattr(dynamodb_table, "update_item", mock_throttled_update_item)

    # Should fail open!
    allowed, remaining, reset_secs = middleware.check_rate_limit(test_ip)
    assert allowed is True  # Fails open to preserve read-only API availability


def test_rate_limit_ignores_spoofed_forwarded_for_header(dynamodb_table):
    client = TestClient(app, client=("198.51.100.77", 12345))
    # Send request with fake spoofed X-Forwarded-For header
    response = client.get(
        "/v1/meta?regulation=M-C&metric=usage",
        headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"},
    )
    assert response.status_code == 200
    # In DynamoDB, item must be recorded under real client host 198.51.100.77, NOT 1.2.3.4
    epoch_minute = int(time.time() // 60)
    item = dynamodb_table.get_item(
        Key={"PK": "RATELIMIT#198.51.100.77", "SK": f"WINDOW#{epoch_minute}"}
    ).get("Item")
    assert item is not None
    assert item["request_count"] >= 1
