# VGC Telemetry

VGC Telemetry is a Python API and web explorer for Pokémon Champions tournament data. It collects and publishes source-backed tournament records, serves bounded read-only analytics and reference data, and makes the evidence and unknowns behind those results visible. It runs entirely on your own machine with Docker: one command starts the app and fills it with the newest supported tournaments from Limitless, and another refreshes it later. An offline demo with retained fixture data is also included. Production AWS operation is not enabled.

## Contents

- [What it does](#what-it-does)
- [Project status and data limits](#project-status-and-data-limits)
- [Technology](#technology)
- [Quick start](#quick-start)
- [Live data: what to expect](#live-data-what-to-expect)
- [Run from a source checkout](#run-from-a-source-checkout)
- [HTTP API](#http-api)
- [Meta Explorer](#meta-explorer)
- [How the system works](#how-the-system-works)
- [Configuration](#configuration)
- [Tests and code quality](#tests-and-code-quality)
- [Docker images and AWS infrastructure](#docker-images-and-aws-infrastructure)
- [Repository layout](#repository-layout)
- [Troubleshooting](#troubleshooting)
- [Contributing and licensing](#contributing-and-licensing)

## What it does

- Exposes legacy `/v1` endpoints and a snapshot-based `/v2` API for tournament teams, events, player history, usage, cores, and Pokémon and move reference data.
- Publishes complete immutable data snapshots so a paginated query can continue against one consistent view while new data is ingested.
- Tracks source evidence and preserves incomplete or unavailable fields as unknown instead of filling them with guesses.
- Provides a server-rendered Meta Explorer for browsing Pokémon usage, cores, teams, reference details, API examples, and methodology.
- Includes a local Docker Compose stack with DynamoDB Local, S3-compatible object storage, an API, a worker, and a scheduler. An opt-in live command collects the newest supported tournaments from Limitless; a retained-fixture demo populates an empty stack without contacting any upstream source.
- Includes ingestion, bounded collection scheduling, recovery tooling, and Terraform definitions for a possible AWS deployment.

## Project status and data limits

The repository contains the API, web explorer, ingestion and publication pipeline, local operations, and AWS infrastructure code. CI runs lint, formatting, and Python tests. The existence of deployment automation and Terraform does not mean that a production deployment, AWS permissions, costs, live collection, or source continuity have been verified. See the [local operations guide](docs/runbooks/self-hosted-phase-4a.md), [query guide](docs/runbooks/phase-5-queries.md), [query validation record](docs/evidence/query-usability/verification.json), and [local stack proof](docs/local-operations/fresh-workspace-proof.json) for the scope and limits of recorded checks.

The bundled Limitless source admission (`core/sources/admission.json`) stays disabled, so plain `docker compose up` and the fixture demo never contact an upstream source. The live command enables that same evidence-backed admission only for your installation by writing the gitignored `.local/admission.json`; it does not add sources, formats, or platforms. Live collection depends on Limitless's public API remaining available and unchanged, and its publication was checked against current responses on 2026-10-25, not guaranteed for the future. Automated RK9 screen scraping is excluded under RK9's published terms.

Pokémon Champions builds use native **Stat Points**. The legacy `/v1` surface retains its original EV terminology and values; the project does not convert between them. Missing build fields and incomplete rosters remain unknown. A registered Mega capability indicates roster capability, not proof that a Mega transformation occurred in a match. See [catalog provenance and known limits](core/regulations/v2/README.md).

The offline fixture data is evidence for local development, not a live meta feed. Catalog snapshots identify their own sources and revisions. Historical regulation coverage such as M-A and intermediate game-data patches remains unverified.

## Technology

- **Runtime:** Python 3.12+, FastAPI, Pydantic, Uvicorn, boto3, and Jinja2.
- **Storage:** DynamoDB single-table records and versioned S3 objects. Local development uses DynamoDB Local and RustFS as an S3-compatible service.
- **Web UI:** Jinja2 templates, HTMX, DaisyUI 5, and Tailwind CSS 4. No client-side application framework is used.
- **Web asset build:** Node.js 22 or later, npm, and the exact frontend packages pinned in `package-lock.json`.
- **Infrastructure:** Terraform for AWS resources; Docker multi-stage images for the API, ingestion Lambda, and scraper Lambda.
- **Quality tools:** pytest, Moto, pytest-cov, and Ruff.

## Quick start

You need [Docker Engine](https://docs.docker.com/engine/install/) (or Docker Desktop) with the Compose plugin, `git`, and Python 3.9 or later on the host to run the helper script (standard library only). You do **not** need an AWS account, AWS credentials, Node.js, or uv. The first run downloads pinned images and builds the local application image, which takes a few minutes.

```bash
git clone https://github.com/JustJoyful/VGC-Telemetry.git
cd VGC-Telemetry
```

### Live data (recommended)

```bash
python3 scripts/local/stack.py live
```

This one command:

1. Creates `.env.local` from `local/compose.env.example` and `.local/admission.json` from the bundled admission (enabled for this installation only), if they do not exist yet.
2. Starts storage and the API, with the background worker and scheduler paused.
3. Runs a bounded foreground refresh: it reads up to 10 listing pages per supported regulation (M-C and M-B), captures the 10 newest discovered events, validates them through the normal ingestion pipeline, and publishes a complete snapshot.
4. Starts the worker and scheduler only after the foreground refresh has finished, so two collectors never run at once.
5. Prints a summary such as `Live refresh: pending - ...`, followed by counts and the Explorer URL.

Then open <http://127.0.0.1:8080/explorer>. The API reference is at <http://127.0.0.1:8080/docs>.

To fetch the newest events again later, whether the stack is running or stopped:

```bash
python3 scripts/local/stack.py refresh
python3 scripts/local/stack.py refresh --events 25   # capture more of the newest events now (1-100)
```

`refresh` pauses the worker and scheduler, refreshes in the foreground, then restores exactly what was running before, including when the refresh fails. A stopped stack is left stopped afterwards.

The outcome is one of:

| Status | Meaning |
| --- | --- |
| `completed` | The newest events were collected and published, and no discovered work is left. |
| `pending` | The newest events were published; other discovered events (older history) are still queued and the scheduler continues them while the stack runs. This is normal on a first install. |
| `no_eligible_events` | The source answered, but nothing new passed admission and validation (for example, only custom-rule or simulator events). |
| `failed` | The source was unreachable, refused or rate-limited past the retry limit, or the configuration is invalid. The last complete snapshot is kept. The command exits non-zero. |
| `disabled` | `.local/admission.json` has `"enabled": false`. The command exits non-zero. |

### Offline fixture demo (no network)

```bash
python3 scripts/local/stack.py up
python3 scripts/local/stack.py seed-demo
```

`up` (the same as `docker compose up -d --build --wait`) never contacts an upstream source: the scheduler has no external network and schedules are disabled. `seed-demo` loads two retained public Limitless captures from `tests/fixtures/` into an **empty** installation and refuses to run if any tournament data or object versions exist. Use a separate clone or project for the demo if you also want live data.

### Stop, restart and what persists

```bash
python3 scripts/local/stack.py down       # stops containers; keeps your data
python3 scripts/local/stack.py live       # start again with live scheduling and a fresh refresh
python3 scripts/local/stack.py up         # or start again offline (no collection)
```

Your database and object storage live in the named Docker volumes `vgc-phase4a_dynamodb-data` and `vgc-phase4a_s3-data` and survive `down`, restarts, and image rebuilds. Do not use `docker compose down -v`; it deletes them. Scheduled collection runs only while the stack is running with the live overlay. Nothing collects while it is stopped, or after a plain `up`.

Each clone stores its own data locally. Fetched captures, the local database, `.env.local`, and `.local/` are gitignored and are never pushed to GitHub. This repository does not host or update a shared live dataset. See the [self-hosted operations guide](docs/runbooks/self-hosted-phase-4a.md) for backups, restore, recovery, and the optional loopback-only `admin` and `tunnel` profiles, which are disabled by default.

## Live data: what to expect

- **Coverage:** Limitless Pokémon Champions VGC tournaments in Regulation M-C and M-B, Nintendo Switch platform, doubles. Simulator events, other formats (including M-A), events with custom rules, and other sources are not collected or are reported as pending/rejected rather than relabelled. The analytics population is verified top-cut data; full standings are kept as labelled context.
- **Incomplete data stays unknown:** Limitless team lists do not include Champions Stat Points, so stat allocations are reported as unknown. Events without public team sheets, missing items, or unrecognised entries are kept with diagnostics instead of guessed. Not every event has team data.
- **Rate limits:** Limitless advertises a limit of 50 requests per 5 minutes. The collector reads its rate-limit headers and waits for the window to reset, so a refresh can pause for up to about five minutes. A first install queues the regulation history (hundreds of events); the scheduler works through it in the background over several hours, then rechecks known events at most once a day.
- **Schedules while running:** discovery and recent-event checks every 6 hours, a daily reference check of up to 40 catalog source URLs (official Champions news and the pinned Showdown data), and weekly reconciliation. Reference checks record changes for review; they do not switch the active catalog.
- **When the source is down or refuses requests:** requests are retried with bounded backoff. Then the refresh reports `failed`, does not change the last complete snapshot, and keeps the queued work so a later refresh or scheduled run can resume it. Events that were validated before the failure are published later by the background worker's normal recovery cycle, never by the failed refresh.
- **Cost:** this path needs no AWS services and runs locally. You provide the hardware, disk, power and network access, and the collector uses Limitless's public API capacity; nothing here promises zero cost or upstream availability.

## Run from a source checkout

The Python API can run on the host when its configured DynamoDB and S3-compatible services are available. For an isolated local stack, use the [quick start](#quick-start) above.

### Requirements

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 or later and npm, only if you need to rebuild the web assets
- A configured DynamoDB table and S3 bucket for non-demo operation

Install the locked Python dependencies and start the server:

```bash
uv sync --frozen
uv run uvicorn app.main:app --reload
```

The server listens at <http://127.0.0.1:8000>. The health route is available at `/health`; API docs are at `/docs`. Data routes need access to the configured data store and a published public snapshot for `/v2` results. The default configuration does not create AWS resources or publish data.

If you change templates, styles, or browser assets, rebuild the generated assets first:

```bash
npm ci --ignore-scripts
npm run build
```

The Docker build runs this asset build in a Node stage and copies the generated, runtime assets into the API image. It does not need Node in the Python runtime image. Depending on your npm version, Tailwind's declared WASM archive may require npm's `--allow-remote=all` installation option; keep lifecycle scripts disabled for this install.

## HTTP API

FastAPI serves health, interactive OpenAPI documentation, the legacy `/v1` API, and the current `/v2` API.

| Route | Purpose |
| --- | --- |
| `GET /health` | Health and active regulation summary. `/` returns the same response. |
| `GET /v1/meta` | Legacy pre-aggregated usage and metric queries. |
| `GET /v1/teams` | Legacy tournament team query with cursor pagination. |
| `GET /v1/players/{player_id}` | Legacy player history. |
| `GET /v2/tournaments` | Published tournament summaries. |
| `GET /v2/tournaments/{event_id}` | One tournament summary. |
| `GET /v2/tournaments/{event_id}/standings` | Tournament standings and team records. |
| `GET /v2/tournaments/{event_id}/matches` | Published match records. |
| `GET /v2/teams` | Filtered team listing. |
| `GET /v2/teams/{team_id}` | One published team. |
| `GET /v2/teams/{team_id}/evidence` | Evidence associated with a team. |
| `GET /v2/players/{player_id}` | Published player team history. |
| `GET /v2/meta` | Usage and build attribute analytics. |
| `GET /v2/cores` | Observed Pokémon cores. |
| `GET /v2/regulations` | Regulation reference records. |
| `GET /v2/pokemon` and `/v2/pokemon/{identifier}` | Pokémon reference lookup. |
| `GET /v2/pokemon/{identifier}/learnset` | Catalog learnset reference. |
| `GET /v2/moves` and `/v2/moves/{identifier}` | Move reference lookup. |
| `GET /v2/status` | Public snapshot and service data status. |

Examples against a running local stack:

```bash
curl -fsS 'http://127.0.0.1:8080/v2/status'
curl -fsS 'http://127.0.0.1:8080/v2/tournaments?limit=25'
curl -fsS 'http://127.0.0.1:8080/v2/teams?members=species:incineroar&members=species:rillaboom&publication_status=complete'
curl -fsS 'http://127.0.0.1:8080/v2/meta?metric=move&move_id=protect'
curl -fsS 'http://127.0.0.1:8080/v2/cores?size=2&members=incineroar&members=rillaboom'
curl -fsS 'http://127.0.0.1:8080/v2/pokemon?name=Floette'
curl -fsS 'http://127.0.0.1:8080/v2/moves?name=Protect'
```

### Snapshot and cursor behavior

The `/v2` API reads from a complete published snapshot. A first page returns a signed snapshot token and may return a continuation cursor. Send both values to later pages or detail requests when provided, and keep the original filters and `limit` unchanged. Cursors bind to their endpoint, filters, catalog selection, and snapshot; they expire after 24 hours by default. Start a new query with a fresh snapshot after expiry. Error responses use a structured envelope with a code, public message, and request ID.

Public list requests return at most 100 rows. The configured query budgets are 20 storage queries and 2,000 candidates for listings; analytics permits 50 queries, 5,000 candidates, at most 90 inclusive UTC days, and a 350,000-byte response. Requests beyond a budget fail rather than returning partial analytics. See the [query guide](docs/runbooks/phase-5-queries.md) for selector rules, denominator semantics, errors, and end-to-end examples.

## Meta Explorer

The server-rendered Explorer is available under `/explorer`:

| Page | Route | What it shows |
| --- | --- | --- |
| Meta overview | `/explorer` | Usage entries, filters, and observed tournament data. |
| Common cores | `/explorer/cores` | Observed pairs and triples with links to matching teams. |
| Teams | `/explorer/teams` | Bounded tournament team search and results. |
| Pokémon | `/explorer/pokemon/{pokemon_id}` | Reference details, observed usage, known attributes, and provenance. |
| Team | `/explorer/teams/{team_id}` | Roster details and available published build evidence. |
| API examples | `/explorer/api` | Fixed API examples with response previews. |
| Evidence and methodology | `/explorer/about` | Sources, sample coverage, freshness, references, and artwork notes. |

The Explorer uses Jinja templates and HTMX enhancements while retaining normal GET navigation and filter forms. Its theme and motion preferences are browser-side settings. Sprite images are fetched directly from a pinned PokéAPI GitHub revision; the image binaries are not bundled with the application. Pokémon artwork remains copyrighted by The Pokémon Company. Public hosting does not grant a license to reuse that artwork. Interface font files are distributed under the SIL Open Font License.

## How the system works

### Data flow

```text
Public source captures ──> normalization and validation ──> immutable staged objects
                                                               │
                                                               v
                                                        native ingestion
                                                               │
                                                               v
                                                   complete public snapshot
                                                               │
                                           DynamoDB pointer + versioned S3 data
                                                               │
                                               /v2 API and Meta Explorer
```

The collector and ingestion pipeline preserve captured source evidence, normalize records into Champions-specific contracts, and write versioned data. Publication builds a complete generation before moving the public pointer. Readers use the complete generation, so interrupted work does not expose a partially published dataset. The legacy `/v1` routes remain available with their earlier storage contract.

### Main components

- `app/`: FastAPI application, response/query models, database readers, API routers, and web Explorer.
- `core/`: shared domain contracts, regulation/catalog snapshots, source admission, and common configuration.
- `db/`: DynamoDB client, single-table key construction, and catalog storage helpers.
- `ingestion/`: source normalization, validation, capture staging, ingestion, publication, and recovery.
- `scraper/`: bounded discovery and collection handlers. Live source admission is disabled in the bundled configuration.
- `scripts/local/`: Compose stack helper, local CLI, fixture demo, and local operations support.
- `infra/`: Terraform modules and dev/prod environments for AWS resources.
- `tests/`: unit and integration coverage using fixtures and mocked AWS services.
- `docs/`: runbooks and dated evidence records for local operations and query behavior.

## Configuration

The local Compose example is [local/compose.env.example](local/compose.env.example). Copy it to `.env.local` for Compose; its credentials and cursor key are dummy, local-only values and must not be reused for a deployed service. Direct host/API and Lambda operation use the AWS SDK credential chain and the application variables below.

| Variable | Purpose | Default / notes |
| --- | --- | --- |
| `AWS_DEFAULT_REGION` | AWS SDK region. | `us-east-1` |
| `DYNAMODB_TABLE_NAME` | DynamoDB single-table name. | `vgc-telemetry-dev`; local demo uses `vgc-telemetry-demo`. |
| `S3_RAW_BUCKET_NAME` | Bucket for source, catalog, and versioned data objects. | Required by relevant ingestion and runtime-catalog paths. |
| `DYNAMODB_ENDPOINT_URL` | DynamoDB-compatible endpoint override. | Unset for AWS; Compose sets its private service URL. |
| `S3_ENDPOINT_URL` | S3-compatible endpoint override. | Unset for AWS; Compose sets its private service URL. |
| `V2_CURSOR_SECRET` | Signs `/v2` snapshot and cursor tokens. | No generated default; configure a secret of at least 32 bytes. |
| `V2_DEFAULT_CATALOG_REVISION` | Default reference catalog revision. | `champions-mc-3661ce4` |
| `ACTIVE_REGULATION` | Regulation reported by health and used by legacy configuration. | `M-C` |
| `CATALOG_RUNTIME_ENABLED` | Enables loading catalog bundles from object storage. | `false` |
| `ENVIRONMENT` | Environment tag used by telemetry. | `dev` |
| `SOURCE_ADMISSION_PATH` | Source-admission JSON file. | `core/sources/admission.json` |
| `CATALOG_INDEX_PATH` | Catalog index file. | `core/regulations/v2/index.json` |
| `INGESTION_FUNCTION_NAME` | Ingestion Lambda target used by scraper dispatch. | Required for that AWS dispatch path. |
| `SCRAPER_FUNCTION_NAME` | Scraper Lambda target used for continuations. | Required for that AWS dispatch path. |
| `COLLECTION_PAGE_SIZE` / `COLLECTION_MAX_PAGES` | Per-run discovery bounds. | `50` / `10` |
| `COLLECTION_BATCH_SIZE` | Ingestion dispatch batch size. | `10` |
| `RECENT_DAYS` / `RECONCILE_DAYS` | Recent and reconciliation windows. | `7` / `90` |
| `COLLECTION_INTERVAL_HOURS` | Minimum hours before an already checked event is fetched again. | `6`; the live overlay uses `24`. |
| `LIVE_REFRESH_EVENTS` | Newest events captured by a foreground `live`/`refresh` run (1-100). | `10`; `--events` sets it per run. |
| `VGC_LOCAL_DEMO` / `VGC_LOCAL_CONTAINER` | Enforce local demo safety checks. | Set by the Compose example. |
| `LOCAL_SCHEDULES_ENABLED` | Opts into local scheduled work. | `false` in the Compose example; `true` only in `compose.live.yaml`. |
| `VGC_API_PORT` | Host port for the Compose API. | `8080`; read from `.env.local` by the helper, or from the shell for plain `docker compose`. |

Use AWS IAM roles or the SDK's standard credential providers for AWS access. Do not commit `.env.local`, cloud credentials, tunnel credentials, or Terraform state. Runtime collection remains disabled unless the source-admission and scheduling gates are explicitly configured.

## Tests and code quality

Install Python development dependencies with `uv sync`, then run the same checks used in CI:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

For the CI coverage command:

```bash
uv run pytest -v --cov=core --cov=db --cov=app --cov=ingestion --cov=scraper
```

Tests use pytest, Moto for mocked DynamoDB and S3, and local fixtures. They do not require live AWS resources and do not prove live source behavior, AWS throughput, deployed permissions, or operating cost. CI is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) and runs on pushes and pull requests to `main`.

For Terraform formatting:

```bash
terraform fmt -check -recursive infra/
```

## Docker images and AWS infrastructure

The root Dockerfile has three targets:

```bash
docker build --target api -t vgc-telemetry:api .
docker build --target ingestion -t vgc-telemetry:ingestion .
docker build --target scraper -t vgc-telemetry:scraper .
```

The API image runs Uvicorn on port 8080 and includes the generated Explorer assets. The ingestion and scraper targets are Python 3.12 Lambda images. Docker Compose is a separate self-hosted local stack and uses `Dockerfile.local`.

Terraform environments are in `infra/environments/dev` and `infra/environments/prod`, with reusable services in `infra/modules`. The Terraform pull-request workflow plans the dev environment when its AWS read-only role and ECR settings are configured. The deploy workflow runs only when started manually (Actions > Deploy > Run workflow, typing `deploy-production`) from `main`, and runs in the `production` GitHub Environment, which should be configured to require reviewer approval. It builds and pushes all three images to ECR and applies the prod Terraform environment when `AWS_DEPLOY_ROLE_ARN` is configured; it uses `ECR_REPOSITORY_NAME` if provided and otherwise `vgc-telemetry`. These are workflow capabilities, not confirmation that the secrets, cloud account, deployment, or production service exist. Review the generated Terraform plan and the source-admission state before any deployment or live collection.

## Repository layout

```text
app/                  FastAPI routes, models, readers, and Jinja Explorer
core/                 Domain contracts, regulation/catalog data, source policy
db/                   DynamoDB and object-store integration helpers
ingestion/            Validation, normalization, ingestion, publication, recovery
scraper/              Bounded source discovery and collection handlers
infra/                Terraform environments and AWS modules
local/                Compose environment example and optional tunnel config
scripts/              Local operations, catalog and benchmark tools
tests/                Unit, integration, and fixture-backed tests
docs/                 Runbooks and dated verification/evidence records
```

Useful references:

- [Self-hosted operations](docs/runbooks/self-hosted-phase-4a.md)
- [Query guide and examples](docs/runbooks/phase-5-queries.md)
- [Query validation record](docs/evidence/query-usability/verification.json)
- [Local stack proof](docs/local-operations/fresh-workspace-proof.json)
- [Catalog provenance](core/regulations/v2/README.md)

## Troubleshooting

### Docker is not running or permission is denied

`python3 scripts/local/stack.py ...` needs a working `docker compose`. Check with `docker compose version` and `docker info`. On Linux, start the daemon (`sudo systemctl start docker`) and either add your user to the `docker` group or use rootless Docker. On macOS and Windows, start Docker Desktop.

### Compose reports that `.env.local` is missing

The helper creates it automatically. When using `docker compose` directly, create it from the checked-in local-only example, then retry:

```bash
cp local/compose.env.example .env.local
docker compose up -d --build --wait
```

### The live refresh reports `failed`

- `reason: admission_invalid` or `admission_unverified`: `.local/admission.json` is malformed or was edited to an unverified entry. Delete it and rerun `python3 scripts/local/stack.py live` to recreate it from the bundled admission.
- `reason: collection_in_progress`: another collector holds the lease, for example after a scheduler was killed mid-run. Leases expire within 15 minutes; retry then.
- A `discovery_failed` or `capture_failed` diagnostic: the source could not be reached or refused requests. Check that the host can reach `https://play.limitlesstcg.com/api/games` (proxies and firewalls must allow outbound HTTPS from Docker containers), wait a few minutes in case of rate limiting, then run `refresh` again. Previously published data stays available.
- `.local/admission.json is a directory`: Docker created an empty directory because the file was missing when Compose started. Remove the empty directory and rerun `live`.

### Seeing what background collection is doing

Run `docker compose logs -f scheduler`. It shows each upstream request URL, rate-limit waits (`source_rate_limit_wait`), and redacted per-run outcomes. Upstream response bodies are never logged.

### The live refresh reports `no_eligible_events`

The source responded, but the newest events were not admitted Champions doubles events on Switch, or had custom rules. The summary's diagnostics count each reason. Earlier published data stays available, and the scheduler keeps checking for new events while the stack runs.

### `disabled`, or nothing is being collected

Live collection requires the live overlay and an enabled admission. Plain `docker compose up` and `python3 scripts/local/stack.py up` are intentionally offline. Use `python3 scripts/local/stack.py live`, and check that `.local/admission.json` contains `"enabled": true`.

### The API is healthy, but `/v2` has no usable data

Health confirms that the API process is responding; it does not mean a public snapshot has been published. On a fresh local stack, seed the retained fixture demo. The command refuses to write over existing local table rows or object versions.

### `seed-demo` refuses to run

The demo seed is intentionally limited to an empty installation. Do not delete volumes to bypass the check. Use the [local operations guide](docs/runbooks/self-hosted-phase-4a.md) to inspect, back up, or recover existing data.

### `/v2` reports cursor or snapshot errors

Keep the original endpoint filters and page size when continuing a query, and send the returned snapshot token with later pages. A cursor expires after its configured lifetime (24 hours by default); restart with a fresh first-page request after expiry. Ensure `V2_CURSOR_SECRET` is stable across requests and at least 32 bytes.

### Host API port is already in use

Add `VGC_API_PORT=8090` (any free port) to `.env.local`, rerun `python3 scripts/local/stack.py live` or `up`, and use that port in the URL. The helper passes `.env.local` to Compose. Plain `docker compose` reads the port only from your shell or a `.env` file, so use `VGC_API_PORT=8090 docker compose up -d --build --wait` there. The API stays bound to `127.0.0.1` by default and is not reachable from other machines.

### Changes to CSS or browser code do not appear

Run `npm ci --ignore-scripts` and `npm run build`, or rebuild the Compose image with `docker compose up -d --build`. The app serves generated assets from `app/web/static`; editing the source CSS alone does not update the built stylesheet.

## Contributing and licensing

Keep changes aligned with the Champions-specific data contracts and the evidence boundaries above. Use four-space Python indentation, type annotations, Ruff formatting, and tests for behavior changes. See the repository's [contribution and test commands](#tests-and-code-quality) and review the affected evidence or runbook when changing data behavior.

The project's original source code is released under the [MIT License](LICENSE). Pokémon names and artwork are owned by their respective rights holders; the Explorer's remote sprites are not relicensed by this project. Bundled font assets include their SIL Open Font License notices. Check the rights and terms of each upstream dataset, asset, and dependency before redistribution or deployment.
