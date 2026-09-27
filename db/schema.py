"""DynamoDB single-table schema key builders and formatting utilities.

Encapsulates all partition key (PK) and sort key (SK) patterns, along with
GSI1 and GSI2 attribute builders, ensuring zero key drift between ingestion
and the public API read models.
"""


def tournament_pk(tid: str) -> str:
    return f"TOURNAMENT#{tid}"


def tournament_metadata_sk() -> str:
    return "METADATA"


def team_sk(player_id: str) -> str:
    return f"TEAM#{player_id}"


def match_sk(round_num: int, match_id: str) -> str:
    return f"MATCH#{round_num}#{match_id}"


def status_sk() -> str:
    return "STATUS"


def species_usage_sk(species: str) -> str:
    return f"USAGE#{species}"


def moves_usage_sk(species: str) -> str:
    return f"MOVES#{species}"


def ability_usage_sk(species: str) -> str:
    return f"ABILITY#{species}"


def evspread_usage_sk(species: str) -> str:
    return f"EVSPREAD#{species}"


def partner_sk(species_a: str, species_b: str) -> str:
    """Canonicalize partner pairs alphabetically so order never creates duplicate items."""
    s1, s2 = sorted([species_a, species_b])
    return f"PARTNER#{s1}#{s2}"


def regulation_pk(regulation: str) -> str:
    return f"REGULATION#{regulation}"


def rate_limit_pk(client_ip: str) -> str:
    return f"RATELIMIT#{client_ip}"


def rate_limit_sk(epoch_minute: int) -> str:
    return f"WINDOW#{epoch_minute}"


def team_gsi1(player_id: str, date: str, tid: str) -> tuple[str, str]:
    """GSI1: Query player tournament history sorted chronologically."""
    return f"PLAYER#{player_id}", f"TOURNAMENT#{date}#{tid}"


def regulation_gsi2(regulation: str, date: str, tid: str) -> tuple[str, str]:
    """GSI2: Query tournaments by regulation sorted by date."""
    return f"REGULATION#{regulation}", f"DATE#{date}#{tid}"
