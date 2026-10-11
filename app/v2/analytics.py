"""Complete integer window reduction, then deterministic result pagination."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel

from app.models.v2 import CoreView, Coverage, MetricView, Page, Performance, SnapshotInfo
from app.models.v2_queries import CoreQuery, canonical_query
from app.v2.cursors import _decode, decode_cursor, decode_snapshot, encode_cursor
from app.v2.errors import V2Error, unavailable
from core.models.public_read import DailyContribution, PopulationKey, population_id
from core.public_reference import load_public_reference
from db.schema_public import snapshot_row_key
from ingestion.public_contributions import performance_percentage
from ingestion.staging import canonical_bytes


def broad():
    return V2Error(
        422,
        "query_too_broad",
        "Narrow dates, tournament or population filters",
        {"maximum_queries": 50, "maximum_contribution_rows": 5000},
    )


class Budget:
    def __init__(self, reader):
        self.reader, self.queries, self.rows = reader, 0, 0

    def pages(self, snapshot, section, *, contributions=False, prefix=None):
        position = None
        directory_rows = 0
        while True:
            if self.queries >= self.reader.config.analytics_queries:
                raise broad()
            limit = self.reader.config.analytics_rows - self.rows if contributions else 100
            if limit <= 0:
                raise broad()
            self.queries += 1
            kwargs = {"prefix": prefix} if prefix is not None else {}
            response = self.reader.query(
                snapshot, section, position=position, limit=limit, **kwargs
            )
            rows = response["Items"]
            if contributions:
                self.rows += len(rows)
                if self.rows > self.reader.config.analytics_rows:
                    raise broad()
            else:
                directory_rows += len(rows)
                if directory_rows > self.reader.config.listing_rows:
                    raise broad()
            yield rows
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                return
            position = cursor["SK"]


def catalog(revision, *, default=False, config=None):
    if config and config.catalog_runtime:
        from app.v2.catalog import resolve_catalog

        try:
            return resolve_catalog(revision, config=config)
        except ValueError as error:
            raise unavailable("Runtime catalog is unavailable") from error
    reference = load_public_reference(None)
    if revision not in reference["catalogs"]:
        if default:
            raise unavailable("Default native catalog is unavailable")
        raise V2Error(404, "not_found", "Catalog not found")
    return reference["catalogs"][revision]


def _select(snapshot, query, reader, budget, *, default_catalog_revision=None):
    default = not (query.catalog_revision or query.regulation or query.population_id)
    revision = query.catalog_revision or (
        (default_catalog_revision or reader.config.default_catalog_revision) if default else None
    )
    chosen_catalog = catalog(revision, default=default, config=reader.config) if revision else None
    start, end = _window(snapshot, query, reader)
    populations = {}
    for page in budget.pages(snapshot, "POPULATIONS"):
        for row in page:
            if query.tournament_id and row["SK"].partition(":")[2] != query.tournament_id:
                continue
            event_day = row.get("event_day")
            if event_day is not None:
                if not start <= date.fromisoformat(event_day) <= end:
                    continue
            elif not (query.tournament_id and query.date_from is None and query.date_to is None):
                continue
            key = PopulationKey.model_validate(row["data"])
            populations[population_id(key)] = key
    filters = {
        "source": query.source,
        "rules_id": query.regulation,
        "catalog_revision": revision,
        "division_id": query.division_id,
        "competition_id": query.competition_id,
        "game_data_revision": query.game_data_revision,
    }

    def matches(key):
        return (
            all(value is None or getattr(key, field) == value for field, value in filters.items())
            and (query.match_mode is None or query.match_mode in key.match_formats)
            and (query.custom_rule is None or query.custom_rule in key.custom_rules)
        )

    if query.population_id:
        selected = populations.get(query.population_id)
        if selected is None:
            raise V2Error(404, "not_found", "Population not found")
        if not matches(selected):
            raise V2Error(422, "invalid_query", "Population conflicts with explicit selectors")
        return selected
    choices = {pid: key for pid, key in populations.items() if matches(key)}
    if len(choices) > 1:
        raise V2Error(
            409,
            "ambiguous_population",
            "Select one exact population",
            {
                "choices": [
                    {"population_id": pid, **key.model_dump(mode="json")}
                    for pid, key in sorted(choices.items())[:20]
                ],
                "truncated": len(choices) > 20,
            },
        )
    if choices:
        return next(iter(choices.values()))
    if chosen_catalog is None:
        # Empty regulation selections still require a defined, verified native catalog.
        if query.regulation:
            candidates = [
                c
                for c in load_public_reference(None)["catalogs"].values()
                if c.rules_id == query.regulation
            ]
            if not candidates:
                raise V2Error(404, "not_found", "No catalog for requested regulation")
            if len(candidates) > 1:
                raise V2Error(409, "ambiguous_population", "Select explicit catalog revision")
            chosen_catalog = candidates[0]
        else:
            chosen_catalog = catalog(
                default_catalog_revision or reader.config.default_catalog_revision,
                default=True,
                config=reader.config,
            )
    if (
        query.regulation is not None
        and query.regulation != chosen_catalog.rules_id
        or query.game_data_revision is not None
        and query.game_data_revision != chosen_catalog.game_data_revision
    ):
        raise V2Error(422, "invalid_query", "Selectors conflict with the requested native catalog")
    return PopulationKey(
        source="limitless",
        game_id="pokemon-champions",
        battle_format="doubles",
        rules_id=chosen_catalog.rules_id,
        catalog_revision=chosen_catalog.snapshot_id,
        game_data_revision=chosen_catalog.game_data_revision,
        division_id=query.division_id,
        competition_id=query.competition_id,
        match_formats=[query.match_mode] if query.match_mode else [],
        custom_rules=[query.custom_rule] if query.custom_rule else [],
    )


def select_population(snapshot: SnapshotInfo, query: BaseModel, *, reader) -> PopulationKey:
    return _select(snapshot, query, reader, Budget(reader))


def _window(snapshot, query, reader):
    summary = reader.get(snapshot_row_key(snapshot.snapshot_id, "META", "SUMMARY")) or {}
    anchor = (
        date.fromisoformat(summary["latest_event_day"])
        if summary.get("latest_event_day")
        else (datetime.fromtimestamp(snapshot.expires_at - reader.config.token_seconds, UTC).date())
    )
    end = query.date_to or anchor
    start = query.date_from or end - timedelta(days=29)
    if (end - start).days < 0 or (end - start).days >= reader.config.analytics_days:
        raise broad()
    return start, end


def _focal(row, query):
    return (
        (not query.tournament_id or row.event_id == query.tournament_id)
        and (
            query.placement_min is None
            or row.placement is not None
            and row.placement >= query.placement_min
        )
        and (
            query.placement_max is None
            or row.placement is not None
            and row.placement <= query.placement_max
        )
    )


def reduce_window(
    contributions: Iterable[DailyContribution], *, query: BaseModel
) -> list[MetricView | CoreView]:
    selected = [r for r in contributions if _focal(r, query)]
    rows = [r for r in selected if r.group_by == query.group_by]
    metric = f"core{query.size}" if isinstance(query, CoreQuery) else query.metric
    roster_count = sum(r.count for r in rows if r.metric == "roster")
    excluded_rosters = sum(
        r.known_count - r.denominator for r in selected if r.metric.startswith("coverage:")
    )
    totals = defaultdict(lambda: defaultdict(int))
    denominators = defaultdict(lambda: defaultdict(int))
    performance = defaultdict(lambda: defaultdict(int))
    values = defaultdict(set)
    exemplar = rows[0] if rows else None
    for row in rows:
        if row.metric == "performance:" + metric:
            for field in (
                "wins",
                "losses",
                "ties",
                "double_losses",
                "mirrors",
                "byes",
                "unlinked",
                "excluded_count",
            ):
                performance[row.identity][field] += getattr(row, field)
        if row.metric != metric:
            continue
        if query.species_id and row.identity.split("/")[0] != query.species_id:
            continue
        if query.form_id and row.identity != query.species_id + "/" + query.form_id:
            continue
        if isinstance(query, CoreQuery) and not all(
            member in row.identity.split("+")
            if "/" in member
            else member in {part.split("/")[0] for part in row.identity.split("+")}
            for member in query.members
        ):
            continue
        if row.value is None and metric not in ("species", "core2", "core3"):
            for field in ("denominator", "known_count", "unknown_count", "excluded_count"):
                denominators[row.identity][field] += getattr(row, field)
        else:
            if query.move_id and row.value != query.move_id:
                continue
            totals[(row.identity, row.value)]["count"] += row.count
            values[row.identity].add(row.value)
    for identity in denominators:
        if not values[identity]:
            totals[(identity, query.move_id)]["count"] = 0
    result = []
    for (identity, value), counts in sorted(
        totals.items(), key=lambda pair: (pair[0][0], pair[0][1] or "")
    ):
        usage = metric in ("species", "core2", "core3")
        denominator = roster_count if usage else denominators[identity]["denominator"]
        rates = performance[identity]
        perf = Performance(
            wins=rates["wins"],
            losses=rates["losses"],
            ties=rates["ties"],
            double_losses=rates["double_losses"],
            played=rates["wins"] + rates["losses"] + rates["ties"],
            mirrors=rates["mirrors"],
            byes=rates["byes"],
            unlinked=rates["unlinked"],
            excluded_outcomes=rates["excluded_count"],
            percentage=performance_percentage(rates["wins"], rates["losses"], rates["ties"]),
        )
        fields = dict(
            metric=metric,
            identity=identity,
            value=value,
            group_by=query.group_by,
            count=counts["count"],
            denominator=denominator,
            percentage=round(counts["count"] * 100 / denominator, 6) if denominator else None,
            known_count=denominator,
            unknown_count=0 if usage else denominators[identity]["unknown_count"],
            excluded_count=excluded_rosters if usage else denominators[identity]["excluded_count"],
            population_id=exemplar.population_id,
            catalog_revision="pending",
            snapshot_id="pending",
            generated_at=None,
            performance=perf,
        )
        result.append(
            CoreView(
                **fields,
                members=identity.split("+"),
                size=query.size,
                team_members=[
                    "species:" + part.split("/")[0]
                    if query.group_by == "species"
                    else "form:"
                    + (part.split("/")[0] if part.split("/")[1] == "base" else part.split("/")[1])
                    for part in identity.split("+")
                ],
            )
            if isinstance(query, CoreQuery)
            else MetricView(**fields)
        )
    return result


def _read(snapshot, query, reader, *, default_catalog_revision=None):
    budget = Budget(reader)
    population = _select(
        snapshot, query, reader, budget, default_catalog_revision=default_catalog_revision
    )
    pid = population_id(population)
    pinned_catalog = catalog(population.catalog_revision, config=reader.config)
    if query.form_id:
        form = pinned_catalog.species.get(query.form_id)
        if form is None or form.base_species_id != query.species_id:
            raise V2Error(422, "invalid_query", "Species and form are incompatible")
    if isinstance(query, CoreQuery):
        from app.v2.catalog import validate_members

        selectors = []
        for member in query.members:
            parts = member.split("/")
            if len(parts) == 1:
                selectors.append("species:" + member)
            elif len(parts) == 2 and parts[1] == "base":
                selectors.append("species:" + parts[0])
            elif len(parts) == 2 and (
                parts[1] in pinned_catalog.species
                and pinned_catalog.species[parts[1]].base_species_id == parts[0]
            ):
                selectors.append("form:" + parts[1])
            else:
                raise V2Error(422, "invalid_query", "Unknown core member")
        validate_members(selectors, pinned_catalog)
    if query.move_id and query.move_id not in pinned_catalog.moves:
        raise V2Error(422, "invalid_query", "Move not found in selected catalog")
    if query.registered_mega_id:
        mega = pinned_catalog.species.get(query.registered_mega_id)
        if mega is None or not mega.is_mega:
            raise V2Error(422, "invalid_query", "Registered Mega not found in selected catalog")
    start, end = _window(snapshot, query, reader)
    days = set()
    for page in budget.pages(snapshot, "DAYS#" + pid):
        days.update(
            row["data"]["day"]
            for row in page
            if start <= date.fromisoformat(row["data"]["day"]) <= end
        )
    sections = ["DAY#" + pid + "#" + day for day in sorted(days)]
    if query.tournament_id and query.date_from is None and query.date_to is None:
        sections.append("UNDATED#" + pid)
    contributions = []
    for section in sections:
        for page in budget.pages(
            snapshot,
            section,
            contributions=True,
            prefix=query.tournament_id + ":" if query.tournament_id else None,
        ):
            contributions.extend(DailyContribution.model_validate(row["data"]) for row in page)
    matching, unknown = None, None
    if query.registered_mega_id:
        # Entry identity joins only evidence from this exact population and snapshot.
        def key(r):
            return (r.event_id, r.entry_id)

        eligible = {
            key(r)
            for r in contributions
            if _focal(r, query) and r.metric == "roster" and r.group_by == "form"
        }
        matching = {
            key(r)
            for r in contributions
            if key(r) in eligible
            and r.metric == "registered_mega"
            and r.value == query.registered_mega_id
            and r.count > 0
        }
        unknown = {
            key(r)
            for r in contributions
            if _focal(r, query)
            and r.group_by == "form"
            and (
                r.metric == "registered_mega"
                and r.identity.split("/")[0] == mega.base_species_id
                and r.unknown_count > 0
                or r.metric.startswith("coverage:")
                and r.known_count > 0
                and r.denominator == 0
            )
        } - matching
        contributions = [r for r in contributions if key(r) in matching]
    result = reduce_window(contributions, query=query)
    for row in result:
        row.catalog_revision, row.snapshot_id, row.generated_at = (
            population.catalog_revision,
            snapshot.snapshot_id,
            snapshot.generated_at,
        )
        row.provenance_links = [
            "/v2/tournaments",
            "/v2/pokemon?catalog_revision=" + population.catalog_revision,
        ]
        if row.metric == "nature" and row.value in pinned_catalog.natures:
            nature = pinned_catalog.natures[row.value]
            row.nature_alignment = {"plus": nature.plus, "minus": nature.minus}
    resolved = {
        **canonical_query(query),
        "catalog_revision": population.catalog_revision,
        "population_id": pid,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "order": "identity,value",
    }
    coverage = Coverage(
        scope_matching_rosters=len(matching) if matching is not None else 0,
        scope_unknown_rosters=len(unknown) if unknown is not None else 0,
    )
    for row in contributions:
        if row.group_by != "form" or not _focal(row, query):
            continue
        if row.metric.startswith("coverage:"):
            status = row.metric.partition(":")[2]
            field = "invalid_rosters" if status == "invalid" else status + "_rosters"
            setattr(coverage, field, getattr(coverage, field) + row.count)
            coverage.standings_entries += row.count
            coverage.proven_cut_participants += row.known_count
            coverage.mapped_participants += row.known_count if status != "invalid" else 0
            coverage.eligible_complete_rosters += row.denominator
            coverage.excluded_membership += row.excluded_count
            coverage.unknown_membership += row.unknown_count
        elif (
            row.metric in ("ability", "nature", "stat_points", "item", "move", "registered_mega")
            and row.value is None
        ):
            coverage.known_attributes[row.metric] = (
                coverage.known_attributes.get(row.metric, 0) + row.known_count
            )
            coverage.unknown_attributes[row.metric] = (
                coverage.unknown_attributes.get(row.metric, 0) + row.unknown_count
            )
            if row.metric == "stat_points":
                coverage.unknown_stat_points += row.unknown_count
    return result, resolved, coverage


def read_analytics(
    snapshot: SnapshotInfo, query: BaseModel, *, reader
) -> list[MetricView | CoreView]:
    return _read(snapshot, query, reader)[0]


def analytics_page(query, *, reader, endpoint):
    secret = reader.config.require_secret()
    pinned = None
    pinned_catalog_revision = None
    if query.cursor:
        value = _decode(query.cursor, now=reader.config.clock(), secret=secret, kind="cursor")
        pinned = SnapshotInfo(snapshot_id=value["snapshot_id"], expires_at=value["exp"])
        pinned_catalog_revision = value.get("catalog_revision")
    if query.snapshot:
        token = decode_snapshot(query.snapshot, now=reader.config.clock(), secret=secret)
        if pinned and pinned != token:
            raise V2Error(400, "invalid_cursor", "Snapshot and cursor differ")
        pinned = token
    snap = reader.resolve_snapshot(pinned.snapshot_id if pinned else None)
    if pinned:
        snap.expires_at = pinned.expires_at
    results, resolved, coverage = _read(
        snap, query, reader, default_catalog_revision=pinned_catalog_revision
    )
    position = None
    if query.cursor:
        position = decode_cursor(
            query.cursor,
            endpoint=endpoint,
            query=resolved,
            limit=query.limit,
            now=reader.config.clock(),
            secret=secret,
        )["position"]
    model = CoreView if isinstance(query, CoreQuery) else MetricView
    page = Page[model](items=[], snapshot=reader.token(snap), generated_at=snap.generated_at)
    for row in results:
        ordinal = row.identity + ":" + (row.value or "")
        if position is not None and ordinal <= position:
            continue
        candidate = page.model_copy(update={"items": [*page.items, row]})
        if (
            len(page.items) == query.limit
            or len(canonical_bytes(candidate.model_dump(mode="json"))) + 4096
            > reader.config.response_bytes
        ):
            if not page.items:
                raise unavailable("Metric exceeds supported response size")
            last = page.items[-1]
            page.next_cursor = encode_cursor(
                snapshot=snap,
                endpoint=endpoint,
                query=resolved,
                limit=query.limit,
                position=last.identity + ":" + (last.value or ""),
                secret=secret,
                catalog_revision=resolved["catalog_revision"],
            )
            break
        page.items.append(row)
    page.coverage = coverage
    reader._bounded(page)
    return page
