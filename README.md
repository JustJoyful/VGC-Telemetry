# VGC Telemetry

**Pokémon Champions tournament data tooling, under active development.**

VGC Telemetry is being built around a Champions-specific API for tournament teams, regulations, Pokémon usage, moves, and team cores. The initial data source is public Limitless tournament data. The project favors verifiable source evidence, explicit unknowns, and reproducible results over broad but weakly supported coverage.

> **Status:** The current `main` branch contains the offline API, ingestion, publication, and automation work through Phase 3. Self-hosted operations and Phase 5 query improvements have been implemented and verified in the local `phase5-query-usability` branch, but those changes have not been merged into `main`. AWS deployment and live data collection are still unverified. This is development software, not a finished or production-ready service.

## Current work

The local Phase 5 branch adds bounded team-member searches, scoped move analytics, core-to-team exploration, reference lookups, and safer fixture seeding for the self-hosted stack. Its full test suite passed 458 tests, with one existing deprecation warning. Local persistence, backup/restore, and recovery checks also passed.

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
