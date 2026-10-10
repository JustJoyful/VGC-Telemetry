from datetime import UTC, datetime

import boto3

from tests.source_helpers import INDEX, real_inputs
from tests.test_catalog_candidates import candidate


def test_collector_and_worker_resolve_retained_pinned_catalog(dynamodb_table, s3_bucket):
    from db.catalog import CatalogStore
    from scraper.dispatcher import collect_sources, run_collection_worker

    s3 = boto3.client("s3")
    store = CatalogStore(table=dynamodb_table, s3_client=s3, bucket=s3_bucket.name)
    catalog = candidate()
    store.save(catalog)
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(
        deep=True, update={"enabled": True, "event_ids": [capture.source_event_id]}
    )
    for mapping in admission.format_mappings.values():
        mapping.catalog_revision = catalog.snapshot.snapshot_id

    class Client:
        def capture_tournament(self, event_id):
            return capture

    class Invoker:
        calls = 0

        def invoke(self, **kwargs):
            self.calls += 1
            return {"StatusCode": 202}

    report = collect_sources(
        admissions=[admission],
        catalog_index=INDEX,
        s3_client=s3,
        bucket_name=s3_bucket.name,
        clients={"limitless": Client()},
        catalog_store=store,
    )
    assert report.sources["limitless"].failed == 0
    invoker = Invoker()
    worker = run_collection_worker(
        admission=admission,
        catalog_index=INDEX,
        s3_client=s3,
        bucket_name=s3_bucket.name,
        client=Client(),
        table=dynamodb_table,
        invoke_client=invoker,
        ingestion_function="ingestion",
        mode="backfill",
        backfill_ids=[capture.source_event_id],
        now=datetime(2026, 10, 10, tzinfo=UTC),
    )
    assert worker.failed == 0 and worker.pending == 0 and invoker.calls == 1
