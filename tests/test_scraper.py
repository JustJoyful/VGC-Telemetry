"""Collector regressions migrated from the retired guessed raw-export path."""

import json

import boto3
import httpx
import pytest

from scraper.dispatcher import CollectionFailure, CollectionReport, SourceReport, collect_sources
from scraper.limitless_client import LimitlessAPIClient
from tests.source_helpers import INDEX, real_inputs


def fixture_client(fail_path=None):
    capture, admission, _ = real_inputs("limitless")

    def respond(request):
        if request.url.path == fail_path:
            return httpx.Response(500, text="Server Error")
        if request.url.path == "/api/tournaments":
            return httpx.Response(200, json=[{"id": capture.source_event_id}])
        for doc in capture.documents.values():
            if str(request.url).split("?")[0] == doc.url:
                return httpx.Response(doc.status, content=doc.body.encode())
        return httpx.Response(404)

    return LimitlessAPIClient(
        client=httpx.Client(transport=httpx.MockTransport(respond))
    ), admission.model_copy(update={"enabled": True})


def test_limitless_client_fetch_and_capture():
    client, _ = fixture_client()
    capture = client.capture_tournament(real_inputs("limitless")[0].source_event_id)
    assert capture.source == "limitless"
    assert capture.documents["details"].url.endswith("/details")
    standings = json.loads(capture.documents["standings"].body)
    assert standings[0]["player"] == "azelle" and len(standings[0]["decklist"]) == 6
    assert len(json.loads(capture.documents["pairings"].body)) > 0


def test_scraper_handler_writes_to_staging_prefix(s3_bucket):
    client, admission = fixture_client()
    report = collect_sources(
        admissions=[admission],
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        clients={"limitless": client},
    )
    refs = report.sources["limitless"].staged_references
    assert len(refs) == 1
    assert refs[0]["capture_key"].startswith("staging/v2/captures/")
    data = json.loads(s3_bucket.Object(refs[0]["normalized_key"]).get()["Body"].read())
    assert data["result"]["tournament"]["metadata"]["event_id"].startswith("limitless:")
    assert all(not obj.key.startswith("raw/") for obj in s3_bucket.objects.all())


def test_scraper_handler_list_fetch_error(s3_bucket):
    client, admission = fixture_client("/api/tournaments")
    with pytest.raises(CollectionFailure) as error:
        collect_sources(
            admissions=[admission],
            catalog_index=INDEX,
            s3_client=boto3.client("s3"),
            bucket_name=s3_bucket.name,
            clients={"limitless": client},
        )
    assert error.value.report.sources["limitless"].failed == 1


def test_scraper_handler_single_capture_error(s3_bucket):
    capture, _, _ = real_inputs("limitless")
    client, admission = fixture_client(f"/api/tournaments/{capture.source_event_id}/standings")
    with pytest.raises(CollectionFailure) as error:
        collect_sources(
            admissions=[admission],
            catalog_index=INDEX,
            s3_client=boto3.client("s3"),
            bucket_name=s3_bucket.name,
            clients={"limitless": client},
        )
    assert error.value.report.sources["limitless"].failed == 1
    assert (
        len(list(s3_bucket.objects.all())) == 2
    )  # HTTP failure evidence and explicit pending result.


def test_scraper_lambda_handler(s3_bucket, monkeypatch):
    from scraper.handler import lambda_handler

    report = CollectionReport(sources={"limitless": SourceReport(captured=1)})
    monkeypatch.setattr("scraper.handler.collect_sources", lambda **kwargs: report)
    result = lambda_handler({})
    assert (
        result["status"] == "success" and result["report"]["sources"]["limitless"]["captured"] == 1
    )
