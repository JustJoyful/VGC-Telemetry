# Phase 4A self-hosted operations

The stack uses DynamoDB Local, persistent RustFS 1.0.1, the actual FastAPI app,
and separate worker/scheduler processes. Everything below is local. Phase 4B
(AWS deployment, billing, IAM, triggers, live capacity/cost) remains deferred.
See the [fresh workspace proof](../local-operations/fresh-workspace-proof.json), [restore proof](../local-operations/restore-proof.json), and [local benchmark record](../benchmarks/local-benchmark-run-01.json).

## Historical Phase 4A evidence

Dated Phase 4A reports describe their recorded revisions. Current Phase 5
query behavior is described in the [query guide](phase-5-queries.md), with fixture results in the [query walkthrough](../evidence/query-usability/query-walkthrough.json).

## Fresh checkout

Requirements: Docker Engine with Compose, Linux or compatible Docker host,
Python 3.12+ and uv, and disk space for retained objects and all generations.
Image/dependency download needs network access once. Runtime fixture demo has
no upstream requests. No AWS credentials, AWS profile or tunnel credentials needed.

```bash
uv sync --frozen
cp local/compose.env.example .env.local
python3 scripts/local/stack.py up
python3 scripts/local/stack.py status
python3 scripts/local/stack.py seed-demo --output .local/evidence/demo.json
curl -fsS http://127.0.0.1:8080/health
curl -fsS 'http://127.0.0.1:8080/v1/meta'
curl -fsS 'http://127.0.0.1:8080/v2/tournaments?limit=100'
curl -fsS 'http://127.0.0.1:8080/v2/meta?metric=stat_points&limit=100'
curl -fsS 'http://127.0.0.1:8080/v2/cores?size=3&limit=100'
curl -fsS http://127.0.0.1:8080/v2/status
python3 scripts/local/stack.py down
```

`up` builds the runtime from uv.lock; bootstrap waits for reachable storage,
creates only `vgc-telemetry-demo`, enables bucket versioning and preserves
existing data. All services have health checks; workers also recover on startup.
API is bound to 127.0.0.1:8080. DynamoDB/S3 have no host ports. The scheduler,
DynamoDB and S3 run on an internal Docker network. API has a separate network
for its loopback port. Containers never mount the Docker socket.

Use the host helper for resets/demo: it stops worker/scheduler, runs the command,
and starts them again even on failure. Reset is explicit and restricted to the
exact demo table. It rebuilds disposable DynamoDB generations/checkpoints while
preserving every S3 object version, including captures and catalogs; back up the
table before reset if its published generations matter. It never deletes captures/catalogs in the
workspace, other tables/buckets, or Docker volumes. `down` preserves volumes.
Every evidence output requires a new path; choose another filename for reruns.

## Separate pipeline roles

These commands share production normalization/staging/native ingestion/public
snapshot code. Captured fixtures work even though source admission is disabled.
For operator mutations, stop background roles first. Generated refs are kept
inside the API container in /tmp; copy them out if long-term retention is needed.

```bash
docker compose stop worker scheduler
docker compose exec -T api python -m scripts.local.cli storage-contract
docker compose exec -T api python -m scripts.local.cli catalogs
docker compose exec -T api python -m scripts.local.cli collect --output /tmp/refs.json
docker compose exec -T api python -m scripts.local.cli ingest --refs /tmp/refs.json
docker compose exec -T api python -m scripts.local.cli recover
docker compose exec -T api python -m scripts.local.cli api-proof
# Synthetic correction: one retained cut entrant's public list becomes absent.
docker compose exec -T api python -m scripts.local.cli normalize --correction --output /tmp/corrected.json
docker compose exec -T api python -m scripts.local.cli ingest --refs /tmp/corrected.json
# Intentional nonzero exit AFTER a durable public-team write; prior pointer stays complete.
docker compose exec -T api python -m scripts.local.cli publish --synthetic-fault
docker compose restart api
docker compose exec -T api python -m scripts.local.cli recover
# Use generation IDs from demo output, always a retained complete target.
docker compose exec -T api python -m scripts.local.cli rollback --snapshot-id <baseline-id>
docker compose exec -T api python -m scripts.local.cli replay
docker compose start worker scheduler
```

`collect`/`normalize` use retained source captures and stage immutable originals
and normalized results with exact VersionIds. `ingest` registers native revision
work. `publish` builds and promotes complete public snapshots; `recover` drains
native and public work; `replay` rebuilds from retained current revisions. Existing
`python -m ingestion.public_replay` commands are also supported with these local
variables; local mode prevents falling back to AWS.

Legacy `/v1` data remains isolated with EV units unchanged; the offline demo does
not relabel native Stat Points as EVs. Empty legacy routes are a compatibility
smoke check. The `/v2` demo has 37 context rosters, 10 eligible cut rosters and 60
unknown allocations before correction; correction yields 9/54. Original source
fixtures are unchanged; the correction and interruption are explicitly synthetic.
Source fixture IDs/hashes, generations, deltas and HTTP summaries appear in output.
Snapshot UUIDs and publication timestamps vary; semantic results are repeatable.

## Persistence, backup and clean restore

