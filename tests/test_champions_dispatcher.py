import importlib
import importlib.util

import boto3
import pytest

from tests.source_helpers import INDEX, real_inputs


def api():
    assert importlib.util.find_spec("scraper.dispatcher"), "Explicit source dispatcher is missing"
    return importlib.import_module("scraper.dispatcher")


class CapturedClient:
    def __init__(self, source, fail=False):
        self.capture = real_inputs(source)[0]
        self.fail = fail
        self.calls = []

    def get_tournaments(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("Source failed")
        return [{"id": self.capture.source_event_id}]

    def capture_tournament(self, event):
        if self.fail:
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


def test_both_explicitly_admitted_adapters_stage_without_raw_writes(s3_bucket):
    admissions = [
        real_inputs(s)[1].model_copy(
            update={"enabled": True, "event_ids": [real_inputs(s)[0].source_event_id]}
        )
        for s in ["limitless", "rk9"]
    ]
    report = api().collect_sources(
        admissions=admissions,
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        clients={s: CapturedClient(s) for s in ["limitless", "rk9"]},
    )
    assert all(
        report.sources[s].captured == 1 and report.sources[s].failed == 0
        for s in ["limitless", "rk9"]
    )
    assert all(obj.key.startswith("staging/v2/") for obj in s3_bucket.objects.all())
    assert len(list(s3_bucket.objects.all())) == 4


def test_failure_raises_after_independent_valid_work_is_staged(s3_bucket):
    admissions = [
        real_inputs(s)[1].model_copy(
            update={"enabled": True, "event_ids": [real_inputs(s)[0].source_event_id]}
        )
        for s in ["limitless", "rk9"]
    ]
    with pytest.raises(api().CollectionFailure) as error:
        api().collect_sources(
            admissions=admissions,
            catalog_index=INDEX,
            s3_client=boto3.client("s3"),
            bucket_name=s3_bucket.name,
            clients={
                "limitless": CapturedClient("limitless", fail=True),
                "rk9": CapturedClient("rk9"),
            },
        )
    assert error.value.report.sources["limitless"].failed == 1
    assert error.value.report.sources["rk9"].captured == 1
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
