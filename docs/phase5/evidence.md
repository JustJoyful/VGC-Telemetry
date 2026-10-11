# Phase 5 evidence — 2026-10-11

Acceptance status: **Phase 5 complete**, all current behavioral, static, local persistence/recovery and review gates verified.

## Revision and isolation

Base: `b85a6c6` (`self-hosted` and `origin/self-hosted`). Implementation branch:
`phase5-query-usability`, worktree `/tmp/vgc-phase5`. User changes in the main
checkout are preserved. Only `vgc-phase5` and `vgc-phase5-restore` resources were
started/stopped/reset/restored for this acceptance pass. Their loopback ports are
18085 and 18086, image `vgc-phase5:local`; no cloud, live collection or public tunnel.

Verified image: `sha256:bb42b2fd43e2961525cbb774253542e23b43f2cbddb36636e157b519f61fe27a`.
Ordered Python runtime source digest (app/core/db/ingestion/scraper/scripts/local):
`53a811e64261a2651d06f4ccf05e06140a8af0f03153df4579a24665121cc234`.
The digest is identical inside the exercised image and final worktree. Subsequent
changes affect only the test harness and documentation.

## Automated checks

- Final API: `.venv/bin/pytest -q tests/test_v2_reference.py tests/test_v2_teams.py tests/test_v2_cursors.py tests/test_v2_analytics.py` — **51 passed**, 193.67s.
- Local safety and v1 compatibility: `.venv/bin/pytest -q tests/test_local_clients.py tests/test_local_stack.py tests/test_local_storage.py tests/test_local_roles.py tests/test_api_endpoints.py tests/test_api_core.py` — **51 passed**, 4.11s.
- Native pipeline: `.venv/bin/pytest -q tests/test_v2_pipeline.py` — **4 passed**, 609.10s.
- Mock rollback harness: `.venv/bin/pytest -q tests/test_moto_transaction_retention.py` — **2 passed**, 0.30s. New copy-once regression failed first; failed transactions still roll back, dashboard copies remain released.
- `.venv/bin/ruff check .` — all checks passed.
- `.venv/bin/ruff format --check .` — 178 files already formatted.
- `git diff --check` — passed.
- Full baseline application suite (same optimized fixture): `.venv/bin/pytest -q` — **431 passed, 1 pre-existing README assertion failed**, 2821.73s. The updated README passes that assertion in the final branch.
- Full final suite: `.venv/bin/pytest -q` — **458 passed**, one existing deprecation warning, 2866.24s. Normal exit 0; source revision `b0adf71`.

Tests use the installed locked environment through the worktree's `.venv` symlink.
FastAPI TestClient needs the approved outside-sandbox execution path in this host.
The focused API/local checks emit one existing Starlette/httpx deprecation warning.

The first frozen baseline run reached 414 passes and one pre-existing README
assertion failure before interruption. Stack diagnostics identified Moto repeatedly
copying the same whole table for every action before transaction writes. The existing
fixture now reuses that untouched backup once per transaction while calling Moto's
real transaction/condition/rollback implementation. Both baseline application and
final suite rerun with this tested harness. Original slow logs remain in `/tmp`. A timed faulthandler diagnostic crashed Python
while printing a stack (exit 139); plain pytest reruns without that diagnostic.
Aborted runs are not counted as passing evidence.

## Local operations

Commands run from the worktree, with the project/image/port explicitly isolated:

```bash
VGC_LOCAL_IMAGE=vgc-phase5:local VGC_API_PORT=18085 docker compose -p vgc-phase5 up -d --build --wait --wait-timeout 180
VGC_LOCAL_IMAGE=vgc-phase5:local VGC_API_PORT=18085 python3 scripts/local/stack.py --project vgc-phase5 seed-demo --output /tmp/phase5-seed-proof.json
VGC_LOCAL_IMAGE=vgc-phase5:local VGC_API_PORT=18085 docker compose -p vgc-phase5 down
VGC_LOCAL_IMAGE=vgc-phase5:local VGC_API_PORT=18085 docker compose -p vgc-phase5 up -d --wait --wait-timeout 180
VGC_LOCAL_IMAGE=vgc-phase5:local VGC_API_PORT=18085 python3 scripts/local/stack.py --project vgc-phase5 backup --output /tmp/phase5-backup
VGC_LOCAL_IMAGE=vgc-phase5:local VGC_API_PORT=18086 python3 scripts/local/stack.py --project vgc-phase5-restore restore --input /tmp/phase5-backup
VGC_LOCAL_IMAGE=vgc-phase5:local VGC_API_PORT=18085 python3 scripts/local/stack.py --project vgc-phase5 demo --confirm-reset --output /tmp/phase5-final-demo-proof.json
```

