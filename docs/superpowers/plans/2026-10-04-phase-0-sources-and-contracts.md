# Phase 0 — Sources and Champions Contracts Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. This plan records pending work; no implementation is authorized by a checked example or assumed source response. Git commits are user-only. Preserve existing workspace edits.

**Goal:** Prove that real, positively identified Champions events from Limitless and RK9 can produce accurate native/partial tournament records and an isolated stored-payload preview.

**Architecture:** Retain the existing Python packages and three container targets. Add versioned Champions contracts beside legacy contracts, source-specific capture/normalization, and an explicit catalog/admission boundary. Stage originals and normalized results in non-trigger S3 prefixes; Phase 1 owns publication and Phase 2 owns public `/v2` routes.

**Tech stack:** Python 3.12+, Pydantic v2, httpx, boto3/S3, pytest, Moto, Ruff, uv. Use the standard library HTML parser initially; add a parser dependency only if captured markup demonstrates a need, updating dependency/lock files together.

**Spec:** [PRD](../../../PRD.md), [Phase 0 roadmap](../../../PHASES.md), [architecture](../../../ARCHITECTURE.md), [rules](../../../RULES.md). These local context documents may be absent from a clean clone; obtain them before execution.

## Goal alignment and chosen boundary

The mandatory product includes tournament discovery, teams/builds, canonical cores, species/form/Mega usage, moves, abilities, items, natures, native allocations, verified results, and automatic tournament/reference updates. The source APIs are inputs; this API adds a common model, provenance, legality, coverage, corrections, and reproducible analysis.

Three approaches were considered:

1. **Recommended: versioned contracts and isolated previews.** Establish source truth now while preserving legacy behavior. Introduces a temporary staging path, but gives Phase 1 reliable inputs.
2. Retrofit native fields into the current writer. Less initial code, but silently changes legacy EV meanings and carries its concurrency/correction bugs into new data.
3. Build the entire `/v2` API before source verification. Produces visible routes sooner, but risks designing responses around unavailable or misidentified data.

Phase 0 ends at a repeatable read-back preview. It does not compute new regulation aggregates, serve `/v2` HTTP routes, enable a production feed, or claim an automated updater. Those remain mandatory later phases, not removed scope.

## Global constraints

- Champions exclusively; generic `VGC`, event names, and dates are insufficient identification.
- Initial analytics are doubles. Preserve division, competition, custom rules, and game/rules revisions.
- Current native allocations are six strict integers, each 0–32, total at most 66. No implicit numeric-size detection or traditional-EV conversion.
- Unknown fields remain unknown. A missing allocation is not six zeroes; missing item and confirmed empty item are distinct.
- Registered base/form, compatible stone, and Mega capability are separate. No claim of observed activation without battle evidence.
- Fixed IV compatibility metadata is derived, not an observed distribution. No current Terastallization field.
- Catalogs declare full coverage, provenance, effective dates, verification status, and immutable upstream revisions. A supplementary Showdown snapshot is labeled community evidence.
- Keep `/v1` build units, response behavior, and legacy storage keys unchanged. Preserve user edits in existing tests rather than replacing their files wholesale.
- No new native data enters the existing additive writer. `staging/v2/captures/` and `staging/v2/normalized/` remain outside the current `raw/` notification.
- New collectors are off by default. Positive admission evidence is required in addition to an enable flag.
- Git commits are user-only; do not stage ignored documents or perform deployment during this phase.

## Review focus

| Condition | Expected behavior | Owning task |
|---|---|---|
| Public list uses familiar VGC IDs but an unverified game/platform | Pending/rejected admission; no guessed Champions regulation | 3, 4 |
| Event has entrants but no public team lists or closed private builds | Preserve entrants and explicit unknowns; no fabricated builds | 2, 5, 6 |
| Swiss and bracket rounds reuse numbers; opponent/winner is absent or a sentinel | Unique deterministic match IDs and explicit outcome uncertainty | 4 |
| Display names repeat or standings change between captures | Source identity remains stable; no name/standing-based player merging | 4, 5 |
| One team is malformed, another source fails, or native input reaches legacy ingestion | Preserve valid evidence, fail operational work visibly, prevent legacy writes | 6, 7, 8 |

