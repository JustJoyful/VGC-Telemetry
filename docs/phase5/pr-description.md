# Phase 5: query usability and self-hosted polish

## Changes

- `/v2/teams`: up to three repeated canonical `members` selectors (`species:`, `form:`, `mega:`), plus exact `population_id` and `publication_status`. Candidate pages remain bounded and may be empty with continuation.
- `/v2/meta`: canonical `move_id` for `metric=move`, and exact eligible-roster `registered_mega_id` scope with matching/unknown coverage. Percentages use known complete builds; unknown and zero remain distinct.
- `/v2/cores`: canonical species filters and returned `team_members` for population/catalog/snapshot-preserving team drilldown.
- `/v2/pokemon` and `/v2/moves`: bounded name/alias lookup with explicit canonical choices and ambiguity; move detail route.
- Container-only `seed-demo` refuses existing data and object versions. Dedicated local project/image/port overrides support isolated acceptance checks.
- Query/runbook documentation and roadmap: deferred AWS 4B, design-first Phase 6, unchanged optional Phase 7 scope.

## Examples

```http
GET /v2/teams?members=species:incineroar&members=species:rillaboom&publication_status=complete
GET /v2/meta?metric=move&move_id=protect&registered_mega_id=floettemega&species_id=floette
GET /v2/cores?size=2&members=incineroar&members=rillaboom
GET /v2/pokemon?name=Rotom
GET /v2/moves?name=PRO-TECT
```

The fixture walkthrough returns seven matching teams, a core count/denominator of 7/10, scoped Protect 3/3, known zero Thunderbolt 0/3, and explicit Rotom ambiguity. Open cores using returned `team_members`, `population_id`, `catalog_revision`, and the page's signed `snapshot`.

## Validation

See [evidence.md](evidence.md) for final revision identities, complete test results, Compose proof, persistence/recovery and review findings.

## Limitations

AWS deployment/capacity/cost/IAM and live tunnel/source checks remain pending. Sources and public tunnel remain disabled by default. Registered Mega describes roster capability; counts describe observed frequency. No Phase 6 frontend or Phase 7 extension is included.
