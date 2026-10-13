"""HTML routes alongside the unchanged JSON API."""

import logging
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.db.public_queries import PublicReader, get_public_reader
from app.models.v2_queries import AnalyticsQuery, CoreQuery, TeamQuery
from app.v2.cursors import decode_snapshot
from app.v2.errors import V2Error
from app.web.data import (
    cores_context,
    evidence_context,
    filter_context,
    overview_context,
    pokemon_context,
    provenance_url,
    query_url,
    safe_url,
    sprite_url,
    team_context,
    teams_context,
)
from app.web.models import ExplorerFilters, FeedQuery

WEB_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_ROOT / "templates")
templates.env.globals["explorer_script"] = (WEB_ROOT / "static" / "app.js").read_text()
templates.env.globals["sprite_url"] = sprite_url
templates.env.filters["safe_url"] = safe_url
templates.env.filters["provenance_url"] = provenance_url
logger = logging.getLogger(__name__)


def web_reader(request: Request, reader: Annotated[PublicReader, Depends(get_public_reader)]):
    request.state.read_config = reader.config
    return reader


Reader = Annotated[PublicReader, Depends(web_reader)]


def render_page(request: Request, template_name: str, context: dict, *, status_code=200):
    headers = {
        "Vary": "HX-Request, HX-Boosted, HX-History-Restore-Request",
        "Cache-Control": "no-store",
    }
    if request.headers.get("HX-Boosted") == "true":
        headers.update({"HX-Reselect": "#main", "HX-Retarget": "#main", "HX-Reswap": "outerHTML"})
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
        headers=headers,
    )


def error_page(request, error):
    query = list(request.query_params.multi_items())
    if error.code in {"cursor_expired", "invalid_cursor"}:
        config = getattr(request.state, "read_config", None)
        retained_snapshot = request.query_params.get("snapshot")
        try:
            if config is None or retained_snapshot is None:
                raise ValueError("No authenticated snapshot context")
            decode_snapshot(retained_snapshot, now=config.clock(), secret=config.require_secret())
        except (V2Error, ValueError):
            retained_snapshot = None
        query = [
            (k, v) for k, v in query if k != "cursor" and (k != "snapshot" or retained_snapshot)
        ]
    recovery = request.url.path + ("?" + urlencode(query) if query else "")
    current = [(k, v) for k, v in query if k not in {"snapshot", "cursor"}]
    current_url = request.url.path + ("?" + urlencode(current) if current else "")
    choices = []
    for choice in (error.details or {}).get("choices", []):
        pairs = [(k, v) for k, v in query if k not in {"cursor", "population_id"}]
        choices.append(
            {
                **choice,
                "url": request.url.path
                + "?"
                + urlencode(pairs + [("population_id", choice["population_id"])]),
            }
        )
    model = TeamQuery if request.url.path.startswith("/explorer/teams") else AnalyticsQuery
    allowed = (
        set(model.model_fields)
        if request.url.path == "/explorer"
        or request.url.path.startswith(("/explorer/teams", "/explorer/pokemon/"))
        else {"example"}
        if request.url.path == "/explorer/api"
        else set()
    )
    values = {k: v for k, v in query if k in allowed and k not in {"cursor", "snapshot", "members"}}
    if "regulation" in allowed:
        for key, default in {
            "regulation": "",
            "source": "",
            "date_from": "",
            "date_to": "",
            "limit": 25,
        }.items():
            values.setdefault(key, default)
    elif "example" in allowed:
        values.setdefault("example", "status")
    fields = [
        {
            "name": k,
            "label": k.replace("_", " ").capitalize(),
            "value": v,
            "type": "date"
            if k in {"date_from", "date_to"}
            else "number"
            if k == "limit"
            else "text",
        }
        for k, v in values.items()
    ]
    fields += [
        {"name": "members", "label": "Member " + str(i + 1), "value": v, "type": "text"}
        for i, v in enumerate(request.query_params.getlist("members")[:4])
        if "members" in allowed
    ]
    snapshot_value = next((v for k, v in query if k == "snapshot"), None)
    messages = {
        "data_unavailable": "Unavailable",
        "cursor_expired": "Query expired",
        "ambiguous_population": "Select one exact population",
        "invalid_query": "Invalid query",
        "query_too_broad": "Narrow your query",
        "invalid_cursor": "Invalid continuation",
        "not_found": "No matching entry",
    }
    return render_page(
        request,
        "partials/feed_error.html"
        if request.url.path == "/explorer/teams/feed"
        and (
            request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-Boosted") != "true"
        )
        else "partials/error_results.html"
        if (
            request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-Boosted") != "true"
        )
        and request.headers.get("HX-History-Restore-Request") != "true"
        else "error.html",
        {
            "message": messages.get(error.code, "Request failed"),
            "explanation": error.message,
            "recovery_url": recovery,
            "choices": choices,
            "current_url": current_url,
            "recovery_fields": fields,
            "recovery_snapshot": snapshot_value,
            "form_path": request.url.path,
            "enhanced_form": request.url.path in {"/explorer", "/explorer/teams"},
        },
        status_code=error.status,
    )


class HTMLRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except V2Error as error:
                return error_page(request, error)
            except (ValidationError, RequestValidationError):
                return error_page(
                    request,
                    V2Error(422, "invalid_query", "Check the supported filters and query limits."),
                )
            except Exception:
                logger.exception("Explorer request failed")
                return error_page(
                    request,
                    V2Error(500, "internal_error", "Public data could not be read. Try again."),
                )

        return safe_handler


router = APIRouter(prefix="/explorer", include_in_schema=False, route_class=HTMLRoute)


