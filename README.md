# VGC Telemetry

**Pokémon Champions tournament data tooling, under active development.**

VGC Telemetry is being built around a Champions-specific API for tournament teams, regulations, Pokémon usage, moves, and team cores. The initial data source is public Limitless tournament data. The project favors verifiable source evidence, explicit unknowns, and reproducible results over broad but weakly supported coverage.

Phase 5 adds bounded multi-member team search, scoped move usage, core exploration,
exact reference lookup and a safe fixture seed for the self-hosted Compose install.
Historical Phase 4A is self-hosted and verified for its dated local checks. Fresh Phase 5
validation and remaining gates are recorded in [the evidence report](docs/phase5/evidence.md).
Source admission, live collection and the tunnel remain disabled by default. AWS
operations are the deferred Phase 4B alternate path. Phase 6 adds the Meta Explorer
at `/explorer`; its design, application, packaging and verification acceptance gates passed, recorded in
[Phase 6 evidence](docs/phase6/evidence.md). Phase 7 is optional. Existing `/v1` routes retain legacy EV units.
> **Status:** The current `main` branch contains the offline API, ingestion, publication, and automation work through Phase 3. Self-hosted operations and Phase 5 query improvements have been implemented and verified in the local `phase5-query-usability` branch, but those changes have not been merged into `main`. AWS deployment and live data collection are still unverified. This is development software, not a finished or production-ready service.

## Current work

The local Phase 5 branch adds bounded team-member searches, scoped move analytics, core-to-team exploration, reference lookups, and safer fixture seeding for the self-hosted stack. Its full test suite passed 458 tests, with one existing deprecation warning. Local persistence, backup/restore, and recovery checks also passed.

## Meta Explorer

Open `/explorer` on the existing FastAPI service. The Retro-Modern Competitive
Pokédex uses readable IBM Plex typography, restrained pixel sprite windows and
margin geometry, with system light/dark preference and a saved theme choice.
Pause motion or use reduced-motion preferences; with JavaScript disabled the
interface stays static and native navigation/filter GET forms remain usable.

- `/explorer`: a bounded three-entry exact-form overview and observed cores.
  Continue with the returned cursor; the index uses canonical API order and makes
  no global ranking or strength claim.
- `/explorer/teams`: repeated `members` selectors for up to three species/forms/
  registered Mega capabilities; each result retains its catalog and population.
- `/explorer/pokemon/incineroar`: reference facts alongside observed usage, known
  slots, unknown fields, partners and provenance.
- `/explorer/teams/{team_id}`: the observed roster and published build evidence;
  absent/partial rosters never acquire invented members.
- `/explorer/api`: six fixed, bounded GET examples with escaped response previews.
  `/docs` remains the complete OpenAPI interface.
- `/explorer/about`: source scope, cut evidence, denominators, freshness, catalog
  authority, Stat Points and artwork terms. Public methodology copies are local
  assets and must be refreshed when their source documents change.

`Snapshot` means published data, `Stale` reflects the API's capture-age flag, and
`Unavailable` means usable current data is missing. Selected signed snapshots and
cursors are separate from current storage status. Collection remains disabled;
missing freshness, counts and build fields stay Unknown. Known zero requires a
positive known denominator. Registered Mega capability does not prove activation.
Filters and drilldowns preserve exact forms, population/catalog selection, UTC
bounds and snapshot identity. Analytics accepts at most 90 inclusive UTC days;
existing native query/row/response limits remain authoritative.

Run the local stack with the existing instructions below; then open
`http://127.0.0.1:8080/explorer` (or your chosen `VGC_API_PORT`). Empty installations
show an unavailable state until a public snapshot is published. The opt-in
`seed-demo` command uses retained offline captures and refuses existing storage.
Neither opening the UI nor seeding fixtures enables collection or a tunnel.

For a source checkout, use Node 22+ to build the pinned frontend dependencies:

```bash
npm ci --ignore-scripts
npm run build
uv sync --frozen
uv run uvicorn app.main:app --reload
```