Named Docker volumes are `<project>_dynamodb-data` (SQLite database) and
`<project>_s3-data` (RustFS data, metadata, versions). Inspect physical host
locations with `docker volume inspect vgc-phase4a_dynamodb-data vgc-phase4a_s3-data`.
Linux Docker normally stores these under /var/lib/docker/volumes; Docker Desktop
stores them inside its VM. These volumes are retained storage, not backups.

```bash
python3 scripts/local/stack.py backup --output .local/backups/retained-001
python3 scripts/local/stack.py down
# Separate allowlisted project, initially empty volumes; same API loopback port.
python3 scripts/local/stack.py --project vgc-phase4a-restore restore --input .local/backups/retained-001
docker compose -p vgc-phase4a-restore exec -T api python -m scripts.local.cli api-proof
docker compose -p vgc-phase4a-restore exec -T api python -m scripts.local.cli storage-contract
python3 scripts/local/stack.py --project vgc-phase4a-restore down
python3 scripts/local/stack.py up
# Explicit opt-in reset of the designated DynamoDB demo table; S3 versions survive:
python3 scripts/local/stack.py reset-demo --confirm-reset
```

Backup stops all services for a consistent physical copy, archives both volumes
with network disabled, records SHA-256 hashes, then restarts services. This causes
API downtime. Restore verifies all hashes/archive paths first and refuses any
nonempty destination volume. Keep backups on another disk/host as appropriate;
the helper does not provide redundant disks or off-host backup. Use the same
pinned storage images for restore, and verify versions, current complete pointer
and HTTP results before resuming work. Do not use `docker compose down -v`.

## Scheduler and source admission

One bounded process runs each role. Durable completion/due timestamps and
owner/expiry leases survive restart in DynamoDB. Worker recovery runs every 30
seconds in bounded calls; SIGTERM ends at the current call boundary. A killed
worker's lease expires; durable publication work remains recoverable. The API
continues serving the last complete snapshot independently.

Scheduler definitions: discovery/recent every 6 hours, reference daily,
reconciliation weekly. `LOCAL_SCHEDULES_ENABLED=false` and bundled admission
`enabled=false` prevent upstream collection. Offline collection uses fixtures.
Live source access is a separate owner admission/configuration gate; source
continuity, completion timestamps and reuse authorization remain unproven here.
To configure future admitted access, use an independently reviewed admission file,
explicit scheduler opt-in, and an explicit egress network override for the
scheduler. The default internal network prevents upstream connectivity.

## Optional private admin and public tunnel

```bash
docker compose --profile admin up -d admin
# http://127.0.0.1:8001; never open router/firewall ports for it.
docker compose --profile admin stop admin
```

Admin is disabled by default, bound only to loopback, and absent from tunnel
routes. S3 API/console, DynamoDB, workers and Docker control have no public route.
The disabled tunnel profile has a single hostname route to `http://api:8080`,
followed by HTTP 404. It requires owner-created `local/tunnel.yml` and ignored
`.local/tunnel-credentials.json`; both are excluded from Docker build contexts.
Do not enable it until owner hostname/credentials and the public API exposure
review are complete. Never paste credentials into logs or Compose source.

The API currently has a fixed 60/minute atomic DynamoDB limiter and fails open on
backend degradation. Proxy headers are disabled; behind a tunnel clients share
the proxy IP bucket, so per-user fairness is not proven. Error envelopes are safe
for `/v2`; legacy responses retain prototype behavior. Public use needs an owner
abuse/authentication review and can add edge rate limits/Access. Public availability
depends on host uptime, power, network, upload capacity, domain and tunnel service;
this is no availability or zero-cost guarantee. Tunnel execution is deferred.

## Verification and recording

```bash
uv run --frozen pytest -q tests/test_local_clients.py tests/test_local_storage.py tests/test_local_roles.py tests/test_local_benchmark.py
uv run --frozen pytest -q tests/test_local_demo.py
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen python -m scripts.benchmarks.run_local --confirm-reset --repeats 2 --output .local/evidence/benchmark-new-1.json
# Output must not exist; use another new filename for the second measurement.
```

The first command uses Moto and no Docker/cloud. Service-backed contract/demo,
restart and restore are distinct evidence. See `scripts/local/recording.md` for a
short recording sequence and `docs/benchmarks/local-benchmark-results.md` for measurement limits.

For a separate actual process-restart recovery proof after the demo:

```bash
uv run --frozen python -m scripts.local.recovery_proof --output .local/evidence/worker-restart.json
```

It invalidates public derived work, injects the named synthetic durable-write
interruption, checks the previous pointer stays complete, restarts the real worker,
and observes complete pointers until recovery finishes. No new source capture is
required. Future owner-authorized live scheduling has an override template at
`local/compose.sources.yaml.example`; copy/configure it only after source review.


## Empty-install seed and Phase 5 queries

Use `docker compose stop worker scheduler`, then
`docker compose exec -T api python -m scripts.local.cli seed-demo`, then
`docker compose start worker scheduler`. It never resets data. Existing table data
or object versions cause refusal. Empty recovery cursors and request counters do
not count as tournament data; a live worker lease must expire after interruption.
The original `demo --confirm-reset` flow remains the explicit destructive correction,
fault, rollback and replay proof. See [query examples](phase-5-queries.md).
Live source and tunnel credentials/authorization remain pending. The sources override
provides scheduler egress/configuration only; default admission remains off.