def read_filters(request: Request, model=AnalyticsQuery, *, default_limit=25) -> ExplorerFilters:
    values = dict(request.query_params)
    if set(values) - set(model.model_fields):
        raise V2Error(422, "invalid_query", "This page does not support those filters.")
    if "members" in values:
        values["members"] = [v for v in request.query_params.getlist("members") if v]
    values = {k: v for k, v in values.items() if v != ""}
    values.setdefault("limit", default_limit)
    validated = model.model_validate(values)
    if model is CoreQuery and validated.size != 2:
        raise V2Error(
            422, "invalid_query", "Common cores shows Pokémon pairs. Use the API for larger cores."
        )
    if model is AnalyticsQuery and validated.group_by != "form":
        raise V2Error(
            422,
            "invalid_query",
            "Explorer entries use exact forms. Use the API for species grouping.",
        )
    return ExplorerFilters.model_validate(validated.model_dump(exclude={"size"}))


@router.get("")
def overview(request: Request, reader: Reader):
    filters = read_filters(request, default_limit=25)
    if filters.metric != "species" or filters.move_id:
        raise V2Error(422, "invalid_query", "Choose an entity entry for attribute metrics.")
    context = filter_context(overview_context(reader, filters), "/explorer", filters)
    return result_page(
        request, "overview.html", "partials/pokemon_results.html", context, "Explore the meta."
    )


def result_page(request, full, partial, context, title):
    enhanced = (
        request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"
    ) and request.headers.get("HX-History-Restore-Request") != "true"
    response = render_page(request, partial if enhanced else full, {"ctx": context, "title": title})
    if enhanced:
        changes = {}
        if request.url.path == "/explorer":
            changes = {
                field: request.query_params.get(field) or None for field in ("date_from", "date_to")
            }
        response.headers["HX-Push-Url"] = query_url(request.url.path, context.filters, **changes)
    return response


@router.get("/cores")
def cores_page(request: Request, reader: Reader):
    context = cores_context(reader, read_filters(request, CoreQuery, default_limit=5))
    return render_page(
        request,
        "partials/core_list.html"
        if (
            request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-Boosted") != "true"
        )
        else "cores.html",
        {"ctx": context, "title": "Common cores"},
    )


@router.get("/cores/teams")
def core_teams_page(request: Request, reader: Reader):
    filters = read_filters(request, TeamQuery, default_limit=6)
    if not filters.members:
        raise V2Error(422, "invalid_query", "Choose a core to see its teams.")
    context = teams_context(reader, filters.model_copy(update={"limit": min(filters.limit, 6)}))
    context.data["all_teams_url"] = query_url(
        "/explorer/teams", context.filters, cursor=None, limit=25
    )
    return render_page(
        request,
        "partials/core_teams.html"
        if (
            request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-Boosted") != "true"
        )
        else "core_teams.html",
        {"ctx": context, "title": "Teams with this core"},
    )


@router.get("/teams")
def teams_page(request: Request, reader: Reader):
    context = teams_context(reader, read_filters(request, FeedQuery, default_limit=12))
    return result_page(
        request, "teams.html", "partials/team_results.html", context, "Published teams"
    )


@router.get("/teams/feed")
def team_feed_page(request: Request, reader: Reader):
    filters = read_filters(request, FeedQuery, default_limit=12)
    enhanced = (
        request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"
    ) and request.headers.get("HX-History-Restore-Request") != "true"
    context = teams_context(
        reader,
        filters,
        previous_event=filters.previous_event if enhanced and filters.cursor else None,
    )
    response = render_page(
        request,
        "partials/team_feed_items.html" if enhanced else "teams.html",
        {
            "ctx": context,
            "title": "Tournament teams",
            "refresh_filter_chips": enhanced and not filters.cursor,
        },
    )
    if enhanced and not filters.cursor:
        response.headers["HX-Push-Url"] = query_url(
            "/explorer/teams", context.filters, cursor=None, previous_event=None
        )
    return response


@router.get("/pokemon/{pokemon_id}")
def pokemon_page(request: Request, pokemon_id: str, reader: Reader):
    context = pokemon_context(reader, pokemon_id, read_filters(request))
    return render_page(
        request,
        "pokemon.html",
        {
            "ctx": context,
            "title": context.data.get("pokemon").name
            if context.data.get("pokemon")
            else "Choose an exact form",
        },
    )


@router.get("/teams/{team_id}")
def team_page(request: Request, team_id: str, reader: Reader):
    context = team_context(reader, team_id, read_filters(request, TeamQuery))
    return render_page(
        request,
        "team.html",
        {"ctx": context, "title": "Tournament team"},
    )


@router.get("/api")
def api_page(request: Request, reader: Reader):
    from app.web import data

    if set(request.query_params) - {"example"}:
        raise V2Error(422, "invalid_query", "Choose one of the fixed read-only examples.")
    example = request.query_params.get("example", "status")
    if example not in data.API_EXAMPLES:
        raise V2Error(422, "invalid_query", "Choose one of the fixed read-only examples.")
    schema = request.app.openapi()
    examples = {
        key: value
        for key, value in data.API_EXAMPLES.items()
        if "get" in schema["paths"].get(value[0], {})
    }
    return render_page(
        request,
        "api_explorer.html",
        {
            "title": "Explore the API.",
            "example": example,
            "examples": examples,
            "preview": data.api_example(reader, example),
        },
    )


@router.get("/about")
def about_page(request: Request, reader: Reader):
    if request.query_params:
        raise V2Error(422, "invalid_query", "Evidence does not accept query parameters.")
    return render_page(
        request, "about.html", {"title": "Read the evidence", "ctx": evidence_context(reader)}
    )
