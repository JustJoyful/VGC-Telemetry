"""Deterministic bounded observed-entry contributions, never inferred top-cut analytics."""

from collections import defaultdict
from itertools import combinations

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from core.models.publication import ContributionBundle, RevisionInput, RevisionManifest
from db.schema_v2 import digest, native, revision_pk
from ingestion.revisions import revision_id
from ingestion.staging import _put_immutable, canonical_bytes


def semantic(value):
    if isinstance(value, dict):
        return {k: semantic(v) for k, v in value.items() if k not in ("evidence", "field_evidence")}
    if isinstance(value, list):
        return [semantic(v) for v in value]
    return value


def check_size(row):
    # Conservative JSON byte bound leaves room for DynamoDB name/type overhead.
    if len(canonical_bytes(native(row))) > 350_000:
        raise ValueError("DynamoDB row exceeds safe item size")


def query_rows(table, pk, prefix="", cursor=None, limit=100):
    condition = Key("PK").eq(pk)
    if prefix:
        condition &= Key("SK").begins_with(prefix)
    args = dict(KeyConditionExpression=condition, ConsistentRead=True, Limit=limit)
    if cursor:
        args["ExclusiveStartKey"] = cursor
    return table.query(**args)


def immutable_row(table, row):
    check_size(row)
    try:
        table.put_item(Item=row, ConditionExpression="attribute_not_exists(PK)")
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        old = table.get_item(Key={"PK": row["PK"], "SK": row["SK"]}, ConsistentRead=True)["Item"]
        if old != row:
            raise ValueError("Immutable revision row conflict") from error


def build_contributions(input: RevisionInput) -> ContributionBundle:
    tournament = input.result.tournament
    rid = revision_id(input)
    pk = revision_pk(input.event_id, rid)
    rows = [
        {
            "PK": pk,
            "SK": "ROW#EVENT",
            "kind": "event",
            "data": semantic(tournament.metadata.model_dump(mode="json")),
            "top_cut_eligibility": "unknown",
        }
    ]
    counters = defaultdict(lambda: [0, 0, 0])
    populations = set()
    day = (
        tournament.metadata.starts_at.date().isoformat() if tournament.metadata.starts_at else None
    )

    def population(division):
        meta = tournament.metadata
        return digest(
            [
                "observed_entries",
                input.source,
                meta.game_id,
                meta.battle_format,
                division,
                meta.competition_id,
                meta.rules_id,
                meta.catalog_revision,
                meta.game_data_revision,
                sorted(meta.match_formats),
                sorted(meta.custom_rules),
            ]
        )

    def add(pop, metric, count=0, known=0, unknown=0):
        key = (pop, tuple(metric))
        for i, v in enumerate((count, known, unknown)):
            counters[key][i] += v

    # Empty events still have a population and can remove earlier members/contributions.
    populations.add(population(tournament.metadata.division_id))
    for team in tournament.teams:
        pop = population(team.division_id or tournament.metadata.division_id)
        populations.add(pop)
        rows.append(
            {
                "PK": pk,
                "SK": f"ROW#TEAM#{digest(team.entry_id)}",
                "kind": "team",
                "population_id": pop,
                "data": semantic(team.model_dump(mode="json")),
                "top_cut_eligibility": "unknown",
            }
        )
        add(pop, ["coverage", team.publication_status], 1, 1)
        add(pop, ["entries"], 1, 1)
        if team.publication_status != "complete":
            continue
        add(pop, ["rosters"], 1, 1)
        identities = sorted({(p.species_id, p.form_id or "base") for p in team.pokemon})
        for size in (2, 3):
            for core in combinations(identities, size):
                add(pop, [f"core{size}", *[f"{s}/{f}" for s, f in core]], 1, 1)
        for sp, form in identities:
            add(pop, ["species", sp, form], 1, 1)
        for p in team.pokemon:
            identity = [p.species_id, p.form_id or "base"]
            attrs = {
                "ability": p.ability_id,
                "nature": p.nature_id,
                "stat_points": p.stat_points.canonical() if p.stat_points else None,
                "item": p.item.item_id
                if p.item.state == "held"
                else ("empty" if p.item.state == "empty" else None),
            }
            for attr, value in attrs.items():
                add(
                    pop,
                    [attr, *identity, "denominator"],
                    known=int(value is not None),
                    unknown=int(value is None),
                )
                if value is not None:
                    add(pop, [attr, *identity, value], 1, 1)
            add(
                pop,
                ["move", *identity, "denominator"],
                known=int(p.moves is not None),
                unknown=int(p.moves is None),
            )
            for move in p.moves or []:
                add(pop, ["move", *identity, move], 1, 1)
            add(
                pop,
                ["registered_mega", *identity, "denominator"],
                known=int(p.mega_variant_ids is not None),
                unknown=int(p.mega_variant_ids is None),
            )
            for mega in p.mega_variant_ids or []:
                add(pop, ["registered_mega", *identity, mega], 1, 1)
    for match in tournament.matches:
        rows.append(
            {
                "PK": pk,
                "SK": f"ROW#MATCH#{digest(match.match_id)}",
                "kind": "match",
                "data": semantic(match.model_dump(mode="json")),
            }
        )
    for (pop, metric), (count, known, unknown) in sorted(counters.items()):
        rows.append(
            {
                "PK": pk,
                "SK": f"ROW#METRIC#{pop}#{digest(list(metric))}",
                "kind": "metric",
                "population_id": pop,
                "day": day,
                "scope": "observed_entries",
                "metric": list(metric),
                "count": count,
                "known": known,
                "unknown": unknown,
            }
        )
    rows.sort(key=lambda row: row["SK"])
    # A pointer plus intents must fit one bounded DynamoDB transaction.
    if len(populations) > 40:
        raise ValueError("Too many populations for an atomic event promotion")
    return ContributionBundle(
        input=input, revision_id=rid, rows=rows, populations=sorted(populations)
    )


def stage_revision(
    bundle: ContributionBundle, *, table, s3_client, bucket: str
) -> RevisionManifest:
    for row in bundle.rows:
        check_size(row)
    body = canonical_bytes(bundle.rows)
    checksum = digest(bundle.rows)
    key = f"staging/v2/contributions/{checksum}.json"
    _put_immutable(key, body, s3_client=s3_client, bucket_name=bucket)
    for row in bundle.rows:
        immutable_row(table, row)
    pk = revision_pk(bundle.input.event_id, bundle.revision_id)
    # Read back the complete row set before making the revision ready.
    stored = []
    cursor = None
    while True:
        page = query_rows(table, pk, "ROW#", cursor)
        stored.extend(page["Items"])
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            break
    if len(stored) != len(bundle.rows) or digest(stored) != checksum:
        raise ValueError("Revision read-back count/checksum mismatch")
    manifest = RevisionManifest(
        event_id=bundle.input.event_id,
        revision_id=bundle.revision_id,
        order=bundle.input.order,
        populations=bundle.populations,
        row_count=len(stored),
        checksum=checksum,
        bucket=bucket,
        object_key=key,
    )
    observation = {
        "PK": pk,
        "SK": f"OBS#{bundle.input.order}",
        "input": bundle.input.model_dump(mode="json", exclude={"result"}),
    }
    # Same order can have independent captures with identical content.
    table.put_item(Item=observation)
    table.put_item(Item={"PK": pk, "SK": "MANIFEST", **manifest.model_dump(mode="json")})
    return manifest