Both Dockerfiles build CSS/HTMX/JavaScript in a separate Node stage. Their Python
runtime images contain templates, generated assets, fonts and licenses; Node,
node_modules and frontend build sources are excluded. No JavaScript or font CDN
is required. npm 12 may require `--allow-remote=all` for Tailwind's declared WASM
archive during installation; keep `--ignore-scripts`.

Entity sprites use exact verified paths at PokéAPI/sprites revision
`2ecb4eeacd5a1718621fc30f12772e3f60d830b9`, fetched directly from GitHub by the browser.
Unresolved or failed images retain names and neutral placeholders. Sprite binaries
are not packaged. Image copyrights are reserved to The Pokémon Company; CC0 and
remote hosting do not establish image permission. GitHub availability and rights
clearance remain risks. Interface fonts are bundled under the SIL Open Font License.

## Domain
The Phase 5 implementation and its evidence are in the `phase5-query-usability` branch and worktree. See the [query usability acceptance record](docs/evidence/query-usability/evidence.md) for revisions, exact commands, results, and remaining limits. The [delivery roadmap](PHASES.md) tracks the larger project sequence. Phase 6 frontend work has not started; it begins with a design discussion.

## Project boundaries

- Pokémon Champions is the target game. Generic VGC data does not establish Champions format or legality.
- Native builds use **Stat Points**. Existing `/v1` EV values retain their meaning; no conversion is performed.
- Partial public teams are useful evidence. Missing data stays unknown. A registered Mega indicates roster capability, not battle activation.
- Tournament analytics require evidence for event structure and cut membership. A standings rank threshold alone does not establish an official cut.
- Limitless is the only admitted tournament source. Automated RK9 screen scraping is excluded under its published Terms.
- Collection schedules and the optional public tunnel remain disabled by default.

## Development setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

The full test suite is slow; the recorded Phase 5 run took about 48 minutes. Tests use fixtures and mocked AWS services. They do not establish live source behavior, AWS capacity, deployment, or operating cost.

To run the API, configure the required AWS and application settings for your environment, then start:

```bash
uv run uvicorn app.main:app --reload
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
The interactive OpenAPI page is at `/docs`. Data routes require access to the configured data store. Keep credentials and environment files out of Git.

## API shape

The `/v2` API reads complete published snapshots and supports bounded, cursor-based queries. Preserve the returned snapshot and cursor when continuing a listing.

```text
GET /v2/tournaments
GET /v2/teams?members=species:incineroar&members=species:rillaboom
GET /v2/meta?metric=move&move_id=protect
GET /v2/cores?size=2&members=incineroar&members=rillaboom
GET /v2/pokemon?name=Floette
GET /v2/moves?name=Protect
```

The query examples above describe the Phase 5 implementation branch until it is integrated. See the [Phase 5 query guide](docs/runbooks/phase-5-queries.md) for pagination, filters, coverage, and ambiguity behavior.

## Local self-hosted stack

The Phase 4A/5 Compose stack is available in the `phase5-query-usability` branch. It runs the API, DynamoDB Local, versioned object storage, and bounded worker/scheduler processes. The API binds to loopback; source admission and tunnel access are off by default. The fixture demo requires no AWS account or credentials.

Follow the [self-hosted operations guide](docs/runbooks/self-hosted-phase-4a.md) for setup, worker operations, replay, backup, and restore. Do not use that runbook with `main` until the branch containing the Compose files is checked out.

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
## What remains open

- Merge and integrate the self-hosted and Phase 5 work.
- Verify AWS permissions, deployment, capacity, cost, billing, and operational recovery.
- Establish live source continuity and separately review any activation of collection or a tunnel.
- Design the Meta Explorer before starting frontend implementation.

## Project references

- [Delivery phases](PHASES.md)
- [Product scope](PRD.md)
- [Architecture](ARCHITECTURE.md)
- [Domain and reliability rules](RULES.md)
- [Source and release checklist](SOURCE_UPDATE.md)
- [Catalog provenance](core/regulations/v2/README.md)

This README is a working project note. Update it as development moves; a polished project introduction can wait until the project is closer to done.
