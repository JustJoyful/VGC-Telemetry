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

M-A and intermediate historical game-data patches are unverified. Official notices do not provide all mechanical/learnset details. These community snapshots support the declared acceptance fixtures; automated update detection, candidate validation, historical backfill, and activation are Phase 3 work. Captured tournaments do not publish native Stat Points; unavailable private allocations stay unknown.
