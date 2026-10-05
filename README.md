# VGC Telemetry

A REST API project for **Pokémon Champions tournament analysis**: registered teams, published builds, Pokémon/form usage, moves, abilities, items, natures, Stat Points, Mega capability, and team cores.

## Project status

The repository contains a working prototype and offline tests, not a verified production data service. Existing `/v1` routes serve tournament teams, player history, and species/move/ability/legacy-spread/partner counts. Phase 0 adds verified real-source fixtures, native/partial contracts, M-C/M-B catalogs, and isolated previews. Phase 1 adds isolated revision and generation publication with offline replay proof. Automatic catalog updates remain pending.

The target `/v2` API reliably serves verified Limitless Pokémon Champions VGC top-cut tournament data, preserves historical regulations, and reports provenance, sample coverage, and freshness. Limitless is the only initial tournament source. Ranked ladder feeds and brittle or mismatched sources are out of scope. RK9 is excluded because its published Terms prohibit automated screen scraping.

Limitless already provides an [upstream API](https://docs.limitlesstcg.com/developer/tournaments). This project's value is Champions-specific legality, reproducible analysis, coverage, and freshness beyond a direct API copy. Additional sources require a separate access review.

## Domain

Champions exclusively; generic VGC metadata does not establish the game. Native builds use **Stat Points** and nature/stat alignment. IV distributions are not a Champions tournament metric. Missing private fields remain unknown, and registered Mega capability does not prove battle activation. Legal pools are versioned by regulation; future updates may add or remove entries. Current mechanics do not include Terastallization.

## Stack and layout

Python 3.12+, FastAPI, Pydantic, boto3/httpx, DynamoDB, S3, AWS Lambda, Docker, and Terraform; uv, pytest/Moto, and Ruff for development.

- `app/`: routes, presentation, queries, distributed rate limiting.
- `core/`: shared contracts and current prototype regulation configuration.
- `db/`: DynamoDB client and key helpers.
- `scraper/`, `ingestion/`: source clients, normalization, writing, quarantine.
- `tests/`: offline unit and integration tests.
- `infra/`, `.github/workflows/`: infrastructure definitions and CI/deploy workflows.

## Local development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
uv run uvicorn app.main:app --reload
```

Visit `/docs` for the current OpenAPI interface. Data endpoints require configured DynamoDB access; tests use mocked AWS resources. Environment settings include `AWS_DEFAULT_REGION`, `DYNAMODB_TABLE_NAME`, `S3_RAW_BUCKET_NAME`, and the current prototype's `ACTIVE_REGULATION`. Do not commit credentials.

```bash
uv run pytest -v --cov=core --cov=db --cov=app --cov=ingestion --cov=scraper
docker build --target api -t vgc-telemetry:api .
terraform fmt -check -recursive infra/
```

Other Docker targets are `ingestion` and `scraper`. CI reports coverage without enforcing a numerical threshold. Offline tests do not verify real upstream responses, AWS capacity, or deployment.

## Phase 0 preview

Preview captured public data locally without AWS credentials:

```bash
uv run python -m ingestion.preview \
  --capture tests/fixtures/sources/limitless/accepted-capture.json \
  --admission core/sources/admission.json \
  --catalog-index core/regulations/v2/index.json \
  --output /tmp/champions-phase-0-preview.json
```

Preview JSON reports observed rosters, unknown fields, and diagnostics; it is a local verification artifact. The captured Limitless teams contain no published Stat Points, so their allocations remain unknown.

The Limitless source in `core/sources/admission.json` ships disabled. The collector writes immutable `staging/v2/captures/` and `staging/v2/normalized/` objects. The legacy writer skips these prefixes and schema-2 payloads. Configure `SOURCE_ADMISSION_PATH` and `CATALOG_INDEX_PATH` to supply an independently reviewed source configuration. Access refusals fail visibly.

Catalogs use official regulation/eligible-pool evidence and pinned MIT-licensed community mechanics. See [catalog provenance](core/regulations/v2/README.md) for inventory and reproduction. M-A and intermediate historical patches remain unverified.

## Development documents

The repository's context documents are currently gitignored but remain part of the local workspace. Read them when present; do not assume they are unavailable because of ignore rules.

- [PRD.md](PRD.md): product scope, sources, API target, release acceptance.
- [ARCHITECTURE.md](ARCHITECTURE.md): current prototype and proposed revision-safe design.
- [PHASES.md](PHASES.md): delivery sequence and implementation gates.
- [RULES.md](RULES.md): domain, data, reliability, and operational constraints.
- [MEMORY.md](MEMORY.md): verified baseline, open bugs, decisions.
- [SOURCE_UPDATE.md](SOURCE_UPDATE.md): source and release evidence checklist.

The old `phase-2-plan.md` is superseded. Phase 0's admitted Limitless input-to-preview gate is complete: 120 offline tests pass with 92% coverage and clean Ruff checks. The [Phase 1 publication plan](docs/superpowers/plans/2026-10-05-phase-1-publication.md) implements the separate native publication foundation. Phase 1 is complete offline: 184 tests pass, with 93% measured coverage and clean Ruff checks. See MEMORY.md for evidence; public `/v2` and automated updates remain later gates.

## Hosting

Terraform defines dev/prod provisioned DynamoDB and Lambda Function URL hosting. Deployment requires verified AWS prerequisites and same-revision CI gates. Reserved concurrency limits simultaneous execution; budget alerts notify. Neither guarantees a zero bill, and both environments share account-level cost considerations.

## Native publication and replay

The explicit `ingestion.native_handler.handle_native_event` accepts staged references:

```python
from ingestion.native_handler import handle_native_event

handle_native_event(
    {
        "normalized": [
            {
                "bucket": "your-staging-bucket",
                "key": "staging/v2/normalized/<sha256>.json",
                "version_id": "exact-s3-version",  # omit only for a verified hash-addressed object
            }
        ]
    }
)
# Recovery without a new delivery: repeat until the outcome is published.
handle_native_event({"recover": True})
```

This is an explicit operator invocation, with DynamoDB/S3 access, not a configured
production trigger or automatic schedule. `max_steps` bounds each population's build
work per call (default 100); `pending` means retry/resume, not completion. Recovery
continues past busy populations and pages both intent and dirty-generation jobs. Build cursors
persist in DynamoDB; intent recovery advances a durable bounded cursor. Operational
batch failures raise with per-record `results` after independent successes.

Native rows use `V2#` namespaces. Content identity excludes volatile evidence timestamps;
capture fetch time supplies ordering until upstream sequence evidence is available.
Equal-order conflicting content remains pending. Contributions cover observed entries,
with top-cut eligibility unknown; they are not public top-cut analytics. Legacy `/v1`
keys and EV units retain their existing meaning and legacy aggregation limitations.

Reproduce offline replay, crashes, corrections, reassignment, and clean-rebuild checks:

```bash
uv run --frozen pytest tests/test_revision_inputs.py tests/test_revision_contributions.py -q
uv run --frozen pytest tests/test_publication_promotion.py tests/test_publication_recovery.py -q
uv run --frozen pytest tests/test_publication_generations.py tests/test_publication_handler.py tests/test_publication_pipeline.py -q
```

Captures, revisions, manifests, complete and incomplete generations are retained
indefinitely; no cleanup or TTL is enabled. Readers pin a complete generation ID;
previous complete generations stay readable. Automated expiration requires a reviewed
retention policy that protects publication pointers, active builds, and reader windows.
Offline Moto checks establish logic, not AWS throughput, permissions, or deployment.
