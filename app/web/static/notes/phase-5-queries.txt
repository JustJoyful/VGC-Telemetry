# Phase 5 query examples

Run these against the local fixture installation. `API_BASE` defaults to port 8080;
use `API_BASE=http://127.0.0.1:18085` for the isolated Phase 5 project.

```bash
API_BASE=${API_BASE:-http://127.0.0.1:8080}
curl -fsSG "$API_BASE/v2/teams" \
  --data-urlencode members=species:incineroar \
  --data-urlencode members=species:rillaboom \
  --data-urlencode publication_status=complete
curl -fsSG "$API_BASE/v2/teams" \
  --data-urlencode members=form:floetteeternal \
  --data-urlencode members=species:rillaboom
curl -fsSG "$API_BASE/v2/teams" --data-urlencode members=mega:floettemega
curl -fsSG "$API_BASE/v2/meta" --data-urlencode metric=move \
  --data-urlencode move_id=protect --data-urlencode registered_mega_id=floettemega
curl -fsSG "$API_BASE/v2/cores" --data-urlencode size=2 \
  --data-urlencode members=incineroar --data-urlencode members=rillaboom
curl -fsSG "$API_BASE/v2/pokemon" --data-urlencode name=Floette
curl -fsSG "$API_BASE/v2/pokemon" --data-urlencode name=Rotom
curl -fsSG "$API_BASE/v2/moves" --data-urlencode name=Protect \
  --data-urlencode catalog_revision=champions-mc-3661ce4
```

Team selectors are repeated `members` values, at most three. `species:` selects a
base species, `form:` its observed registration form, and `mega:` its registered
Mega capability. Every requested selector must have supporting data on the same
roster. Duplicate/redundant selectors for one base species and incompatible forms
are rejected. Partial rosters can match observed members, while absent/unknown
fields cannot satisfy a selector. Mega capability never proves battle activation.
Existing scalar filters retain their behavior. New members default to the configured
catalog, which is pinned for continuation. Listing order is event ID then entry ID.

Teams use the complete snapshot's `TEAMS` stream. Defaults: 25 returned items,
maximum 100, 20 storage queries and 2,000 candidates per request, 350,000 response
bytes. A short or empty filtered page can have `next_cursor`. Continue until it is
null; preserve all filters and `limit`. Cursors bind the versioned candidate plan,
member set, catalog and snapshot. Reordered member selectors are equivalent.

`/v2/meta?metric=move&move_id=protect` returns one selected move per observed Pokemon
identity, including true zeros for known complete move lists. `move_id` is valid
only for move metrics. `registered_mega_id` selects eligible complete rosters through
an exact retained event/entry join; it can scope another member's attributes, so
`species_id` is the focal Pokemon, independently of the roster's Mega species.
`scope_matching_rosters` reports proven matches; `scope_unknown_rosters` reports
unresolved capability/incomplete eligible roster evidence. Known nonmatches are
excluded. Existing pinned population, regulation, division, ruleset and 90-day,
5,000-row/50-query budgets still apply; over-budget windows fail with
`422 query_too_broad`, never partial analytics.

Move denominators count Pokemon slots with known complete move lists within eligible
complete rosters, independently of nature/Stat Point availability. An observed zero
has count 0 and percentage 0 with a positive denominator. Unknown move lists have
`unknown_count > 0`, denominator 0 and null percentage when nothing is known. No
matching population returns an empty items list and zero matching coverage. These
are observations, with provenance links; no field-wide lower bound is claimed.

Core counts and frequencies describe observed eligible complete rosters. They make
no strength/recommendation claim. `members=incineroar&members=rillaboom` selects
canonical pairs/triples; retained `species/base` and `species/form` identities work.
To open matching teams, use the core response's exact `population_id`, the page's
signed `snapshot` token, `publication_status=complete`, returned `team_members`,
and the core's `catalog_revision`.
For `group_by=species`, these use `species:`; for `group_by=form`, all use `form:`,
including canonical base forms such as `form:rotom`. Exact population selection can
also resolve its catalog when omitted, within the listing request budget. Apply the
same tournament/date/placement filters. This preserves division, custom-rule,
catalog and eligibility boundaries. Without the complete-roster filter, team search
may additionally return partial rosters with those observed members.

