# Champions catalog provenance

Phase 0 keeps these snapshots separate from the legacy `core/regulations/m_c.json` subset. `index.json` verifies immutable snapshot IDs and SHA-256 content before loading. Event admission selects a named revision; the current date never selects one implicitly.

| Snapshot | Regulation interval (UTC, end exclusive) | Species/forms | Items | Official eligible entries |
|---|---|---:|---:|---:|
| `champions-mc-3661ce4` | 2026-09-09 02:00 → 2026-12-02 02:00 | 390 | 166 | 262 |
| `champions-mb-3661ce4` | 2026-06-17 02:00 → 2026-09-09 02:00 | 355 | 148 | 235 |

Each resolves 515 move records, 316 ability references, and 25 natures. Species records restrict compatible abilities and learnsets; global reference inventories alone do not establish a legal build. Species counts include cosmetic and battle-only forms; battle-only forms cannot be registered directly. Compatible held stones derive Mega capability without claiming activation. A legal incompatible held stone stays observed and confers no Mega capability.

Official [M-C](https://champions-news.pokemon-home.com/en/page/816.html) and [M-B](https://champions-news.pokemon-home.com/en/page/776.html) notices establish rules intervals and link complete eligible lists. Every official listed entry resolves. Mechanics, base stats, abilities, items, and inherited learnsets come from community [Pokémon Showdown](https://github.com/smogon/pokemon-showdown/tree/3661ce40bf9001d185ce078b8920e12304204609), revision `3661ce40bf9001d185ce078b8920e12304204609`. `source_kind=community` identifies that supplementary authority; this revision is not an official game patch number. The retained license is [UPSTREAM_LICENSE.txt](UPSTREAM_LICENSE.txt).

## Reproduce

Use a separate reference checkout at the exact revision above, install its locked dependencies with `npm ci --ignore-scripts`, then run `node build` there. The reference build requires its supported Node version; Node is unnecessary for the Python service.

From this repository root, substitute the reference checkout path:

```bash
node scripts/export_champions_catalog.cjs /path/to/pinned-showdown M-C > /tmp/catalog-mc.json
node scripts/export_champions_catalog.cjs /path/to/pinned-showdown M-B > /tmp/catalog-mb.json
uv run python -m scripts.build_phase0_fixtures --resolved-dir /tmp
uv run pytest tests/test_catalog.py tests/test_source_fixtures.py -q
```

The exporter uses each named VGC format's resolved Dex and validator, including inherited data. The builder checks recorded hashes, verifies official-list coverage, writes snapshots/index, and rebuilds default-disabled admission configuration and real capture bundles. Review source evidence before replacing an existing revision. Public capture metadata/bodies and failed access responses are retained in `tests/fixtures/sources/captures/`; the manifest marks evidence used for admission.

## Coverage limits

M-A and intermediate historical game-data patches are unverified. Official notices do not provide all mechanical/learnset details. These community snapshots support the declared acceptance fixtures. Captured tournaments do not publish native Stat Points; unavailable private allocations stay unknown.

## Phase 3 catalog operations

The disabled 24-hour `reference` schedule checks official news, retained M-B/M-C
notices and pools, pinned Showdown evidence, and its public branch revision. It
retains successful and failed HTTP evidence under `staging/v2/catalog/`, outside
the legacy raw trigger. Changed sources stay pending until a resolved export and
official evidence are reviewed. The Python Lambda does not run Node or infer
legal pools from source diffs.

Bootstrap must match a hash-verified bundled resolved export. Later candidates
compare all five collections to the retained verified baseline. Changed IDs or
mechanics require a reviewer, exact additions/removals, and quotations resolving
to retained official evidence. Unknown intervals, incomplete official pools,
unsupported evidence, and failed validation cannot activate.

Supply a JSON bundle containing `snapshot` (the existing CatalogSnapshot shape),
`documents` (URL to CapturedDocument), and optional `review` (CatalogChangeReview).
Use the existing CJS exporter above to resolve supported upstream data first.
Candidate IDs and snapshot IDs are content-derived; submissions preserve prior
catalogs and their evidence.

```bash
uv run --frozen python -m scripts.catalog_operator submit /tmp/catalog-bundle.json
# Use the returned candidate ID and current ACTIVE epoch (0 before bootstrap).
uv run --frozen python -m scripts.catalog_operator queue CANDIDATE_ID --expected-epoch 0
uv run --frozen python -m scripts.catalog_operator activate CANDIDATE_ID --expected-epoch 0
uv run --frozen python -m scripts.catalog_operator rollback RETAINED_ID --expected-epoch 1 --operator OPERATOR --reason REASON
```

Queueing selects one verified candidate for the reference worker. The worker
rechecks supporting evidence, waits for the official interval, and uses epoch
CAS to activate it. `activate` performs the guarded operation immediately;
`rollback` retains history and records operator/reason. These commands write AWS
state when configured; Phase 3 verification uses mocked resources only. Event
catalog pins are unchanged by activation or rollback. API runtime reads require
`catalog_runtime_enabled = true` after activation; its default is false.

Public JSON build acquisition has no admitted source (`core/sources/builds.json`
is empty). Explicit evidence can enter ingestion with `mode: "build"`, a `build`
object containing `document`, reviewed `source` access evidence, and `captured_at`,
plus 1–10 exact `normalized` references. The `published-build-v1` JSON body declares
author, build ID, source event/player or entrant IDs, Pokemon fields, and optional
`supersedes`. A reviewed `cross_reference` may provide an exact correspondence.
Links require the current native event revision and identical roster identity.
Build revisions and conflicts remain under `V2#BUILD`; no observations or
population counts are overwritten. Use `BuildStore.links` with 1–100 rows and its
returned cursor to inspect retained per-team outcomes.
