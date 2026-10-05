import importlib
import importlib.util

import boto3
import pytest

from tests.source_helpers import INDEX, real_inputs


def api():
    assert importlib.util.find_spec("scraper.dispatcher"), "Explicit source dispatcher is missing"
    return importlib.import_module("scraper.dispatcher")


class CapturedClient:
    def __init__(self, source, fail=False, fail_on_event=None):
        self.capture = real_inputs(source)[0]
        self.fail = fail
        self.fail_on_event = fail_on_event
        self.calls = []

    def get_tournaments(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("Source failed")
        return [{"id": self.capture.source_event_id}]

    def capture_tournament(self, event):
        if self.fail or event == self.fail_on_event:
            raise RuntimeError("Source failed")
        return self.capture


def test_disabled_and_unverified_do_not_collect(s3_bucket):
    _, admission, _ = real_inputs("limitless")
    client = CapturedClient("limitless")
    report = api().collect_sources(
        admissions=[admission],
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        clients={"limitless": client},
    )
    assert report.sources["limitless"].captured == 0 and client.calls == []
    with pytest.raises(ValueError):
        api().collect_sources(
            admissions=[admission.model_copy(update={"enabled": True, "verified": False})],
            catalog_index=INDEX,
            s3_client=boto3.client("s3"),
            bucket_name=s3_bucket.name,
            clients={"limitless": client},
        )
    assert client.calls == []


def test_admitted_limitless_adapter_stages_without_raw_writes(s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(
        update={"enabled": True, "event_ids": [capture.source_event_id]}
    )
    report = api().collect_sources(
        admissions=[admission],
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        clients={"limitless": CapturedClient("limitless")},
    )
    assert report.sources["limitless"].captured == 1
    assert report.sources["limitless"].failed == 0
    assert all(obj.key.startswith("staging/v2/") for obj in s3_bucket.objects.all())
    assert len(list(s3_bucket.objects.all())) == 2


def test_failure_raises_after_independent_valid_work_is_staged(s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(
        update={"enabled": True, "event_ids": [capture.source_event_id, "failed-event"]}
    )
    with pytest.raises(api().CollectionFailure) as error:
        api().collect_sources(
            admissions=[admission],
            catalog_index=INDEX,
            s3_client=boto3.client("s3"),
            bucket_name=s3_bucket.name,
            clients={"limitless": CapturedClient("limitless", fail_on_event="failed-event")},
        )
    assert error.value.report.sources["limitless"].failed == 1
    assert error.value.report.sources["limitless"].captured == 1
    assert len(list(s3_bucket.objects.all())) == 2


def test_parser_failure_retains_original_capture(s3_bucket, monkeypatch):
    module = api()
    _, a, _ = real_inputs("limitless")
    a = a.model_copy(update={"enabled": True, "event_ids": ["one"]})
    monkeypatch.setattr(
        module, "normalize_capture", lambda *a, **k: (_ for _ in ()).throw(ValueError("Bad body"))
    )
    with pytest.raises(module.CollectionFailure):
        module.collect_sources(
            admissions=[a],
            catalog_index=INDEX,
            s3_client=boto3.client("s3"),
            bucket_name=s3_bucket.name,
            clients={"limitless": CapturedClient("limitless")},
        )
    assert len(list(s3_bucket.objects.all())) == 1
    assert next(iter(s3_bucket.objects.all())).key.startswith("staging/v2/captures/")
