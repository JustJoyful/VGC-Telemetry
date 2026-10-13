"""Consistent COMPLETE-snapshot reads; bounded base-table queries only."""

import logging
from datetime import UTC, datetime
from typing import TypeVar

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ValidationError

from app.models.v2 import (
    Coverage,
    EventSummary,
    MatchView,
    Page,
    PublicEvidence,
    SnapshotInfo,
    TeamView,
)
from app.models.v2_queries import TeamQuery, canonical_query
from app.v2.catalog import resolve_catalog, validate_members
from app.v2.config import V2ReadConfig, get_v2_config
from app.v2.cursors import _decode, decode_cursor, decode_snapshot, encode_cursor, encode_snapshot
from app.v2.errors import V2Error, unavailable
from core.models.public_read import PUBLIC_SCHEMA, PopulationKey, population_id
from core.top_cut import POLICY_VERSION
from db.client import get_dynamodb_table
from db.schema_public import public_state_key, snapshot_pk, snapshot_row_key
from db.schema_v2 import native
from ingestion.staging import canonical_bytes

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class PublicReader:
    def __init__(self, table, *, config: V2ReadConfig):
        self.table, self.config = table, config
        self._listing_queries = 0

    def get(self, key):
        try:
            return native(self.table.get_item(Key=key, ConsistentRead=True).get("Item"))
        except (ClientError, BotoCoreError) as error:
            logger.exception("Public storage read failed")
            raise unavailable() from error

    def query(self, snapshot, section, *, position=None, limit=100, prefix=None):
        pk = snapshot_pk(snapshot.snapshot_id, section)
        args = {"KeyConditionExpression": Key("PK").eq(pk), "ConsistentRead": True, "Limit": limit}
        if prefix is not None:
            args["KeyConditionExpression"] &= Key("SK").begins_with(prefix)
        if position is not None:
            args["ExclusiveStartKey"] = {"PK": pk, "SK": position}
        try:
            return native(self.table.query(**args))
        except (ClientError, BotoCoreError) as error:
            logger.exception("Public storage query failed")
            raise unavailable() from error

    def resolve_snapshot(self, snapshot_id: str | None) -> SnapshotInfo:
        self.config.require_secret()
        if snapshot_id is None:
            state = self.get(public_state_key()) or {}
            snapshot_id = state.get("snapshot_id")
        if not snapshot_id:
            raise unavailable("No complete public snapshot")
        marker = self.get(snapshot_row_key(snapshot_id, "META", "COMPLETE"))
        if (
            not marker
            or marker.get("schema_version") != PUBLIC_SCHEMA
            or marker.get("policy_version") != POLICY_VERSION
        ):
            raise unavailable("Snapshot is unavailable")
        info = self.get(snapshot_row_key(snapshot_id, "META", "INFO")) or {}
        return SnapshotInfo(
            snapshot_id=snapshot_id,
            expires_at=self.config.clock() + self.config.token_seconds,
            generated_at=info.get("generated_at"),
        )

    def token(self, snapshot):
        return encode_snapshot(
            snapshot.snapshot_id,
            expires_at=snapshot.expires_at,
            secret=self.config.require_secret(),
        )

    def request_snapshot(self, query, endpoint):
        secret = self.config.require_secret()
        position = None
        pinned = None
        self._listing_queries = 0
        if isinstance(query, TeamQuery) and query.members and not query.catalog_revision:
            retained = (
                _decode(query.cursor, now=self.config.clock(), secret=secret, kind="cursor")
                if query.cursor
                else {}
            )
            revision = retained.get("catalog_revision")
            if revision is None and query.population_id:
                if self.config.listing_queries < 2:
                    raise V2Error(
                        422, "query_too_broad", "Specify catalog_revision with population"
                    )
                token = (
                    decode_snapshot(query.snapshot, now=self.config.clock(), secret=secret)
                    if query.snapshot
                    else None
                )
                chosen = self.resolve_snapshot(token.snapshot_id if token else None)
                if token:
                    chosen.expires_at = token.expires_at
                rows = self.query(chosen, "POPULATIONS", prefix=query.population_id + ":", limit=1)[
                    "Items"
                ]
                self._listing_queries += 1
                if not rows:
                    raise V2Error(404, "not_found", "Population not found")
                try:
                    population = PopulationKey.model_validate(rows[0]["data"])
                    if population_id(population) != query.population_id:
                        raise ValueError("Population identity mismatch")
                except (KeyError, ValueError) as error:
                    raise unavailable("Published population is unavailable") from error
                revision = population.catalog_revision
                pinned = SnapshotInfo(snapshot_id=chosen.snapshot_id, expires_at=chosen.expires_at)
            query.catalog_revision = revision or self.config.default_catalog_revision
        if query.cursor:
            value = decode_cursor(
                query.cursor,
                endpoint=endpoint,
                query=canonical_query(query),
                limit=query.limit,
                now=self.config.clock(),
                secret=secret,
            )
            pinned = SnapshotInfo(snapshot_id=value["snapshot_id"], expires_at=value["exp"])
            position = value["position"]
        if query.snapshot:
            token = decode_snapshot(query.snapshot, now=self.config.clock(), secret=secret)
            if pinned and pinned != token:
                raise V2Error(400, "invalid_cursor", "Snapshot and cursor differ")
            pinned = token
        snapshot = self.resolve_snapshot(pinned.snapshot_id if pinned else None)
        if pinned:
            snapshot.expires_at = pinned.expires_at
        return snapshot, position

    def _detail(self, snapshot, section, model: type[T]) -> T:
        row = self.get(snapshot_row_key(snapshot.snapshot_id, section, "DETAIL"))
        if not row:
            raise V2Error(404, "not_found", "Resource not found")
        value = self._dto(row, model)
        value.snapshot, value.generated_at = self.token(snapshot), snapshot.generated_at
        self._bounded(value)
        return value

    def get_event(self, snapshot: SnapshotInfo, event_id: str) -> EventSummary:
        return self._detail(snapshot, "EVENT#" + event_id, EventSummary)

    def get_team(self, snapshot: SnapshotInfo, team_id: str) -> TeamView:
        return self._detail(snapshot, "TEAM#" + team_id, TeamView)

    def _dto(self, row, model):
        try:
            return model.model_validate(row["data"])
        except (KeyError, ValueError, ValidationError) as error:
            logger.exception("Invalid public row")
            raise unavailable("Published item is unavailable") from error

    def _bounded(self, model):
        if (
            len(canonical_bytes(model.model_dump(mode="json", by_alias=True)))
            > self.config.response_bytes
        ):
            raise unavailable("Published item exceeds response size")

    def _matches(self, data, query):
        values = query.model_dump(exclude_none=True)
        mapping = {
            "publication_status": "publication_status",
            "population_id": "population_id",
            "regulation": "rules_id",
            "division_id": "division_id",
            "competition_id": "competition_id",
            "catalog_revision": "catalog_revision",
            "player_id": "player_id",
            "tournament_id": "event_id",
            "phase": "phase",
            "outcome": "outcome",
        }
        for param, field in mapping.items():
            if param in values and data.get(field) != values[param]:
                return False
        if values.get("source") and values["source"] != data.get("source", "limitless"):
            return False
        if values.get("match_mode") and values["match_mode"] not in data.get("match_formats", []):
            return False
        if values.get("cut_status") and values["cut_status"] != data.get("cut", {}).get("status"):
            return False
        if (
            values.get("eligibility") == "included"
            and data.get("eligibility", {}).get("status") != "included"
        ):
            return False
        for bound, sign in (("placement_min", 1), ("placement_max", -1)):
            if bound in values and (
                data.get("standing") is None or sign * data["standing"] < sign * values[bound]
            ):
                return False
        day = (
            datetime.fromisoformat(data["starts_at"]).astimezone(UTC).date()
            if data.get("starts_at")
            else None
        )
        for bound, sign in (("date_from", 1), ("date_to", -1)):
            if bound in values and (day is None or sign * (day - values[bound]).days < 0):
                return False
        if values.get("species_id") or values.get("form_id"):
            if not any(
                (not values.get("species_id") or mon["species_id"] == values["species_id"])
                and (not values.get("form_id") or mon.get("form_id") == values["form_id"])
                for mon in data.get("pokemon", [])
            ):
                return False
        if values.get("mega_variant_id") and not any(
            values["mega_variant_id"] in (mon.get("mega_variant_ids") or [])
            for mon in data.get("pokemon", [])
        ):
            return False
        for selector in values.get("members", []):
            kind, identifier = selector.split(":")
            if not any(
                mon.get("species_id") == identifier
                if kind == "species"
                else identifier in (mon.get("mega_variant_ids") or [])
                if kind == "mega"
                else (mon.get("form_id") or mon.get("species_id")) == identifier
                for mon in data.get("pokemon", [])
            ):
                return False
        return True

    def page(
        self,
        snapshot: SnapshotInfo,
        *,
        section: str,
        query: BaseModel,
        position: str | None,
        limit: int,
    ) -> Page:
        if not 1 <= limit <= 100:
            raise V2Error(422, "invalid_query", "Limit must be 1–100")
        if section == "EVENTS":
            model, endpoint = EventSummary, "/v2/tournaments"
        elif section == "TEAMS" or section.startswith("REG#"):
            model, endpoint = TeamView, "/v2/teams"
        elif section.startswith("PLAYER#"):
            model, endpoint = TeamView, "/v2/players/" + section.removeprefix("PLAYER#")
        elif section.startswith("EVIDENCE#TEAM#"):
            model, endpoint = (
                PublicEvidence,
                "/v2/teams/" + section.removeprefix("EVIDENCE#TEAM#") + "/evidence",
            )
        elif section.endswith("#STANDINGS"):
            model, endpoint = (
                TeamView,
                "/v2/tournaments/"
                + section.removeprefix("EVENT#").removesuffix("#STANDINGS")
                + "/standings",
            )
        elif section.endswith("#MATCHES"):
            model, endpoint = (
                MatchView,
                "/v2/tournaments/"
                + section.removeprefix("EVENT#").removesuffix("#MATCHES")
                + "/matches",
            )
        else:
            raise ValueError("Unknown public collection section")
        if getattr(query, "form_id", None) and getattr(query, "species_id", None):
            try:
                catalog = resolve_catalog(
                    getattr(query, "catalog_revision", None)
                    or self.config.default_catalog_revision,
                    config=self.config,
                )
            except ValueError as error:
                raise unavailable("Catalog is unavailable") from error
            form = catalog.species.get(query.form_id)
            if form is None or form.base_species_id != query.species_id:
                raise V2Error(422, "invalid_query", "Species and form are incompatible")
        if getattr(query, "members", None):
            revision = query.catalog_revision or self.config.default_catalog_revision
            query.catalog_revision = revision
            try:
                pinned_catalog = resolve_catalog(revision, config=self.config)
            except ValueError as error:
                raise V2Error(422, "invalid_query", "Catalog not found") from error
            validate_members(query.members, pinned_catalog)
        summary = self.get(snapshot_row_key(snapshot.snapshot_id, "META", "SUMMARY")) or {}
        page = Page[model](
            items=[],
            snapshot=self.token(snapshot),
            generated_at=snapshot.generated_at,
            coverage=Coverage(**summary.get("coverage", {})),
        )
        inspected, continuation = 0, None
        for _ in range(self.config.listing_queries - self._listing_queries):
            response = self.query(
                snapshot,
                section,
                position=position,
                limit=min(self.config.candidate_page_size, self.config.listing_rows - inspected),
            )
            candidates = response["Items"]
            for row in candidates:
                inspected += 1
                if self._matches(row["data"], query):
                    if len(page.items) == limit:
                        continuation = position
                        break
                    item = self._dto(row, model)
                    if isinstance(item, (TeamView, EventSummary)):
                        item.snapshot, item.generated_at = page.snapshot, snapshot.generated_at
                    candidate = page.model_copy(update={"items": [*page.items, item]})
                    # Reserve the maximum token length for a continuation envelope.
                    if (
                        len(canonical_bytes(candidate.model_dump(mode="json", by_alias=True)))
                        + 4096
                        > self.config.response_bytes
                    ):
                        if not page.items:
                            raise unavailable("Published item exceeds response size")
                        continuation = position
                        break
                    page.items.append(item)
                position = row["SK"]
            else:
                if not response.get("LastEvaluatedKey"):
                    continuation = None
                    break
                continuation = position
                if inspected < self.config.listing_rows:
                    continue
            break
        else:
            continuation = position
        if continuation is not None:
            page.next_cursor = encode_cursor(
                snapshot=snapshot,
                endpoint=endpoint,
                query=canonical_query(query),
                limit=limit,
                position=continuation,
                secret=self.config.require_secret(),
                catalog_revision=getattr(query, "catalog_revision", None),
            )
        self._bounded(page)
        return page


def get_public_reader() -> PublicReader:
    config = get_v2_config()
    config.require_secret()
    return PublicReader(get_dynamodb_table(), config=config)
