import time

from ingestion.parser import parse_tournament_export
from ingestion.writer import write_tournament_data


def generate_synthetic_tournament(num_teams: int) -> dict:
    species_pool = [
        "Swampert",
        "Incineroar",
        "Amoonguss",
        "Zapdos",
        "Rillaboom",
        "Garchomp",
        "Gholdengo",
        "Kingambit",
        "Flutter Mane",
        "Urshifu",
        "Ogerpon",
        "Tornadus",
        "Landorus",
        "Chien-Pao",
        "Iron Hands",
    ]

    teams = []
    for i in range(num_teams):
        # Pick 6 distinct species deterministically based on team index
        start_idx = i % len(species_pool)
        selected_species = [species_pool[(start_idx + j) % len(species_pool)] for j in range(6)]

        pokemon = []
        for sp in selected_species:
            pokemon.append(
                {
                    "species": sp,
                    "ability": "DefaultAbility",
                    "moves": ["MoveA", "MoveB", "MoveC", "Protect"],
                    "evs": {"hp": 252, "atk": 252, "def": 4, "spa": 0, "spd": 0, "spe": 0},
                }
            )

        teams.append(
            {
                "player_id": f"player_{i:04d}",
                "player_name": f"Competitor {i:04d}",
                "standing": i + 1,
                "record": f"{max(0, 7 - (i // 10))}-{min(7, i // 10)}",
                "pokemon": pokemon,
            }
        )

    matches = []
    for i in range(num_teams // 2):
        matches.append(
            {
                "round": 1,
                "match_id": f"round1_match_{i:03d}",
                "player1_id": f"player_{2 * i:04d}",
                "player2_id": f"player_{2 * i + 1:04d}",
                "winner_id": f"player_{2 * i:04d}",
                "score": "2-0",
            }
        )

    return {
        "metadata": {
            "tid": f"SYNTH_{num_teams}",
            "name": f"Synthetic Championship {num_teams}",
            "date": "2026-09-22",
            "regulation": "M-C",
            "tier": "Major",
            "format": "Pokémon Champions VGC",
        },
        "teams": teams,
        "matches": matches,
    }


class DynamoDBCallTracker:
    def __init__(self, table):
        self.table = table
        self.update_calls = 0
        self.partner_update_calls = 0
        self.species_update_calls = 0
        self.status_update_calls = 0
        self.put_calls = 0
        self.get_calls = 0
        self.batch_calls = 0
        self.batch_items_total = 0

        self._orig_update = table.update_item
        self._orig_put = table.put_item
        self._orig_get = table.get_item
        self._orig_batch = table.meta.client.batch_write_item

        table.update_item = self._track_update
        table.put_item = self._track_put
        table.get_item = self._track_get
        table.meta.client.batch_write_item = self._track_batch

    def _track_update(self, *args, **kwargs):
        self.update_calls += 1
        key = kwargs.get("Key", {})
        sk = key.get("SK", "")
        if sk.startswith("PARTNER#"):
            self.partner_update_calls += 1
        elif sk.startswith("USAGE#"):
            self.species_update_calls += 1
        elif sk == "STATUS":
            self.status_update_calls += 1
        return self._orig_update(*args, **kwargs)

    def _track_put(self, *args, **kwargs):
        self.put_calls += 1
        return self._orig_put(*args, **kwargs)

    def _track_get(self, *args, **kwargs):
        self.get_calls += 1
        return self._orig_get(*args, **kwargs)

    def _track_batch(self, *args, **kwargs):
        self.batch_calls += 1
        req_items = kwargs.get("RequestItems", {})
        for _, items in req_items.items():
            self.batch_items_total += len(items)
        return self._orig_batch(*args, **kwargs)

    def restore(self):
        self.table.update_item = self._orig_update
        self.table.put_item = self._orig_put
        self.table.get_item = self._orig_get
        self.table.meta.client.batch_write_item = self._orig_batch


def test_write_budget_64_teams(dynamodb_table):
    """Verify standard 64-team tournament ingestion fits in write budget and execution time."""
    payload = generate_synthetic_tournament(num_teams=64)
    parsed = parse_tournament_export(payload)

    assert len(parsed.team_rows) == 64
    assert len(parsed.match_rows) == 32

    tracker = DynamoDBCallTracker(dynamodb_table)
    try:
        write_start = time.time()
        success = write_tournament_data(dynamodb_table, parsed)
        write_time = time.time() - write_start

        assert success is True
        assert write_time < 10.0

        # Exact call count assertions for 64 teams:
        # 15-species cyclic pool with length-6 sliding window yields exactly 150 directional pairs
        # Batch items: 1 metadata + 64 teams + 32 matches + 60 stats + 150 partners = 307 items
        # 307 items / 25 per batch = 13 batch calls
        assert tracker.batch_calls == 13
        assert tracker.batch_items_total == 307
        # Regulation updates: 15 species + 150 partners + 1 status = 166 UpdateItem calls
        assert tracker.update_calls == 166
        assert tracker.partner_update_calls == 150
        assert tracker.species_update_calls == 15
        assert tracker.status_update_calls == 1
        # Regulation maps: 15 moves + 15 abilities + 15 evs = 45 get calls
        # Put calls: 1 crash-safe lock (STATUS#PROCESSING) + 45 regulation map puts = 46 put calls
        assert tracker.get_calls == 45
        assert tracker.put_calls == 46
    finally:
        tracker.restore()


def test_write_budget_200_teams_stress_benchmark(dynamodb_table):
    """Stress test with 200 teams (1200 Pokémon) to verify operational bounds."""
    payload = generate_synthetic_tournament(num_teams=200)
    parsed = parse_tournament_export(payload)

    assert len(parsed.team_rows) == 200
    assert len(parsed.match_rows) == 100

    tracker = DynamoDBCallTracker(dynamodb_table)
    try:
        start_time = time.time()
        success = write_tournament_data(dynamodb_table, parsed)
        elapsed = time.time() - start_time

        assert success is True
        assert elapsed < 30.0  # Safe execution within Lambda timeout

        # At 200 teams, since the meta pool is saturated:
        # Regulation updates remain CONSTANT (150 partners + 15 species + 1 status = 166).
        # Proves partner writes scale with distinct pairs in the meta, NOT team count!
        assert tracker.partner_update_calls == 150
        assert tracker.update_calls == 166
        # Batch writes scale linearly: 1 meta + 200 teams + 100 matches + 60 stats + 150 = 511
        # 511 / 25 = 21 batch calls
        assert tracker.batch_calls == 21
        assert tracker.batch_items_total == 511
    finally:
        tracker.restore()
