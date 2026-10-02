import logging
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from db.client import batch_write_with_retry
from db.schema import (
    ability_usage_sk,
    evspread_usage_sk,
    moves_usage_sk,
    partner_directional_sk,
    regulation_gsi2,
    regulation_pk,
    species_usage_sk,
    status_sk,
    tournament_metadata_sk,
    tournament_pk,
)
from ingestion.parser import ParsedTournament

logger = logging.getLogger(__name__)


LEDGER_CONDITION = "attribute_not_exists(processed_tids) OR NOT contains(processed_tids, :tid)"


def write_tournament_data(table: Any, parsed: ParsedTournament) -> bool:
    """Idempotently write tournament rows and aggregates to DynamoDB single table.

    1. Acquires lock with condition 'attribute_not_exists(PK) OR #st <> :processed'.
       Returns False if tournament is already PROCESSED.
    2. Writes team, match, and tournament-scoped aggregates in batches of 25 (SET semantics).
    3. Atomically increments regulation-scoped aggregates using ADD + processed_tids ledger.
    4. Sets STATUS#PROCESSED.
    """
    tid = parsed.export.metadata.tid
    reg = parsed.export.metadata.regulation
    t_date = parsed.export.metadata.date
    now_iso = datetime.now(timezone.utc).isoformat()

    # Step 1: Acquire lock
    try:
        table.put_item(
            Item={
                "PK": tournament_pk(tid),
                "SK": status_sk(),
                "status": "PROCESSING",
                "started_at": now_iso,
            },
            ConditionExpression="attribute_not_exists(PK) OR #st <> :processed",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":processed": "PROCESSED"},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("Tournament %s is already PROCESSED. Skipping duplicate ingestion.", tid)
            return False
        raise

    # Step 2: Assemble all tournament-scoped items for BatchWriteItem
    batch_items: list[dict[str, Any]] = []

    # Tournament Metadata
    gsi2_pk, gsi2_sk = regulation_gsi2(reg, t_date, tid)
    batch_items.append(
        {
            "PK": tournament_pk(tid),
            "SK": tournament_metadata_sk(),
            "GSI2PK": gsi2_pk,
            "GSI2SK": gsi2_sk,
            "tid": tid,
            "name": parsed.export.metadata.name,
            "date": t_date,
            "regulation": reg,
            "tier": parsed.export.metadata.tier,
            "format": parsed.export.metadata.format,
        }
    )

    # Teams & Matches
    batch_items.extend(parsed.team_rows)
    batch_items.extend(parsed.match_rows)

    # Tournament-scoped species usage (SET)
    for sp, count in parsed.species_counts.items():
        batch_items.append(
            {
                "PK": tournament_pk(tid),
                "SK": species_usage_sk(sp),
                "species": sp,
                "count": count,
            }
        )

    # Tournament-scoped moves usage (SET)
    for sp, moves_counter in parsed.moves_counts.items():
        batch_items.append(
            {
                "PK": tournament_pk(tid),
                "SK": moves_usage_sk(sp),
                "species": sp,
                "moves": dict(moves_counter),
            }
        )

    # Tournament-scoped ability usage (SET)
    for sp, ability_counter in parsed.ability_counts.items():
        batch_items.append(
            {
                "PK": tournament_pk(tid),
                "SK": ability_usage_sk(sp),
                "species": sp,
                "abilities": dict(ability_counter),
            }
        )

    # Tournament-scoped EV spread usage (SET)
    for sp, ev_counter in parsed.evspread_counts.items():
        batch_items.append(
            {
                "PK": tournament_pk(tid),
                "SK": evspread_usage_sk(sp),
                "species": sp,
                "ev_spreads": dict(ev_counter),
            }
        )

    # Tournament-scoped bidirectional partner pairs (SET)
    for (sp_a, sp_b), count in parsed.partner_counts.items():
        batch_items.append(
            {
                "PK": tournament_pk(tid),
                "SK": partner_directional_sk(sp_a, sp_b),
                "species": sp_a,
                "partner": sp_b,
                "count": count,
            }
        )

    # Execute batch writes
    batch_write_with_retry(table, batch_items, batch_size=25)

    # Step 3: Write Regulation-scoped aggregates with processed_tids ledger
    reg_pk = regulation_pk(reg)

    # Regulation species counts (UpdateItem ADD + ledger)
    for sp, count in parsed.species_counts.items():
        try:
            table.update_item(
                Key={"PK": reg_pk, "SK": species_usage_sk(sp)},
                UpdateExpression="ADD #cnt :delta, processed_tids :tid_set SET #sp = :sp",
                ConditionExpression=LEDGER_CONDITION,
                ExpressionAttributeNames={"#cnt": "count", "#sp": "species"},
                ExpressionAttributeValues={
                    ":delta": count,
                    ":tid_set": {tid},
                    ":tid": tid,
                    ":sp": sp,
                },
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    # Regulation partner pairs (UpdateItem ADD + ledger)
    for (sp_a, sp_b), count in parsed.partner_counts.items():
        try:
            table.update_item(
                Key={"PK": reg_pk, "SK": partner_directional_sk(sp_a, sp_b)},
                UpdateExpression=(
                    "ADD #cnt :delta, processed_tids :tid_set SET #sp = :sp, #pt = :pt"
                ),
                ConditionExpression=LEDGER_CONDITION,
                ExpressionAttributeNames={"#cnt": "count", "#sp": "species", "#pt": "partner"},
                ExpressionAttributeValues={
                    ":delta": count,
                    ":tid_set": {tid},
                    ":tid": tid,
                    ":sp": sp_a,
                    ":pt": sp_b,
                },
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    # Regulation movesets (read + merge with condition on ledger)
    for sp, moves_counter in parsed.moves_counts.items():
        _update_regulation_map_stat(
            table=table,
            reg_pk=reg_pk,
            sk=moves_usage_sk(sp),
            stat_field="moves",
            species=sp,
            delta_map=dict(moves_counter),
            tid=tid,
        )

    # Regulation abilities
    for sp, ability_counter in parsed.ability_counts.items():
        _update_regulation_map_stat(
            table=table,
            reg_pk=reg_pk,
            sk=ability_usage_sk(sp),
            stat_field="abilities",
            species=sp,
            delta_map=dict(ability_counter),
            tid=tid,
        )

    # Regulation EV spreads
    for sp, ev_counter in parsed.evspread_counts.items():
        _update_regulation_map_stat(
            table=table,
            reg_pk=reg_pk,
            sk=evspread_usage_sk(sp),
            stat_field="ev_spreads",
            species=sp,
            delta_map=dict(ev_counter),
            tid=tid,
        )

    # Step 4: Set STATUS#PROCESSED
    table.update_item(
        Key={"PK": tournament_pk(tid), "SK": status_sk()},
        UpdateExpression="SET #st = :p, processed_at = :now",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":p": "PROCESSED",
            ":now": datetime.now(timezone.utc).isoformat(),
        },
    )

    return True


def _update_regulation_map_stat(
    table: Any,
    reg_pk: str,
    sk: str,
    stat_field: str,
    species: str,
    delta_map: dict[str, int],
    tid: str,
) -> None:
    """Helper to update regulation map breakdown idempotently with processed_tids ledger."""
    try:
        curr = table.get_item(Key={"PK": reg_pk, "SK": sk}).get("Item")
        existing_tids = set(curr.get("processed_tids", set())) if curr else set()
        if tid in existing_tids:
            return

        merged = dict(curr.get(stat_field, {})) if curr else {}
        for k, v in delta_map.items():
            merged[k] = merged.get(k, 0) + v

        existing_tids.add(tid)

        table.put_item(
            Item={
                "PK": reg_pk,
                "SK": sk,
                "species": species,
                stat_field: merged,
                "processed_tids": existing_tids,
            },
            ConditionExpression=LEDGER_CONDITION,
            ExpressionAttributeValues={":tid": tid},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
