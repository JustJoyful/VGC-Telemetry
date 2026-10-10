import json
import logging

from core.telemetry import NAMESPACE, RunTelemetry
from ingestion.native_handler import resume_public
from ingestion.retry import retry


class CloudWatch:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("secret metric transport")


def test_metric_contract_and_redacted_logs(monkeypatch, caplog):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    client = CloudWatch()
    telemetry = RunTelemetry("native", source="limitless", client=client)
    with caplog.at_level(logging.INFO):
        telemetry.emit("Quarantines")
        telemetry.emit("PublicationLag", 3601)
        telemetry.failure(ValueError("raw team: SECRET player-id"), event_ref="event-1")
        telemetry.finish("error")
    assert all(call["Namespace"] == NAMESPACE for call in client.calls)
    data = [call["MetricData"][0] for call in client.calls]
    assert [(d["MetricName"], d["Unit"]) for d in data] == [
        ("Quarantines", "Count"),
        ("PublicationLag", "Seconds"),
        ("WorkerErrors", "Count"),
    ]
    assert data[0]["Dimensions"] == [
        {"Name": "Environment", "Value": "dev"},
        {"Name": "Source", "Value": "limitless"},
        {"Name": "Worker", "Value": "native"},
    ]
    assert "SECRET" not in caplog.text and "player-id" not in caplog.text
    records = [json.loads(record.message) for record in caplog.records]
    assert records[0]["event_ref"] == "event-1"
    assert records[0]["error_class"] == "ValueError"
    assert records[-1]["counts"] == {"Quarantines": 1, "WorkerErrors": 1}


def test_metric_delivery_failure_does_not_hide_original(caplog):
    telemetry = RunTelemetry("native", client=CloudWatch(fail=True))
    error = ValueError("original")
    with caplog.at_level(logging.WARNING):
        try:
            telemetry.failure(error)
            raise error
        except ValueError as caught:
            assert caught is error
    assert "secret metric transport" not in caplog.text


def test_retry_exhaustion_is_once_per_operation(caplog):
    from botocore.exceptions import ClientError

    client = CloudWatch()
    telemetry = RunTelemetry("native", client=client)

    def fail():
        raise ClientError({"Error": {"Code": "Throttling"}}, "GetObject")

    try:
        retry(fail, sleep=lambda _: None, random=lambda: 0, on_retry=telemetry.retry)
    except ClientError:
        pass
    assert telemetry.counts["RetryAttempts"] == 3
    assert telemetry.counts["RetryExhaustions"] == 1
    record = json.loads(caplog.records[-1].message)
    assert record["retry_count"] == 3 and record["result"] == "exhausted"


def test_recovery_lag_reports_pending_and_idle_without_fake_publication(monkeypatch):
    class Table:
        row = {"dirty": False}

        def get_item(self, **kwargs):
            return {"Item": self.row}

    table = Table()
    metrics = CloudWatch()
    telemetry = RunTelemetry("native", client=metrics)
    kwargs = dict(
        table=table,
        s3_client=None,
        owner="owner",
        clock=lambda: 5000,
        run=lambda f: f(),
        max_steps=1,
        telemetry=telemetry,
    )
    assert resume_public(**kwargs)
    assert telemetry.counts["Publications"] == 0
    assert metrics.calls[-1]["MetricData"][0]["Value"] == 0
    table.row = {"dirty": True, "first_validated_at": 1000}
    monkeypatch.setattr(
        "ingestion.native_handler.acquire_lease",
        lambda **_: (_ for _ in ()).throw(RuntimeError("stop")),
    )
    try:
        resume_public(**kwargs)
    except RuntimeError:
        pass
    assert metrics.calls[-1]["MetricData"][0]["Value"] == 4000
    assert telemetry.counts["Publications"] == 0
    table.row = {"dirty": True}
    before = len(
        [call for call in metrics.calls if call["MetricData"][0]["MetricName"] == "PublicationLag"]
    )
    try:
        resume_public(**kwargs)
    except RuntimeError:
        pass
    after = len(
        [call for call in metrics.calls if call["MetricData"][0]["MetricName"] == "PublicationLag"]
    )
    assert before == after