If analytics returns `409 ambiguous_population`, choose one exact `population_id`
from `error.details.choices` and repeat the query with it. Explicit conflicting
selectors return `422 invalid_query`. Team population selection filters exact IDs;
it does not select a neighboring or similar population.

Reference lists accept exact `name` lookup with punctuation/spacing normalization.
Canonical IDs and retained aliases win; a base display name with several forms
returns canonical choices and `lookup_status=ambiguous`. Inspect every page before
choosing; `lookup_status` describes all choices, even when the page limit is one.
Unknown names return 404. Pokemon entries report official registration eligibility
separately from community mechanics; move presence does not prove a Pokemon's
learnset or official legality. Pin `catalog_revision` or the independent reference
snapshot token. Reference and tournament snapshot tokens are different scopes.

Use the returned `snapshot` string for later pages and details. Signed cursors have
fixed expiry (24 hours by default). `410 cursor_expired` means restart the query from
a fresh snapshot. `400 invalid_cursor` means malformed/tampered token, changed
filters/limit/endpoint or mismatched snapshot; retrying the same bad token cannot fix
it. Other safe envelopes contain `error.code`, a public message and `request_id`.

## Executable fixture walkthrough

After `seed-demo`, run the Python example below inside the API container. It uses
only the standard library and the private `api:8080` endpoint. Exact fixture counts
apply before the destructive synthetic correction demo.

```bash
docker compose exec -T api python - <<'PY'
import json
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

def get(path, **params):
    url = 'http://api:8080' + path + '?' + urlencode(params, doseq=True)
    with urlopen(url) as response:
        return json.load(response)

params = dict(members=['species:incineroar', 'species:rillaboom'],
              publication_status='complete', limit=1)
first = get('/v2/teams', **params)
page, teams = first, list(first['items'])
while page['next_cursor']:
    page = get('/v2/teams', **params, cursor=page['next_cursor'])
    assert page['snapshot'] == first['snapshot']
    teams.extend(page['items'])
assert len(teams) == 7
cores = get('/v2/cores', size=2, members=['incineroar', 'rillaboom'])
core = cores['items'][0]
assert (core['count'], core['denominator']) == (7, 10)
matching = get('/v2/teams', members=core['team_members'],
               catalog_revision=core['catalog_revision'],
               publication_status='complete', population_id=core['population_id'],
               snapshot=cores['snapshot'])
assert len(matching['items']) == 7
usage = dict(metric='move', move_id='protect', registered_mega_id='floettemega',
             species_id='floette')
move = get('/v2/meta', **usage)['items'][0]
assert (move['count'], move['denominator'], move['percentage']) == (3, 3, 100)
zero = get('/v2/meta', **(usage | {'move_id': 'thunderbolt'}))['items'][0]
assert (zero['count'], zero['denominator'], zero['percentage']) == (0, 3, 0)
assert get('/v2/pokemon', name='Floette')['items'][0]['pokemon_id'] == 'floetteeternal'
assert get('/v2/pokemon', name='Rotom')['lookup_status'] == 'ambiguous'
assert get('/v2/moves', name=' PRO-TECT ')['items'][0]['move_id'] == 'protect'
for params, status, code in [
    ({'members': 'form:floettemega'}, 422, 'invalid_query'),
    ({'cursor': 'malformed'}, 400, 'invalid_cursor'),
]:
    try:
        get('/v2/teams', **params)
    except HTTPError as error:
        assert error.code == status and json.load(error)['error']['code'] == code
    else:
        raise AssertionError('expected safe error')
print(json.dumps({'member_teams': 7, 'core_count': 7, 'eligible_rosters': 10,
                  'scoped_move': '3/3', 'known_zero': '0/3', 'safe_errors': True}))
PY
```