## Source and execution gates

All tasks below are unchecked. Task 1 precedes source-specific implementation. Native contracts and fixture tooling can progress while a source is unavailable, but cannot substitute for source admission.

For each source, record: public endpoints/page links, retrieval time, content hashes, visibility/access result, format/platform evidence, rules/catalog linkage, and actual public-field coverage. Record absent or unsupported Champions coverage as an evidence gap; keep that adapter disabled. A documented API example is useful for structural tests but is not a real Champions acceptance fixture.

The current `m_c.json` is not authoritative. Do not copy its IDs, dates, stones, or forms into a new verified catalog without independent evidence. Require a complete catalog for each admitted acceptance event. Include the current target and a verified prior rules revision when available; explicitly record uncovered history for Phase 3.

Execute tasks in this order: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9**. Record failures and evidence in MEMORY.md. If a required live fixture/catalog cannot be acquired, report that gate as incomplete and continue only work independent of it.

## File map

| Files | Responsibility |
|---|---|
| `scripts/capture_sources.py`; `tests/fixtures/sources/manifest.json` and source subdirectories | Reproducible public captures and admission evidence |
| `core/models/champions.py` | Version-2 tournament, build, capture, and diagnostic contracts |
| `core/models/catalog.py`; `core/catalog.py`; `core/source_admission.py` | Explicit snapshot loading, source identification, legality checks |
| `core/regulations/v2/index.json`; snapshot JSON files; `core/sources/admission.json` | Sourced catalog inventory and default-disabled admission configuration |
| `scraper/limitless_client.py`; `scraper/limitless_normalizer.py` | Correct Limitless requests and observed-shape normalization |
| `scraper/rk9_scraper.py`; `scraper/rk9_normalizer.py` | Public RK9 event/team extraction and identity preservation |
| `ingestion/normalize.py`; `ingestion/staging.py`; `ingestion/preview.py` | Common validation, isolated storage, offline read-back preview |
| `scraper/dispatcher.py`; `scraper/handler.py` | Explicit source dispatch and visible collection outcomes |
| New focused test files listed below | Behavioral regression and offline acceptance coverage |

Legacy `core/models/contracts.py` retains its definitions and bounds; only correct its misleading Champions EV docstring. `ingestion/parser.py`, `ingestion/writer.py`, and `/v1` routers are not converted to native inputs in this phase.

## Task 1 — Capture public source evidence

**Files:** Create `scripts/capture_sources.py`, `tests/test_source_fixtures.py`, and `tests/fixtures/sources/manifest.json`; add captured files under `tests/fixtures/sources/limitless/` and `tests/fixtures/sources/rk9/`.

**Interfaces:** `capture_public_url(url: str, *, output_dir: Path, client: httpx.Client) -> Path` records response body, URL, capture time, status/content type, and hashes. Manifest entries map named documents to local paths and distinguish `live_public`, `documentation`, and `synthetic` origins.

- [ ] Write `test_fixture_manifest_hashes_and_origins`: retained file hashes match, every capture has URL/time/origin, and only `live_public` evidence can support admission. Missing fixtures must report a gap, never pass an acceptance test through `skip`.
- [ ] Run `uv run --frozen pytest tests/test_source_fixtures.py -q`; confirm the missing evidence/recorder causes the expected failure.
- [ ] Implement the recorder and capture Limitless `/games`, paginated listings, event details/standings/pairings; capture RK9 discovery/event/entrant/public-team pages and access rules. Use actual event IDs obtained from the public listing.
- [ ] Retain representative public full-roster/open-sheet, private-field-missing, and no-team cases. Keep structural negative examples separately labeled; do not present invented full private builds as captured data.
- [ ] Remove credentials/private details. If pseudonymizing public identities, transform IDs consistently across standings, sheets, and pairings; record original and retained hashes separately. Inspect public participant data before inclusion.
- [ ] Rerun the fixture test and record the observed response shapes and source gaps in MEMORY.md. Do not enable either adapter yet.

