"""Presentation adapters over the existing bounded reads, with no metric reduction."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from app.db.public_queries import PublicReader
from app.models.v2_queries import AnalyticsQuery, CoreQuery, PageQuery, PokemonQuery, TeamQuery
from app.routers.v2.reference import pokemon
from app.routers.v2.status import data_status
from app.routers.v2.teams import team as read_team
from app.routers.v2.teams import teams as read_teams
from app.routers.v2.tournaments import event as read_event
from app.v2.analytics import _window, analytics_page, select_population
from app.v2.catalog import resolve_catalog
from app.v2.cursors import _decode, encode_snapshot
from app.v2.errors import V2Error
from app.web.models import ExplorerFilters, OverviewContext, PokemonContext, TeamContext
from core.models.public_read import population_id
from db.schema_public import snapshot_row_key


@lru_cache(maxsize=1)
def sprite_manifest():
    return json.loads(Path(__file__).with_name("sprites.json").read_text())


def sprite_url(identifier: str) -> str | None:
    manifest = sprite_manifest()
    entry = next((e for e in manifest["entries"] if e["champions_id"] == identifier), None)
    if entry is None or not entry["path_verified"]:
        return None
    return (
        "https://raw.githubusercontent.com/PokeAPI/sprites/"
        + manifest["sprite_revision"]
        + "/"
        + entry["path"]
    )


def query_url(path: str, filters: ExplorerFilters, **changes) -> str:
    values = filters.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    values["limit"] = filters.limit
    values.update(changes)
    pairs = []
    for key, value in values.items():
        if value is not None and value != []:
            pairs.extend((key, item) for item in value) if isinstance(
                value, list
            ) else pairs.append((key, value))
    return path + ("?" + urlencode(pairs) if pairs else "")


def analytics_query(filters: ExplorerFilters, **changes) -> AnalyticsQuery:
    values = filters.model_dump(mode="json", include=set(AnalyticsQuery.model_fields))
    values.update(changes)
    return AnalyticsQuery.model_validate(values)


def identity_id(identity: str) -> str:
    species, _, form = identity.partition("/")
    return species if form in ("", "base") else form


def identity_name(identity: str, catalog) -> str:
    identifier = identity_id(identity)
    entry = catalog.species.get(identifier)
    return entry.name if entry else identifier


def pin_population(reader: PublicReader, filters: ExplorerFilters):
    if filters.cursor and not filters.snapshot:
        retained = _decode(
            filters.cursor,
            now=reader.config.clock(),
            secret=reader.config.require_secret(),
            kind="cursor",
        )
        filters = filters.model_copy(
            update={
                "snapshot": encode_snapshot(
                    retained["snapshot_id"],
                    expires_at=retained["exp"],
                    secret=reader.config.require_secret(),
                ),
                "catalog_revision": filters.catalog_revision or retained.get("catalog_revision"),
            }
        )
    query = analytics_query(filters, cursor=None)
    snapshot, _ = reader.request_snapshot(query, "/v2/meta")
    population = select_population(snapshot, query, reader=reader)
    pinned = filters.model_copy(
        update={
            "snapshot": reader.token(snapshot),
            "catalog_revision": population.catalog_revision,
        }
    )
    if not (filters.tournament_id and filters.date_from is None and filters.date_to is None):
        start, end = _window(snapshot, query, reader)
        pinned = pinned.model_copy(update={"date_from": start, "date_to": end})
    return pinned, snapshot, population


def overview_context(reader: PublicReader, filters: ExplorerFilters) -> OverviewContext:
    status = data_status(reader=reader, now=datetime.fromtimestamp(reader.config.clock(), UTC))
    data = {"status": status, "usage": None, "cores": None, "selected": [], "coverage": None}
    if not status.snapshot_id and not filters.snapshot and not filters.cursor:
        return OverviewContext(filters, data)
    usage = analytics_page(
        analytics_query(filters, limit=min(filters.limit, 25)), reader=reader, endpoint="/v2/meta"
    )
    selected = filters.model_copy(update={"snapshot": usage.snapshot})
    if usage.items:
        selected = selected.model_copy(
            update={
                "catalog_revision": usage.items[0].catalog_revision,
                "population_id": usage.items[0].population_id,
            }
        )
    pinned, snapshot, population = pin_population(reader, selected)
    if not usage.items and not filters.population_id:
        # Empty windows have no published population ID to select on continuation.
        pinned = pinned.model_copy(update={"population_id": None})
    core_query = CoreQuery.model_validate(
        analytics_query(pinned, cursor=None, limit=5).model_dump()
    )
    cores = analytics_page(core_query, reader=reader, endpoint="/v2/cores")
    catalog = resolve_catalog(population.catalog_revision, config=reader.config)
    team_filters = team_scope(reader, pinned, snapshot, population)
    data.update(
        {
            "usage": usage,
            "cores": cores,
            "coverage": usage.coverage,
            "snapshot_id": snapshot.snapshot_id,
            "generated_at": usage.generated_at,
            "population": population,
            "catalog": catalog,
            "selected": sorted_usage(usage.items, pinned, catalog),
            "core_entries": core_entries(cores.items[:5], team_filters, pinned, catalog),
            "cores_next_url": query_url(
                "/explorer/cores", pinned, cursor=cores.next_cursor, limit=5
            )
            if cores.next_cursor
            else None,
            "next_url": query_url("/explorer", pinned, cursor=usage.next_cursor)
            if usage.next_cursor
            else None,
            "teams_url": query_url(
                "/explorer/teams",
                ExplorerFilters.model_validate(
                    team_query(
                        team_filters, cursor=None, publication_status="complete"
                    ).model_dump()
                ),
            )
            if team_filters
            else None,
            "team_scope_unavailable": team_filters is None,
            "summary": reader.get(snapshot_row_key(snapshot.snapshot_id, "META", "SUMMARY")) or {},
        }
    )
    data["team_feed"] = (
        teams_context(
            reader,
            team_filters.model_copy(update={"limit": 12, "cursor": None, "previous_event": None}),
        )
        if team_filters
        else None
    )
    return replace(OverviewContext(filters, data), filters=pinned)


def sorted_usage(rows, pinned, catalog):
    ordered = sorted(
        rows,
        key=lambda row: (
            row.percentage is None,
            -(row.percentage or 0),
            -row.count,
            identity_name(row.identity, catalog),
        ),
    )[:25]
    return [
        {
            "row": row,
            "rank": rank,
            "name": identity_name(row.identity, catalog),
            "pokemon_id": identity_id(row.identity),
            "sprite": sprite_url(identity_id(row.identity)),
            "url": query_url("/explorer/pokemon/" + identity_id(row.identity), pinned, cursor=None),
        }
        for rank, row in enumerate(ordered, 1)
    ]


def core_entries(rows, scope, pinned, catalog):
    entries = []
    for row in rows:
        members = [
            {"name": identity_name(identity, catalog), "sprite": sprite_url(identity_id(identity))}
            for identity in row.members
        ]
        values = {"members": row.team_members, "publication_status": "complete", "cursor": None}
        team_filters = (
            ExplorerFilters.model_validate(team_query(scope, **values).model_dump())
            if scope
            else None
        )
        entries.append(
            {
                "row": row,
                "pokemon": members,
                "url": query_url("/explorer/teams", team_filters) if team_filters else None,
                "expansion_url": query_url("/explorer/cores/teams", team_filters, limit=6)
                if team_filters
                else None,
            }
        )
    return entries


def cores_context(reader: PublicReader, filters: ExplorerFilters) -> OverviewContext:
    pinned, snapshot, population = pin_population(reader, filters)
    query = CoreQuery.model_validate(
        analytics_query(pinned, limit=min(filters.limit, 5)).model_dump()
    )
    page = analytics_page(query, reader=reader, endpoint="/v2/cores")
    catalog = resolve_catalog(population.catalog_revision, config=reader.config)
    scope = team_scope(reader, pinned, snapshot, population)
    return OverviewContext(
        pinned,
        {
            "core_entries": core_entries(page.items[:5], scope, pinned, catalog),
            "cores_next_url": query_url("/explorer/cores", pinned, cursor=page.next_cursor, limit=5)
            if page.next_cursor
            else None,
        },
    )


def evidence_context(reader: PublicReader) -> OverviewContext:
    status = data_status(reader=reader, now=datetime.fromtimestamp(reader.config.clock(), UTC))
    summary = (
        reader.get(snapshot_row_key(status.snapshot_id, "META", "SUMMARY"))
        if status.snapshot_id
        else None
    )
    return OverviewContext(
        ExplorerFilters(),
        {
            "status": status,
            "summary": summary or {},
            "sample": summary.get("coverage", {}).get("eligible_complete_rosters")
            if summary
            else None,
            "tournament_count": summary.get("event_count") if summary else None,
        },
    )


def safe_url(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username:
        return value
    if value.startswith("/v2/") and not parsed.netloc and not parsed.scheme:
        return value
    return None


def reference_name(identifier, catalog, collection):
    if identifier is None:
        return "Unknown"
    entry = getattr(catalog, collection).get(identifier) if catalog else None
    return entry.name if entry else identifier


def filter_context(context, path, explicit_filters=None):
    filters = context.filters
    visible = (explicit_filters or filters).model_dump(
        mode="json", exclude_none=True, exclude_defaults=True
    )
    chips = []
    for key, value in visible.items():
        if key in {
            "cursor",
            "snapshot",
            "limit",
            "catalog_revision",
            "population_id",
            "metric",
            "group_by",
        }:
            continue
        if isinstance(value, list):
            for member in value:
                chips.append(
                    {
                        "key": key + ":" + member,
                        "label": member,
                        "url": query_url(
                            path, filters, cursor=None, **{key: [v for v in value if v != member]}
                        ),
                    }
                )
        else:
            chips.append(
                {
                    "key": key,
                    "label": key.replace("_", " ") + ": " + str(value),
                    "url": query_url(
                        path,
                        filters,
                        cursor=None,
                        **({key: None, "form_id": None} if key == "species_id" else {key: None}),
                    ),
                }
            )
    context.data.update({"chips": chips, "form_path": path})
    return context


def team_query(filters, **changes):
    values = filters.model_dump(mode="json", include=set(TeamQuery.model_fields))
    values.update(changes)
    if filters.registered_mega_id:
        values["mega_variant_id"] = filters.registered_mega_id
    return TeamQuery.model_validate(values)


def team_scope(reader, filters, snapshot, population):
    """Only published exact populations can replace analytics-only selectors."""
    if filters.population_id:
        return filters
    identifier = population_id(population)
    if reader.query(snapshot, "POPULATIONS", prefix=identifier + ":", limit=1)["Items"]:
        return filters.model_copy(update={"population_id": identifier})
    return None if filters.custom_rule or filters.game_data_revision else filters


def teams_context(
    reader: PublicReader, filters: ExplorerFilters, *, previous_event=None
) -> OverviewContext:
    query = team_query(filters, limit=min(filters.limit, 12))
    page = read_teams(reader=reader, query=query)
    pinned = ExplorerFilters.model_validate(query.model_dump()).model_copy(
        update={"snapshot": page.snapshot, "limit": query.limit, "previous_event": None}
    )
    events = {}
    for event_id in dict.fromkeys(row.event_id for row in page.items):
        try:
            events[event_id] = read_event(
                event_id=event_id, reader=reader, query=PageQuery(snapshot=page.snapshot)
            )
        except V2Error as error:
            if error.code != "not_found":
                raise
            events[event_id] = None

    def ordering(row):
        event = events[row.event_id]
        count = event.entrant_count if event else None
        date = event.starts_at if event else row.starts_at
        # Tier is not exposed; missing entrant counts fall back to published date.
        return (
            count is None,
            -(count or 0),
            -(date.timestamp() if date else 0),
            row.event_id,
            row.standing is None,
            row.standing or 0,
            row.team_id,
        )

    entries = []
    for row in sorted(page.items, key=ordering):
        catalog = (
            resolve_catalog(row.catalog_revision, config=reader.config)
            if row.catalog_revision
            else None
        )
        entries.append(
            {
                "team": row,
                "tournament": events[row.event_id],
                "show_header": row.event_id != previous_event,
                "source_url": next(
                    (safe_url(e.url) for e in row.provenance if safe_url(e.url)),
                    query_url(
                        "/v2/teams/" + quote(row.team_id, safe=""),
                        ExplorerFilters(snapshot=page.snapshot),
                    ),
                ),
                "pokemon": [
                    {
                        "name": reference_name(p.form_id or p.species_id, catalog, "species"),
                        "sprite": sprite_url(p.form_id or p.species_id),
                        "build": p,
                        "ability": reference_name(p.ability_id, catalog, "abilities"),
                        "item": "No held item"
                        if p.item.state == "empty"
                        else reference_name(p.item.item_id, catalog, "items"),
                        "moves": [reference_name(move, catalog, "moves") for move in p.moves or []],
                        "nature": reference_name(p.nature_id, catalog, "natures"),
                    }
                    for p in row.pokemon
                ],
                "url": query_url(
                    "/explorer/teams/" + quote(row.team_id, safe=""), pinned, cursor=None
                ),
                "names": [
                    reference_name(p.form_id or p.species_id, catalog, "species")
                    for p in row.pokemon
                ],
            }
        )
        previous_event = row.event_id
    return filter_context(
        OverviewContext(
            pinned,
            {
                "teams": page,
                "entries": entries,
                "ordering_note": (
                    "Teams in this loaded page are ordered by tournament size and placement. "
                    "More teams may include larger events. Unknown tournament sizes "
                    "use newest date first."
                ),
                "pokemon_options": {
                    "form:" + (p.form_id or p.species_id): reference_name(
                        p.form_id or p.species_id,
                        resolve_catalog(row.catalog_revision, config=reader.config)
                        if row.catalog_revision
                        else None,
                        "species",
                    )
                    for row in page.items
                    for p in row.pokemon
                },
                "events": events,
                "next_url": query_url(
                    "/explorer/teams/feed",
                    pinned,
                    cursor=page.next_cursor,
                    previous_event=previous_event,
                )
                if page.next_cursor
                else None,
                "status": data_status(
                    reader=reader, now=datetime.fromtimestamp(reader.config.clock(), UTC)
                ),
            },
        ),
        "/explorer/teams",
    )


def pokemon_context(
    reader: PublicReader, pokemon_id: str, filters: ExplorerFilters
) -> PokemonContext:
    status = data_status(reader=reader, now=datetime.fromtimestamp(reader.config.clock(), UTC))
    pinned = filters
    team_filters = filters
    if status.snapshot_id or filters.snapshot or filters.cursor:
        pinned, snapshot, population = pin_population(reader, filters)
        team_filters = team_scope(reader, pinned, snapshot, population)
    elif filters.custom_rule or filters.game_data_revision:
        team_filters = None
    reference = pokemon(
        config=reader.config,
        query=PokemonQuery(name=pokemon_id, catalog_revision=pinned.catalog_revision),
    )
    data = {
        "status": status,
        "reference": reference,
        "choices": reference.items,
        "usage": None,
        "metrics": [],
        "partners": [],
        "coverage": None,
    }
    if reference.lookup_status == "ambiguous":
        data["choice_links"] = [
            {
                "name": v.name,
                "url": query_url("/explorer/pokemon/" + v.pokemon_id, pinned, cursor=None),
            }
            for v in reference.items
        ]
        data["more_choices"] = (
            "/v2/pokemon?"
            + urlencode(
                {
                    "name": pokemon_id,
                    "catalog_revision": reference.items[0].catalog_revision,
                    "cursor": reference.next_cursor,
                }
            )
            if reference.next_cursor and reference.items
            else None
        )
        return PokemonContext(pinned, data, pokemon_id)
    view = reference.items[0]
    pinned = pinned.model_copy(update={"catalog_revision": view.catalog_revision})
    catalog = resolve_catalog(view.catalog_revision, config=reader.config)
    data.update(
        {
            "pokemon": view,
            "catalog": catalog,
            "sprite": sprite_url(view.pokemon_id),
            "reference_abilities": [
                reference_name(v, catalog, "abilities") for v in view.ability_ids
            ],
            "back_url": query_url(
                "/explorer",
                ExplorerFilters.model_validate(
                    analytics_query(
                        pinned, cursor=None, metric="species", move_id=None
                    ).model_dump()
                ),
            ),
            "teams_url": query_url(
                "/explorer/teams",
                ExplorerFilters.model_validate(
                    team_query(
                        team_filters,
                        cursor=None,
                        species_id=None,
                        form_id=None,
                        catalog_revision=view.catalog_revision,
                        publication_status="complete",
                        members=[("mega:" if view.is_mega else "form:") + view.pokemon_id],
                    ).model_dump()
                ),
            )
            if team_filters
            else None,
            "learnset_url": view.learnset_link + "&snapshot=" + quote(reference.snapshot, safe=""),
        }
    )
    if not pinned.snapshot or view.battle_only:
        return PokemonContext(pinned, data, pokemon_id)
    identity = (
        view.species_id + "/" + ("base" if view.pokemon_id == view.species_id else view.pokemon_id)
    )
    common = {
        "species_id": view.species_id,
        "form_id": None if view.pokemon_id == view.species_id else view.pokemon_id,
        "cursor": None,
        "metric": "species",
        "move_id": None,
    }
    usage = analytics_page(analytics_query(pinned, **common), reader=reader, endpoint="/v2/meta")
    data["usage"] = usage
    data["coverage"] = usage.coverage
    data["usage_rows"] = [row for row in usage.items if row.identity == identity]
    labels = {
        "move": "Observed moves",
        "ability": "Observed abilities",
        "item": "Observed items",
        "nature": "Observed natures",
        "stat_points": "Stat Points",
        "registered_mega": "Registered Mega capability",
    }
    for metric, label in labels.items():
        changes = {**common, "metric": metric}
        if metric == filters.metric:
            changes.update({"move_id": filters.move_id, "cursor": filters.cursor})
        page = analytics_page(
            analytics_query(pinned, **changes), reader=reader, endpoint="/v2/meta"
        )
        rows = [row for row in page.items if row.identity == identity]
        collection = {
            "move": "moves",
            "ability": "abilities",
            "item": "items",
            "nature": "natures",
            "registered_mega": "species",
        }.get(metric)
        entries = [
            {
                "row": row,
                "name": reference_name(row.value, catalog, collection)
                if collection
                else row.value or "Unknown",
            }
            for row in rows
        ]
        next_url = (
            query_url(
                "/explorer/pokemon/" + view.pokemon_id,
                pinned,
                metric=metric,
                cursor=page.next_cursor,
                move_id=filters.move_id if metric == "move" else None,
            )
            if page.next_cursor
            else None
        )
        data["metrics"].append(
            {"metric": metric, "label": label, "entries": entries, "next_url": next_url}
        )
    values = analytics_query(pinned, **common).model_dump()
    values.update({"species_id": None, "form_id": None, "members": [identity], "limit": 3})
    partners = analytics_page(CoreQuery.model_validate(values), reader=reader, endpoint="/v2/cores")
    for row in partners.items:
        data["partners"].append(
            {
                "row": row,
                "names": [identity_name(v, catalog) for v in row.members],
                "url": query_url(
                    "/explorer/teams",
                    ExplorerFilters.model_validate(
                        team_query(
                            team_filters,
                            cursor=None,
                            species_id=None,
                            form_id=None,
                            publication_status="complete",
                            members=row.team_members,
                        ).model_dump()
                    ),
                ),
            }
        )
    return PokemonContext(pinned, data, pokemon_id)


def team_context(reader: PublicReader, team_id: str, filters: ExplorerFilters) -> TeamContext:
    row = read_team(
        team_id=team_id,
        reader=reader,
        query=PageQuery(snapshot=filters.snapshot, cursor=filters.cursor, limit=filters.limit),
    )
    pinned = filters.model_copy(update={"snapshot": row.snapshot, "cursor": None})
    catalog = (
        resolve_catalog(row.catalog_revision, config=reader.config)
        if row.catalog_revision
        else None
    )
    common = analytics_query(
        pinned,
        cursor=None,
        metric="species",
        move_id=None,
        species_id=None,
        form_id=None,
        catalog_revision=row.catalog_revision,
    ).model_dump()
    builds = []
    for index, value in enumerate(row.pokemon):
        identifier = value.form_id or value.species_id
        builds.append(
            {
                "build": value,
                "id": "member-" + str(index),
                "name": reference_name(identifier, catalog, "species"),
                "sprite": sprite_url(identifier),
                "pokemon_url": query_url(
                    "/explorer/pokemon/" + identifier, ExplorerFilters.model_validate(common)
                ),
                "ability": reference_name(value.ability_id, catalog, "abilities"),
                "moves": [(v, reference_name(v, catalog, "moves")) for v in value.moves or []],
                "item": "No held item (confirmed)"
                if value.item.state == "empty"
                else reference_name(value.item.item_id, catalog, "items"),
                "nature": reference_name(value.nature_id, catalog, "natures"),
                "mega_names": [
                    reference_name(v, catalog, "species") for v in value.mega_variant_ids or []
                ],
            }
        )
    return TeamContext(
        pinned,
        {
            "team": row,
            "builds": builds,
            "catalog": catalog,
            "back_url": query_url("/explorer/teams", pinned),
            "evidence_url": "/v2/teams/"
            + quote(team_id, safe="")
            + "/evidence?"
            + urlencode({"snapshot": row.snapshot}),
            "status": data_status(
                reader=reader, now=datetime.fromtimestamp(reader.config.clock(), UTC)
            ),
        },
        team_id,
    )


def provenance_url(link, data_snapshot, reference_snapshot):
    if safe_url(link) is None:
        return None
    parsed = urlsplit(link)
    if not parsed.path.startswith("/v2/") or parsed.netloc:
        return link
    snapshot = (
        reference_snapshot
        if parsed.path.startswith(("/v2/pokemon", "/v2/moves", "/v2/regulations"))
        else data_snapshot
    )
    values = [(k, v) for k, v in parse_qsl(parsed.query) if k != "snapshot"]
    if snapshot:
        values.append(("snapshot", snapshot))
    return urlunsplit(parsed._replace(query=urlencode(values)))


API_EXAMPLES = {
    "status": ("/v2/status", "Status", "Freshness and coverage"),
    "meta": ("/v2/meta", "Meta", "Observed exact forms"),
    "cores": ("/v2/cores", "Cores", "Observed roster pairs"),
    "teams": ("/v2/teams", "Teams", "Published eligible teams"),
    "pokemon": ("/v2/pokemon", "Pokémon", "Pinned reference catalog"),
    "tournaments": ("/v2/tournaments", "Tournaments", "Published events"),
}


def api_example(reader: PublicReader, example: str) -> dict:
    """Fixed native GET reads; caller validates the example before storage access."""
    from app.models.v2_queries import TournamentQuery
    from app.routers.v2.tournaments import tournaments
    from app.v2.errors import V2Error

    path, label, description = API_EXAMPLES[example]
    if example == "status":
        result = data_status(reader=reader, now=datetime.fromtimestamp(reader.config.clock(), UTC))
        query = ""
    else:
        query = "?limit=3"
        try:
            if example == "meta":
                result = analytics_page(AnalyticsQuery(limit=3), reader=reader, endpoint=path)
            elif example == "cores":
                result = analytics_page(CoreQuery(limit=3), reader=reader, endpoint=path)
            elif example == "teams":
                result = read_teams(reader=reader, query=TeamQuery(limit=3))
            elif example == "pokemon":
                result = pokemon(config=reader.config, query=PokemonQuery(limit=3))
            else:
                result = tournaments(reader=reader, query=TournamentQuery(limit=3))
        except V2Error as error:
            return {
                "request_url": path + query,
                "response_status": error.status,
                "response_json": json.dumps(
                    {
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "details": error.details,
                        }
                    },
                    indent=2,
                ),
                "catalog_revision": reader.config.default_catalog_revision,
            }
    return {
        "request_url": path + query,
        "response_status": 200,
        "response_json": json.dumps(result.model_dump(mode="json"), indent=2),
        "catalog_revision": reader.config.default_catalog_revision,
    }
