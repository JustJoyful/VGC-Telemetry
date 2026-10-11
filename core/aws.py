"""AWS defaults, or explicit fail-closed clients for the isolated local demo."""

import os
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

DEMO_NAME = "vgc-telemetry-demo"


def local_mode() -> bool:
    value = os.environ.get("VGC_LOCAL_DEMO", "false")
    if value not in {"true", "false"}:
        raise ValueError("VGC_LOCAL_DEMO must be true or false")
    return value == "true"


def validate_local() -> dict[str, str]:
    if not local_mode():
        raise ValueError("Explicit VGC_LOCAL_DEMO=true required")
    for key, expected in {
        "AWS_ACCESS_KEY_ID": "localdemo",
        "AWS_SECRET_ACCESS_KEY": "local-demo-secret",
        "DYNAMODB_TABLE_NAME": DEMO_NAME,
        "S3_RAW_BUCKET_NAME": DEMO_NAME,
    }.items():
        if os.environ.get(key) != expected:
            raise ValueError(f"Local demo requires exact {key}")
    if any(os.environ.get(k) for k in ("AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN", "AWS_PROFILE")):
        raise ValueError("Local demo forbids ambient AWS profiles/session credentials")
    endpoints = {}
    for service, variable, container_origin in (
        ("dynamodb", "DYNAMODB_ENDPOINT_URL", "http://dynamodb:8000"),
        ("s3", "S3_ENDPOINT_URL", "http://storage:9000"),
    ):
        value = os.environ.get(variable, "")
        try:
            url = urlsplit(value)
            valid = (
                url.scheme == "http"
                and url.hostname in {"127.0.0.1", "localhost", "::1"}
                and url.port is not None
                and not url.username
                and not url.password
                and not url.path
                and not url.query
                and not url.fragment
            )
        except ValueError:
            valid = False
        if os.environ.get("VGC_LOCAL_CONTAINER") == "true" and value == container_origin:
            valid = True
        if not valid:
            raise ValueError(f"Missing or unsafe local {variable}")
        endpoints[service] = value
    return endpoints


def _options(service: str, options: dict) -> dict:
    endpoints = validate_local()
    if service not in endpoints:
        raise ValueError(f"Unsupported local service: {service}; use direct worker calls")
    config = options.pop("config", Config())
    config = config.merge(Config(proxies={}, s3={"addressing_style": "path"}))
    return options | {
        "endpoint_url": endpoints[service],
        "aws_access_key_id": "localdemo",
        "aws_secret_access_key": "local-demo-secret",
        "aws_session_token": None,
        "region_name": "us-east-1",
        "config": config,
    }


def _guard_resources(params, **kwargs):
    names = [params[key] for key in ("Bucket", "TableName") if key in params]
    names.extend(params.get("RequestItems", {}).keys())
    for entry in params.get("TransactItems", []):
        for operation in entry.values():
            if "TableName" in operation:
                names.append(operation["TableName"])
    if any(name != DEMO_NAME for name in names):
        raise ValueError("Local demo forbids non-demo resource requests")


def aws_client(service: str, **options):
    enabled = local_mode()
    client = boto3.client(service, **(_options(service, options) if enabled else options))
    if enabled:
        client.meta.events.register("before-parameter-build.*.*", _guard_resources)
    return client


def aws_resource(service: str, **options):
    enabled = local_mode()
    resource = boto3.resource(service, **(_options(service, options) if enabled else options))
    if enabled:
        resource.meta.client.meta.events.register("before-parameter-build.*.*", _guard_resources)
    return resource