## Task 2 — Define native and partial Champions contracts

**Files:** Create `core/models/champions.py`, `tests/test_champions_contracts.py`; modify only the legacy EV description in `core/models/contracts.py`.

**Interfaces:** Define these Pydantic models in `core/models/champions.py`:

- `StatPoints(hp, atk, def_, spa, spd, spe)`, with JSON alias `def` and `canonical() -> str`.
- `ItemSlot(state: Literal["unknown", "empty", "held"], item_id: str | None)`; only `held` has an ID.
- `EvidenceRef(url, captured_at, document_key, content_hash)` and `Diagnostic(code, path, message, evidence)`.
- `CapturedDocument(url, content_type, body: str, content_hash)` and `SourceCapture(source, source_event_id, fetched_at, documents: dict[str, CapturedDocument])` for public JSON/HTML bodies.
- `ChampionsPokemon(species_id, form_id, ability_id, moves, moves_complete, item, nature_id, stat_points, mega_variant_ids, field_evidence)`; unpublished attributes are nullable. Published moves have 1–4 unique IDs; completeness is separate. Mega IDs are derived capability with evidence, not a battle result.
- `TeamRosterV2(entry_id, player_id, player_name, standing, record, division_id, publication_status, pokemon, field_evidence)`: nullable unavailable metadata, zero to six observed Pokémon, separate `absent/partial/complete` roster status. A complete roster has six identified slots; build fields need not be complete.
- `MatchResultV2(match_id, phase, round, table, bracket_label, player1_id, player2_id, winner_id, outcome, score, evidence)`, with outcomes `win/tie/double_loss/bye/automatic_loss/pending/unknown` and nullable unpublished values.
- `TournamentMetadataV2(event_id, name, starts_at, completed_at, game_id, battle_format, division_id, rules_id, catalog_revision, game_data_revision, entrant_count, evidence)` and `ChampionsTournament(schema_version=2, metadata, teams, matches)`.
- `EventIdentification(status, metadata: TournamentMetadataV2 | None, diagnostics)` represents classification before team parsing; status is `verified/pending/rejected`.
- `NormalizationResult(admission_status, tournament: ChampionsTournament | None, diagnostics)`; status is `verified/pending/rejected`.

Use `extra="forbid"`. Unknown identity can be captured as pending but cannot qualify for analytic admission. Allocation nullability belongs to the whole spread; never fill unspecified stats with zero.

- [ ] Write tests asserting `32/32/2/0/0/0` is accepted; a stat of 33, negative value, total 67, Boolean, or fractional value is rejected. A missing spread remains `None` after JSON round trip.
- [ ] Add tests for unknown versus empty versus held item; unpublished nature/ability/moves; incomplete rosters; roster-complete/private-build-incomplete; duplicate moves; inconsistent winner/outcome; forbidden `tera_type` and legacy `evs` in the new model.
- [ ] Run `uv run --frozen pytest tests/test_champions_contracts.py -q`; confirm failures concern the missing contracts or intended validation.
- [ ] Implement these contracts. Use literal schema/version tags and strict integers. Preserve registered base/form rather than requiring an evolved form when a stone is held.
- [ ] Run `uv run --frozen pytest tests/test_champions_contracts.py tests/test_contracts.py -q`. Existing legacy tests retain traditional EV behavior; new tests assert native semantics.

## Task 3 — Establish catalog and source admission

**Files:** Create `core/models/catalog.py`, `core/catalog.py`, `core/source_admission.py`, `core/sources/admission.json`, `core/regulations/v2/index.json`, evidence-backed snapshots, `tests/test_catalog.py`, and `tests/test_source_admission.py`. Keep `core/config.py` and its legacy snapshot path separate.

