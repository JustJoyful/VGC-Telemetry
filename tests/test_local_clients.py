from unittest.mock import Mock

import pytest


@pytest.fixture
def local_env(monkeypatch):
    for key in ("AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "VGC_LOCAL_DEMO": "true",
        "AWS_ACCESS_KEY_ID": "localdemo",
        "AWS_SECRET_ACCESS_KEY": "local-demo-secret",
        "DYNAMODB_TABLE_NAME": "vgc-telemetry-demo",
        "S3_RAW_BUCKET_NAME": "vgc-telemetry-demo",
        "DYNAMODB_ENDPOINT_URL": "http://127.0.0.1:8000",
        "S3_ENDPOINT_URL": "http://localhost:9000",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("kind", ["client", "resource"])
def test_local_routes_without_ambient_credentials(local_env, monkeypatch, kind):
    from core.aws import aws_client, aws_resource

    spy = Mock()
    monkeypatch.setattr(f"boto3.{kind}", spy)
    (aws_client if kind == "client" else aws_resource)("dynamodb")
    assert spy.call_args.kwargs["endpoint_url"] == "http://127.0.0.1:8000"
    assert spy.call_args.kwargs["aws_access_key_id"] == "localdemo"


@pytest.mark.parametrize(
    "key,value",
    [
        ("S3_ENDPOINT_URL", ""),
        ("S3_ENDPOINT_URL", "https://s3.amazonaws.com"),
        ("S3_ENDPOINT_URL", "http://localhost.evil:9000"),
        ("S3_ENDPOINT_URL", "http://user:password@localhost:9000"),
        ("S3_ENDPOINT_URL", "http://localhost:9000/path"),
        ("AWS_ACCESS_KEY_ID", "real-key"),
        ("AWS_SESSION_TOKEN", "real-token"),
        ("DYNAMODB_TABLE_NAME", "production"),
        ("S3_RAW_BUCKET_NAME", "production"),
    ],
)
def test_bad_config_fails_before_client(local_env, monkeypatch, key, value):
    from core.aws import aws_client

    monkeypatch.setenv(key, value)
    spy = Mock(side_effect=AssertionError("client constructed"))
    monkeypatch.setattr("boto3.client", spy)
    with pytest.raises(ValueError):
        aws_client("s3")
    spy.assert_not_called()


def test_telemetry_never_constructs_cloudwatch(local_env, monkeypatch):
    from core.telemetry import RunTelemetry

    spy = Mock(side_effect=AssertionError("CloudWatch constructed"))
    monkeypatch.setattr("boto3.client", spy)
    RunTelemetry("local").emit("Captures")
    spy.assert_not_called()


def test_lambda_forbidden_in_local_mode(local_env):
    from core.aws import aws_client

    with pytest.raises(ValueError, match="Unsupported local service"):
        aws_client("lambda")


def test_compose_origins_require_container_marker(local_env, monkeypatch):
    from core.aws import aws_client

    monkeypatch.setenv("S3_ENDPOINT_URL", "http://storage:9000")
    with pytest.raises(ValueError):
        aws_client("s3")
    monkeypatch.setenv("VGC_LOCAL_CONTAINER", "true")
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", "http://dynamodb:8000")
    spy = Mock()
    monkeypatch.setattr("boto3.client", spy)
    aws_client("s3")
    assert spy.call_args.kwargs["endpoint_url"] == "http://storage:9000"


def test_aws_defaults_unchanged(monkeypatch):
    from core.aws import aws_client

    monkeypatch.delenv("VGC_LOCAL_DEMO", raising=False)
    spy = Mock()
    monkeypatch.setattr("boto3.client", spy)
    aws_client("s3")
    spy.assert_called_once_with("s3")


@pytest.mark.parametrize(
    "service,operation,params",
    [
        ("dynamodb", "delete_table", {"TableName": "production"}),
        ("s3", "delete_object", {"Bucket": "production", "Key": "some-key"}),
        ("dynamodb", "batch_write_item", {"RequestItems": {"production": []}}),
        (
            "dynamodb",
            "transact_write_items",
            {
                "TransactItems": [
                    {
                        "Delete": {
                            "TableName": "production",
                            "Key": {"PK": {"S": "a"}, "SK": {"S": "b"}},
                        }
                    }
                ]
            },
        ),
    ],
)
def test_request_resource_names_guarded_before_network(
    local_env, monkeypatch, service, operation, params
):
    from core.aws import aws_client

    client = aws_client(service)
    network = Mock(side_effect=AssertionError("network attempted"))
    monkeypatch.setattr(client, "_make_request", network)
    with pytest.raises(ValueError, match="non-demo resource"):
        getattr(client, operation)(**params)
    network.assert_not_called()
