# VGC Telemetry

A REST API project for **Pokémon Champions tournament analysis**: registered teams, published builds, Pokémon/form usage, moves, abilities, items, natures, Stat Points, Mega capability, and team cores.

## Project status

Phase 5 adds bounded multi-member team search, scoped move usage, core exploration,
exact reference lookup and a safe fixture seed for the self-hosted Compose install.
Historical Phase 4A is self-hosted and verified for its dated local checks. Fresh Phase 5
validation and remaining gates are recorded in [the evidence report](docs/phase5/evidence.md).
Source admission, live collection and the tunnel remain disabled by default. AWS
operations are the deferred Phase 4B alternate path. Phase 6 begins with a design
discussion; Phase 7 is optional. Existing `/v1` routes retain legacy EV units.

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

Visit `/docs` for the OpenAPI interface. Data endpoints require configured DynamoDB access; tests use mocked AWS resources. Native operation uses `AWS_DEFAULT_REGION`, `DYNAMODB_TABLE_NAME`, `S3_RAW_BUCKET_NAME`, `V2_CURSOR_SECRET` (at least 32 random bytes), and `V2_DEFAULT_CATALOG_REVISION` (bundled default: `champions-mc-3661ce4`). Do not commit credentials. `/v1` units are not migrated by `/v2`.

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

The original Phase 0–4A reports are historical; they do not establish acceptance for
later revisions. See the roadmap and current evidence report for executed gates.

## Self-hosted install and fixture demo

Docker and Docker Compose are required. Seeding runs in the application container.
The host needs no Python installation for this flow:

```bash
cp local/compose.env.example .env.local
docker compose up -d --build --wait
docker compose stop worker scheduler
docker compose exec -T api python -m scripts.local.cli seed-demo
docker compose start worker scheduler
curl -fsS http://127.0.0.1:8080/health
```

`seed-demo` accepts an empty installation only; existing tournament rows, S3 objects,
versions and delete markers cause refusal. Empty recovery cursors and request counters
are operational state. A busy worker lease causes refusal; wait for its expiry after
an interrupted worker. Seeding uses retained public captures, labels its report
`retained-fixtures`, and never enables live sources. An interrupted seed preserves its
writes and requires explicit recovery rather than an implicit overwrite.

Normal `docker compose down` retains both storage volumes. The separate destructive
`demo --confirm-reset` and `reset-demo --confirm-reset` commands rebuild only the local
demo table, retaining all S3 versions. See [local operations](docs/runbooks/self-hosted-phase-4a.md)
for backup/restore, and [Phase 5 queries](docs/runbooks/phase-5-queries.md) for executable
examples and denominator semantics. Default interfaces are loopback API only; admin
and Cloudflare profiles are opt-in. Public tunnel/source and AWS checks are pending.

Optional host helper: `python3 scripts/local/stack.py up`, `seed-demo`, `backup`, and
`down`. Phase 5 verification uses separate `vgc-phase5`/`vgc-phase5-restore` projects,
`VGC_LOCAL_IMAGE=vgc-phase5:local`, and `VGC_API_PORT=18085`/`18086`; existing installations
are preserved.

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

## Phase 3 automation operations

Offline verification on 2026-10-10: `uv run --frozen pytest -q` passed 394 tests
with one existing Starlette/httpx deprecation warning; Ruff lint, formatting,
and diff checks passed. Those historical tool-availability statements were superseded by Phase 4A local
checks; AWS infrastructure plans and deployments remain unverified.

Admitted Limitless discovery/recent checks are configured for every six hours,
reference checks daily, reconciliation weekly, and native recovery every 30
minutes. All EventBridge rules are disabled until `source_admission_verified`
and the independently reviewed `core/sources/admission.json` source admission
are enabled during Phase 4. Discovery stores event work before advancing its
page. A full work page enqueues a bounded continuation to the scraper Lambda;
failures replay from the last committed page. Unknown completion dates remain
eligible for recent checks; the upstream API does not currently provide a
verified completion timestamp to prove live 7/90-day windows.

For an operator-approved older replay, invoke the scraper Lambda with an
explicit bounded payload and increase `backfill_order` for a new replay:

```json
{"mode":"backfill","event_ids":["<Limitless event ID>"],"backfill_order":1}
```

Use at most 100 event IDs. For a retained, reviewed catalog bundle, run
`uv run --frozen python scripts/catalog_operator.py submit <bundle.json>`, then
`queue <candidate-id> --expected-epoch <epoch>` for scheduled activation.
`activate` and `rollback` require the exact expected epoch; rollback also
requires `--operator` and `--reason`. New upstream mechanics exports still
need operator resolution and official evidence review. Public build acquisition
remains disabled until a source passes access review.