**Interfaces:**

- `CatalogSnapshot`: snapshot/rules/game-data IDs, effective interval, source evidence/upstream revision, coverage/verification state, species/form/base-stat records, items/stones/Mega links, abilities, moves, species learnsets, and allocation rules.
- `SourceAdmission`: source, enabled flag, captured game/platform/format mappings, approved rules/catalog links, evidence, and adapter version. Ship unverified mappings disabled.
- `load_catalog(snapshot_id: str, *, index_path: Path) -> CatalogSnapshot` in `core/catalog.py`; caches by immutable ID, never by today's default regulation.
- `classify_event(capture: SourceCapture, admission: SourceAdmission) -> EventIdentification` in `core/source_admission.py`; identifies verified/pending/rejected event metadata before team normalization. The admission enable flag controls scheduled collection, not offline classification of already captured evidence.
- `validate_known_fields(tournament: ChampionsTournament, catalog: CatalogSnapshot) -> NormalizationResult` in `core/catalog.py`; validates only published fields, reports diagnostics, and preserves unrelated valid entries.

- [ ] Write tests rejecting a generic VGC/date/name match, a conflicting platform, and an unverified catalog. Event rules evidence selects the snapshot; the active/default snapshot is never substituted.
- [ ] Write catalog tests for a legal fixture build, known banned species, impossible species/ability or move combination, wrong stone/species pairing, base form with compatible stone, addition/removal across two snapshots, and unknown references on incomplete coverage.
- [ ] Run `uv run --frozen pytest tests/test_catalog.py tests/test_source_admission.py -q`; verify the intended failures.
- [ ] Construct complete resolved snapshots for admitted fixtures from official/in-game rules evidence and pinned community supplementary data. Resolve inherited species/items/moves/learnsets when exporting Showdown; copying only its Champions override files is not a complete catalog. Record source license and immutable revision.
- [ ] Implement explicit loading/classification/validation. Known illegality produces field/entry diagnostics; unknown coverage is pending reconciliation, not a proven ban. An invalid species slot makes that roster analytically incomplete; an invalid private attribute does not erase valid species evidence.
- [ ] Rerun focused tests. Compare declared coverage with the resolved upstream inventory and document unresolved history. Synthetic two-version catalogs establish behavior, but do not satisfy real historical coverage.

## Task 4 — Repair and normalize Limitless

**Files:** Modify `scraper/limitless_client.py`; create `scraper/limitless_normalizer.py` and `tests/test_limitless_champions.py`; revise relevant assertions in `tests/test_scraper.py` without discarding existing user additions.

**Interfaces:** Add `LimitlessAPIClient.get_games() -> list[dict[str, Any]]`, a `page: int | None = None` argument to `get_tournaments`, and `capture_tournament(tournament_id: str) -> SourceCapture`. Use `get_tournament_details` at `/tournaments/{id}/details`. Define `normalize_limitless_capture(capture: SourceCapture, admission: SourceAdmission, catalog: CatalogSnapshot) -> NormalizationResult` in the new normalizer.

- [ ] Write `test_documented_details_and_scalar_player_ids`: the requested path includes `/details`; player usernames are source-scoped IDs; absent dates/placing remain null rather than becoming today's date/first place.
- [ ] Add tests for captured team-list shapes; unpublished lists; registry-driven platform/format admission; page forwarding; wins/losses/ties; identical round/table numbers in different phases; bracket labels; winner `0`, `-1`, missing winner, and missing opponent.
- [ ] Test bounded HTTP behavior: at most three attempts for 429, 502/503/504, and timeouts; no retry of access refusals. Use the existing 15-second request timeout, cap retry delays at 30 seconds, and fall back to bounded backoff for malformed retry headers. Mock time in tests; exhaustion raises visibly.
- [ ] Run `uv run --frozen pytest tests/test_limitless_champions.py -q`; reproduce the scalar-ID regression before fixing it.
- [ ] Implement documented requests and pure captured-shape normalization. Use only verified game/format filters from Task 3; platform classification uses event details. Do not invent a platform query parameter or a completed-event date.
- [ ] Derive match IDs from source event + phase + round + table/bracket label. An empty opponent can mean bye or automatic loss; classify only when additional captured evidence distinguishes them, otherwise preserve `unknown`. Keep scores null when not published.
- [ ] Rerun `uv run --frozen pytest tests/test_limitless_champions.py tests/test_scraper.py -q`. Update fabricated nested-object fixtures to documented scalar shapes; keep synthetic cases explicitly identified.