- Fresh startup: API, DynamoDB, storage, worker and scheduler healthy; bootstrap exits successfully. Optional admin/tunnel services absent. [Service report](services.json). Verification projects are subsequently stopped with `down`; named volumes remain.
- Empty-install seed: retained fixtures, 37 standings rosters, 10 eligible complete rosters and 60 unknown Stat Point allocations. Thirteen HTTP checks pass. Initial attempt refused an unexpired worker lease; waited for expiry. [Seed report](seed.json).
- Second seed refuses existing data without changing the snapshot; unit regressions also preserve existing rows, object versions and delete markers.
- Down/up retains named volumes (no `-v`). Consistent physical backup stops test services during archive creation. Restore checks archive hashes before writes and uses empty target volumes. [Backup manifest](backup-manifest.json).
- Restore preserves the exact snapshot ID, coverage and all eight key/version pairs: [before](persistence-before.json), [restored](persistence-restored.json).
- Final image executes the documented query walkthrough on both original baseline and restored storage: seven matching teams, core 7/10, scoped Protect 3/3, known zero 0/3, reference choices and safe errors. [Queries](query-walkthrough.json), [restored queries](restored-queries.json).
- Fresh final-runtime demo: duplicate unchanged; synthetic publication fault preserves CURRENT; correction changes eligible rosters 10→9 and unknown allocations 60→54; rollback returns 10/60; replay returns 9/54. Five stages × thirteen HTTP checks all return 200 and check provenance. [Recovery report](recovery.json).
- The destructive synthetic demo is explicitly limited to the designated test table; all original eight S3 versions survive, with eleven total afterwards. [Retention](retention-after.json).
- Default/source Compose configuration is checked with admission disabled. Offline pinned cloudflared ingress validation passes with networking disabled and routes only to FastAPI. Live tunnel/collection was not enabled.

## Independent review and fix pass

One fresh independent final reviewer found three Important issues, no Critical or
Minor findings. All were reproduced by failing tests and fixed:

1. Historical population drilldown uses its retained catalog, consumes one listing query, and pins the resolved revision for continuation.
2. Unknown Mega capability of another base species cannot inflate the requested scope's uncertainty.
3. Form-grouped base cores return exact `form:` team selectors; species grouping returns `species:` selectors.

The same fix pass also tests snapshot identity/expiry independently of internal
generation metadata, and includes lookup ambiguity metadata in response-byte bounds.
No second review was dispatched.

## Rulings and costs

1. Numbered plan headings require manual task tracking because the extractor expects `Task N`; cost: manual ledger maintenance.
2. Baseline runs against a separate source copy while implementation progresses; cost: baseline result arrives later.
3. Independent review covers the uncommitted source diff against the base; cost: evidence-only docs are finalized afterwards.
4. AWS deployment/IAM/capacity/billing/Terraform stays deferred per plan; cost: AWS readiness remains unproven.
5. Live tunnel/upstream checks stay pending per plan; cost: live operation remains unproven.
6. Phase 6/7 stay separate and unimplemented; cost: no frontend/extensions delivered.
7. Pending full-suite and persistence evidence is an acceptance gate; cost if absent: no Phase 5 completion claim.
8. Equivalent Moto backup reuse stays inside the test fixture; cost if wrong: weaker mock rollback evidence. Explicit failed-transaction tests pass and production transactions are unchanged.

## Remaining limits

Observed tournament data and known complete build denominators support these answers;
registered Mega means roster capability, never battle activation. AWS/account/cost/
permissions and live tunnel/source prerequisites are unresolved. Phase 6 begins with
design; Phase 7 retains optional scope. No merge, push, PR publication or deployment.
