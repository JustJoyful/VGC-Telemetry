# VGC Telemetry

A REST API project for **Pokémon Champions tournament analysis**: registered teams, published builds, Pokémon/form usage, moves, abilities, items, natures, Stat Points, Mega capability, and team cores.

## Project status

The repository contains a working prototype and offline tests, not a verified production data service. Existing `/v1` routes serve tournament teams, player history, and species/move/ability/legacy-spread/partner counts. Phase 0 adds verified real-source fixtures, native/partial contracts, M-C/M-B catalogs, and isolated previews. Safe publication and automatic catalog updates remain under development.

The target `/v2` API combines official RK9 and online Limitless tournament records, enriches published builds with attributable public pastes, preserves historical regulations, and reports provenance, sample coverage, and freshness. Source adapters require verified real samples before enablement.

Limitless already provides an [upstream API](https://docs.limitlesstcg.com/developer/tournaments). This project's value is consistent Champions data across sources and reproducible analysis, rather than a copy of that API.

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

Use `tests/fixtures/sources/rk9/accepted-capture.json` for the RK9 sample. Preview JSON reports observed rosters, unknown fields, and diagnostics; it is a local verification artifact. Public Stat Points are unavailable in these samples. RK9 includes two captured sheets and explicitly marks other linked sheets uncaptured.

Both sources in `core/sources/admission.json` ship disabled. The collector writes immutable `staging/v2/captures/` and `staging/v2/normalized/` objects. The legacy writer skips these prefixes and schema-2 payloads. Configure `SOURCE_ADMISSION_PATH` and `CATALOG_INDEX_PATH` to supply an independently reviewed source configuration. RK9's `public_scraping_authorized` records the operator's explicit scraping policy; access refusals still fail visibly.

Catalogs use official regulation/eligible-pool evidence and pinned MIT-licensed community mechanics. See [catalog provenance](core/regulations/v2/README.md) for inventory and reproduction. M-A and intermediate historical patches remain unverified.

## Development documents

The repository's context documents are currently gitignored but remain part of the local workspace. Read them when present; do not assume they are unavailable because of ignore rules.

- [PRD.md](PRD.md): product scope, sources, API target, release acceptance.
- [ARCHITECTURE.md](ARCHITECTURE.md): current prototype and proposed revision-safe design.
- [PHASES.md](PHASES.md): delivery sequence and implementation gates.
- [RULES.md](RULES.md): domain, data, reliability, and operational constraints.
- [MEMORY.md](MEMORY.md): verified baseline, open bugs, decisions.
- [SOURCE_UPDATE.md](SOURCE_UPDATE.md): source and release evidence checklist.

The old `phase-2-plan.md` is superseded. Current documents distinguish planned behavior from implemented code. Phase 0 inputs and previews are implemented; revision-safe publication is the next milestone.

## Hosting

Terraform defines dev/prod provisioned DynamoDB and Lambda Function URL hosting. Deployment requires verified AWS prerequisites and same-revision CI gates. Reserved concurrency limits simultaneous execution; budget alerts notify. Neither guarantees a zero bill, and both environments share account-level cost considerations.