## Task 5 — Extract actual public RK9 teams

**Files:** Modify `scraper/rk9_scraper.py`; create `scraper/rk9_normalizer.py` and `tests/test_rk9_champions.py`.

**Interfaces:** Define `RK9HTMLScraper.capture_tournament(tournament_id: str) -> SourceCapture` and `normalize_rk9_capture(capture: SourceCapture, admission: SourceAdmission, catalog: CatalogSnapshot) -> NormalizationResult`. Preserve public entrant/team-link identifiers; when no stable player identity exists, leave `player_id=None` and use a source event-entry identifier only where the page supplies one.

- [ ] Write captured-HTML tests for event metadata, division, actual team-list links, registered species/form/item/ability/moves, and omitted nature/allocations. Assert participant-table rows alone do not produce a published six-Pokémon roster.
- [ ] Add repeated-name and reordered-standing tests: identities come from captured identifiers, never display names or placing. Test robots denial, inaccessible sheets, unexpected markup, cross-host links, and unsupported private sheets.
- [ ] Run `uv run --frozen pytest tests/test_rk9_champions.py -q`; verify expected failures.
- [ ] Implement extraction against actual fixtures with an HTML parser. Follow only source-approved public link hosts/patterns; do not bypass access controls or fetch arbitrary pasted URLs. Preserve unknown record/standing/division instead of fabricating `0-0` or Masters.
- [ ] Apply the same three-attempt/15-second-timeout/30-second-delay ceilings as Task 4 to retryable page failures, alongside the source crawl delay. Access refusals remain diagnostics without retry loops.
- [ ] Distinguish a valid absent team list from malformed markup or transport failure. Missing stable entry identity stays pending for publication; do not create standing-derived player IDs.
- [ ] Rerun `uv run --frozen pytest tests/test_rk9_champions.py tests/test_scraper.py -q`; record verified fields and remaining RK9 visibility limits.

## Task 6 — Normalize independently valid evidence

**Files:** Create `ingestion/normalize.py`, `tests/test_champions_normalization.py`.

**Interfaces:** `normalize_capture(capture: SourceCapture, *, admission: SourceAdmission, catalog: CatalogSnapshot) -> NormalizationResult` dispatches to Tasks 4/5 and applies Task 3 legality checks. Unknown sources are rejected. `normalized_content_hash(tournament: ChampionsTournament) -> str` hashes deterministic semantic content including schema/catalog revisions, excluding fetch timestamps and capture-location references.

- [ ] Write tests with one malformed entrant alongside a valid entrant. Assert the valid entrant survives, diagnostics identify the invalid path, and roster/attribute completeness are not conflated.
- [ ] Test a full species roster with all private fields missing, an absent roster, a partly identified roster, an illegal published attribute, and an unknown catalog entry. There are no inferred zero allocations, neutral natures, or item values.
- [ ] Test native versus traditional-EV input: native fields normalize, legacy values never enter `stat_points` merely because numbers fit. Traditional conversions remain unsupported unless a separate sourced conversion is explicitly added later.
- [ ] Test repeated same-content captures: semantic hashes match despite different fetch times; changed moves/nature/roster/catalog revision change the hash. Source bodies and field provenance remain retained separately.
- [ ] Run `uv run --frozen pytest tests/test_champions_normalization.py -q`, implement the dispatcher/hash, then rerun. Catch specific expected entry-validation errors; programming/transport exceptions remain failures, not empty tournaments.