CloudWatch custom metrics use `VGCTelemetry/Automation` with only environment,
source, and worker dimensions. The dev/prod Terraform module routes quarantine,
retry exhaustion, repeated worker error, unresolved catalog, tournament/reference
check age, and publication lag alarms to one SNS topic per environment. The
configured `notification_email` recipient must confirm the SNS subscription
after apply. See [the alarm runbook](SOURCE_UPDATE.md#phase-3-alarms). Freshness
and publication-lag alarms treat missing data as breaching, including before
the first run; they will show the disabled-worker state until live admission.

## Phase 2 public API and manual replay

`/v2` reads only a complete immutable public snapshot. Pin the returned signed snapshot token
for detail requests and send it with every later page; keep endpoint filters and
`limit` unchanged for the continuation cursor. Cursors are signed, query/snapshot
scoped, and expire at a fixed 24 hours. Listing defaults to 25 rows (maximum 100);
a request can inspect at most 20 queries/2,000 candidates, analytics at most 50
queries/5,000 contributions over an inclusive UTC window of at most 90 days.
Responses are capped at 350,000 bytes and may return an advancing cursor before the
requested page is full. Unknown values and private allocations remain unknown;
known-empty is distinct. Dates and source captures are separate, and undated events
are not assigned a capture date. Ordering is deterministic: dated events newest first
(unknown last), standings rank with unknown last, then stable identity.

```text
GET /v2/tournaments?limit=25
GET /v2/tournaments/limitless:6ab9204c880ed327106dddf4/standings?limit=25
GET /v2/teams?eligibility=all&tournament_id=limitless:6ab9204c880ed327106dddf4
GET /v2/teams/limitless:<team-id>
GET /v2/players/limitless:axtheb
GET /v2/regulations?limit=25
GET /v2/pokemon?catalog_revision=champions-mb-3661ce4&limit=25
GET /v2/meta?metric=species&date_from=2026-09-01&date_to=2026-10-01&placement_max=8
GET /v2/meta?metric=nature&population_id=<exact-population-id>
GET /v2/cores?size=3&date_from=2026-09-01&date_to=2026-10-01
```

Use each response's `snapshot` token on following pages and details. Team detail may
show a partial or absent roster. Native Stat Points are six integers (0–32, total
at most 66) only when observed; supported metrics are species/form, moves, abilities,
items, natures, native Stat Points, and registered Mega. Legacy EV data is never converted. A registered
Mega is capability evidence, not battle activation. Core sizes are 2 or 3. Analytics
require an unambiguous population; select an exact returned population when multiple
divisions/catalogs/rulesets match. Match rates use linked verified cut matches; ties
count half a win and half a loss, mirrors remove both sides, and byes/double
losses/automatic or pending results are excluded. Zero eligible matches yield null.

Manual replay requires configured AWS access and operator-reviewed event IDs. It
writes durable checkpoints and does not delete retained inputs or snapshots:

```bash
uv run --frozen python -m ingestion.public_replay inventory --events-file /tmp/vgc-phase2-event-ids.json
uv run --frozen python -m ingestion.public_replay seed --events-file /tmp/vgc-phase2-event-ids.json --max-steps 100
uv run --frozen python -m ingestion.public_replay build --max-steps 100
uv run --frozen python -m ingestion.public_replay recover --max-steps 100
uv run --frozen python -m ingestion.public_replay invalidate --policy-version limitless-single-bracket-v1
uv run --frozen python -m ingestion.public_replay rollback --snapshot-id <retained-complete-id> --expected-snapshot-id <current-id>
```

`pending` means repeat/resume with `recover` or `build`, not publication success.
Inventory is read-only. Invalidation and rollback are explicit operations; Phase 3
defines a disabled recovery schedule. Rollback changes the current reader pointer
while preserving newer input membership and dirty work. Historical M-A/patch
coverage and build-source admission remain open. Phase 4 owns same-revision deployment, IAM/client-IP
and runtime smoke checks, AWS capacity/cost/latency measurements, and restore proof.

Phase 2 offline verification: `uv run --frozen pytest -q` passed 320 tests with one
existing Starlette/httpx deprecation warning. Coverage and final command evidence are
recorded in the plan ledger. Moto timings and derived-row sizes are not live AWS
capacity or cost evidence; see SOURCE_UPDATE.md and MEMORY.md for source hashes and
unchecked live gates.
