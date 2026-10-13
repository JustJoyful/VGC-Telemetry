import base64
import json

import pytest

SECRET = b"test-only-signing-secret-at-least-32-bytes"


def test_snapshot_and_cursor_bind_query_limit_endpoint_and_fixed_expiry():
    from app.v2.cursors import decode_cursor, decode_snapshot, encode_cursor, encode_snapshot
    from app.v2.errors import V2Error

    token = encode_snapshot("snapshot-a", expires_at=86500, secret=SECRET)
    snapshot = decode_snapshot(token, now=200, secret=SECRET)
    cursor = encode_cursor(
        snapshot=snapshot,
        endpoint="/v2/teams",
        query={"eligibility": "included"},
        limit=1,
        position="limitless:e:p",
        secret=SECRET,
    )
    value = decode_cursor(
        cursor,
        endpoint="/v2/teams",
        query={"eligibility": "included"},
        limit=1,
        now=300,
        secret=SECRET,
    )
    assert value["exp"] == 86500
    assert value["position"] == "limitless:e:p"
    assert value["snapshot_id"] == "snapshot-a"
    payload = json.loads(base64.urlsafe_b64decode(cursor.split(".")[0] + "=="))
    assert not {"PK", "SK", "lease", "ledger"} & set(payload)
    for changes in [
        {"query": {}},
        {"limit": 2},
        {"endpoint": "/v2/meta"},
        {"secret": b"other-secret" * 4},
    ]:
        args = dict(
            endpoint="/v2/teams", query={"eligibility": "included"}, limit=1, now=300, secret=SECRET
        )
        args.update(changes)
        with pytest.raises(V2Error) as failure:
            decode_cursor(cursor, **args)
        assert failure.value.code == "invalid_cursor"
    with pytest.raises(V2Error) as failure:
        decode_snapshot(token, now=86500, secret=SECRET)
    assert failure.value.status == 410


@pytest.mark.parametrize(
    "token",
    ["", "x", "a.b", "e30=.abcd", "a" * 4097, "🦆" * 1025],
    ids=["empty", "truncated", "invalid", "padded", "oversized", "unicode-oversized"],
)
def test_malformed_or_oversized_tokens_fail_safely(token):
    from app.v2.cursors import decode_snapshot
    from app.v2.errors import V2Error

    with pytest.raises(V2Error) as failure:
        decode_snapshot(token, now=100, secret=SECRET)
    assert failure.value.status == 400


def test_signature_comparison_and_secret_minimum(monkeypatch):
    import hmac

    from app.v2.config import V2ReadConfig
    from app.v2.cursors import decode_snapshot, encode_snapshot
    from app.v2.errors import V2Error

    original = hmac.compare_digest
    seen = []

    def compare(a, b):
        seen.append((type(a), type(b)))
        return original(a, b)

    monkeypatch.setattr(hmac, "compare_digest", compare)
    token = encode_snapshot("s", expires_at=1000, secret=SECRET)
    assert decode_snapshot(token, now=100, secret=SECRET).snapshot_id == "s"
    assert seen == [(bytes, bytes)]
    with pytest.raises(ValueError):
        V2ReadConfig(secret=b"short")
    with pytest.raises(V2Error):
        decode_snapshot(token[:-1] + ("a" if token[-1] != "a" else "b"), now=100, secret=SECRET)


def test_member_cursor_pins_catalog_and_versioned_candidate_plan():
    from app.db.public_queries import PublicReader
    from app.models.v2 import SnapshotInfo
    from app.models.v2_queries import TeamQuery, canonical_query
    from app.v2.config import V2ReadConfig
    from app.v2.cursors import encode_cursor
    from app.v2.errors import V2Error

    query = TeamQuery(members=["species:rillaboom"], catalog_revision="champions-mc-3661ce4")
    cursor = encode_cursor(
        snapshot=SnapshotInfo(snapshot_id="retained", expires_at=1000),
        endpoint="/v2/teams",
        query=canonical_query(query),
        limit=25,
        position="entry",
        secret=SECRET,
        catalog_revision="champions-mc-3661ce4",
    )
    reader = PublicReader(
        None,
        config=V2ReadConfig(
            secret=SECRET, clock=lambda: 100, default_catalog_revision="champions-mb-3661ce4"
        ),
    )
    reader.resolve_snapshot = lambda identifier: SnapshotInfo(
        snapshot_id=identifier, expires_at=1000
    )
    resumed = TeamQuery(members=["species:rillaboom"], cursor=cursor)
    snap, position = reader.request_snapshot(resumed, "/v2/teams")
    assert snap.snapshot_id == "retained" and position == "entry"
    assert resumed.catalog_revision == "champions-mc-3661ce4"
    assert canonical_query(resumed)["candidate_plan"] == "teams-v1"
    with pytest.raises(V2Error) as error:
        reader.request_snapshot(
            TeamQuery(
                members=["species:rillaboom"],
                catalog_revision="champions-mb-3661ce4",
                cursor=cursor,
            ),
            "/v2/teams",
        )
    assert error.value.code == "invalid_cursor"