## Task 7 — Stage captures through an explicit source dispatcher

**Files:** Create `scraper/dispatcher.py`, `ingestion/staging.py`, `tests/test_champions_dispatcher.py`, and `tests/test_champions_staging.py`; modify `scraper/handler.py` and retire obsolete assembly in `scraper/limitless_client.py`.

**Interfaces:**

- `CollectionReport`: per-source discovered/captured/pending/rejected/failed counts, staged references, and diagnostic reasons. Define in `scraper/dispatcher.py`.
- `collect_sources(*, admissions: list[SourceAdmission], catalog_index: Path, s3_client: Any, bucket_name: str) -> CollectionReport` in `scraper/dispatcher.py`, using the two captured adapters. Classify before loading the mapped snapshot; normalize verified captures via Task 6 and stage their results. Pending captures retain diagnostics without guessed catalog selection.
- `stage_capture(capture: SourceCapture, *, s3_client: Any, bucket_name: str) -> str` and `stage_normalized(result: NormalizationResult, *, capture_key: str, s3_client: Any, bucket_name: str) -> str` in `ingestion/staging.py`.

- [ ] Write tests that disabled/unverified sources cause no collection, an explicitly admitted adapter runs, and Limitless/RK9 failures remain distinguishable from empty discovery. A configured source's exhausted operational failure must raise after independent valid work is staged; it cannot return Lambda success.
- [ ] Test immutable object keys/content, different captures, source/adapter/schema/catalog provenance, and explicit pending/diagnostic status. Assert all keys start with staging prefixes and none start with `raw/`.
- [ ] Run `uv run --frozen pytest tests/test_champions_dispatcher.py tests/test_champions_staging.py -q`; verify expected failures.
- [ ] Implement staging using content-addressed JSON captures and normalized results. A capture digest covers its complete recorded envelope, including retrieval time; its document hashes retain source-content identity. Encode source event identifiers safely in keys. Store capture references with normalized output; no DynamoDB write occurs.
- [ ] Stage each original capture before normalization. If parsing fails, the original remains available; a normalized result is stored only after contract validation. Pending/rejected classification is represented explicitly and is never an analytical success.
- [ ] Route the scheduled handler through explicit default-disabled admissions; remove the implicit generic-VGC/M-C ingestion path. Keep the current schedule unchanged. Missing admission configuration for an enabled source is a visible configuration error.
- [ ] Remove the obsolete `fetch_tournament_export` builder and raw-writing `fetch_and_store_tournaments` path after switching their callers/tests to capture/dispatch. The old scalar-ID assumptions and guessed metadata must not remain as an executable fallback; legacy compatibility applies to `/v1` data/contracts, not these incorrect internal collectors.
- [ ] Rerun focused tests and update the old collector tests that expected swallowed errors. Paginated durable discovery/checkpoints and refetch scheduling remain Phase 3; do not claim this dispatcher implements them.

## Task 8 — Prove a read-back preview and legacy isolation

**Files:** Create `ingestion/preview.py`, `tests/test_champions_preview.py`, and `tests/test_champions_pipeline.py`; add preview instructions to README.md. Modify `ingestion/handler.py` only to reject/skip non-legacy schemas and non-`raw/` prefixes before legacy parsing/writing.

**Interfaces:** `PreviewReport` in `ingestion/preview.py` contains the normalized tournament, admission/validation diagnostics, observed entrant/roster counts, and explicitly unknown fields; no percentages or regulation totals. `build_preview(result: NormalizationResult) -> PreviewReport`. CLI arguments: `--capture`, `--admission`, `--catalog-index`, `--output`; choose the catalog through verified event mapping, not a date/default.

- [ ] Write a Moto pipeline test for each admitted source: fixture capture → normalization → S3 staging → exact object read-back → `NormalizationResult` validation → preview JSON. Assert roster/build values and evidence match the captured fixtures.
- [ ] Write legacy-isolation tests: staging-prefix events and schema-2 payloads placed under `raw/` cannot reach `write_tournament_data`; legacy payloads still do. This guard does not repair the existing legacy batch/retry bugs reserved for Phase 1.
- [ ] Test pending/rejected captures: preview reports the reason and withholds analytical eligibility; invalid JSON or operational read failures produce nonzero CLI exit, not a success preview.
- [ ] Run `uv run --frozen pytest tests/test_champions_preview.py tests/test_champions_pipeline.py -q`; verify expected failures, then implement the preview and minimal legacy guard.
- [ ] Rerun the pipeline tests and existing `/v1` compatibility tests. Preview a captured bundle using `uv run python -m ingestion.preview --capture tests/fixtures/sources/limitless/accepted-capture.json --admission core/sources/admission.json --catalog-index core/regulations/v2/index.json --output /tmp/champions-phase-0-preview.json`. Task 1 creates this accepted bundle only after real admission evidence; do not manufacture it to make the command pass.
- [ ] Document that preview JSON is a local verification artifact, not a served `/v2` response. Hand validated immutable inputs to Phase 1 without publishing legacy aggregates.

## Task 9 — Verify the Phase 0 exit gate

**Files:** Update MEMORY.md, PHASES.md, SOURCE_UPDATE.md and README.md with measured results. No Terraform apply, deployment, staging, or commits.

- [ ] Run focused source/contract/catalog/pipeline tests; require both real adapters' acceptance cases to pass without synthetic substitutions or skipped evidence gates.
- [ ] Run `uv run --frozen pytest -q`; all legacy/new tests must pass. If an unrelated baseline failure exists, record and resolve its relevance rather than deleting the test.
- [ ] Run `uv run --frozen ruff check .` and `uv run --frozen ruff format --check .`; both must pass.
- [ ] Run the CI coverage command from AGENTS.md and record its measured result. There is no existing minimum threshold; coverage numbers do not establish source correctness.
- [ ] Confirm native/private-field semantics and fixture-to-preview values independently against captured source sheets. Confirm no new DynamoDB metrics or `raw/` staging writes.
- [ ] Mark only evidenced Phase 0 checklist items complete. Record unresolved source/history/access gaps; Phase 0 is complete only when its full exit gate is met. Prepare the Phase 1 publication plan next.

## Phase handoff and alignment coverage

| User goal | Phase 0 foundation | Shipping phase |
|---|---|---|
| Champions-only limited legal pool | Positive source identity and complete declared snapshots | 0, automated refresh in 3 |
| Teams, natures, allocations, moves, items, abilities | Nullable native contracts and captured public data | Public routes/distributions in 2 |
| EV/IV support appropriate to Champions | Native Stat Points; no fabricated traditional EV/variable-IV statistics | Contract in 0, documented API in 2 |
| Forms/Megas | Compatible registered stone/form capability with evidence | Breakdowns in 2 |
| Cores/usage/performance | Stable rosters, identities, results, and completeness | Safe contributions in 1; analytics in 2 |
| Corrections and tournament updates | Immutable staged inputs and visible source failures | Safe publication in 1; automatic refetch in 3 |
| New game content/rules | Game-data/rules revisions and explicit catalogs | Automatic detection/activation in 3 |
| Useful live REST API | Preserved prototype and repeatable input preview | Public API in 2; operational proof in 4 |

## Source references checked for planning

Checked 2026-10-04: [Limitless tournaments documentation](https://docs.limitlesstcg.com/developer/tournaments) and [game registry documentation](https://docs.limitlesstcg.com/developer/games) support the request/identity repair. Their examples do not establish the live Champions mapping or team-list shape. [Official Champions news](https://champions.pokemon.com/en-us/news/) and [Showdown's Champions implementation](https://github.com/smogon/pokemon-showdown/tree/master/data/mods/champions) are discovery/reference inputs; production snapshots still require pinned, complete evidence. This planning pass did not capture live acceptance fixtures or prove source admission.
