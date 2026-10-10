"""Revision-keyed cache; refresh failure preserves the last verified current revision."""

from collections import OrderedDict

from db.catalog import PENDING_KEY
from db.publication import get
from db.schema_v2 import native


class CatalogRuntime:
    def __init__(self, store):
        self.store = store
        self.cache = OrderedDict()
        self.current = None
        self.current_candidate = None
        self.candidates = OrderedDict()
        self.stale = False

    def revision(self, revision):
        if revision not in self.cache:
            snapshot = self.store.load_revision(revision)
            if snapshot.verification_status != "verified" or not snapshot.coverage_complete:
                raise ValueError("Runtime requires verified complete catalog")
            self.cache[revision] = snapshot
            if len(self.cache) > 16:
                self.cache.popitem(last=False)
        self.cache.move_to_end(revision)
        return self.cache[revision].model_copy(deep=True)

    def refresh(self):
        try:
            state = self.store.state()
            # Read on every refresh to detect evidence/storage failures, even for a cached ID.
            snapshot = self.store.load_revision(state["active_revision"])
            candidate = self.store.load_candidate(state["candidate_id"])
            self.cache[snapshot.snapshot_id] = snapshot
            if len(self.cache) > 16:
                self.cache.popitem(last=False)
            self.current, self.stale = snapshot, False
            self.current_candidate = candidate
        except Exception:
            self.stale = True
            if self.current is None:
                raise
        return self.current.model_copy(deep=True)

    def candidate(self, candidate_id):
        if self.current_candidate and self.current_candidate.candidate_id == candidate_id:
            return self.current_candidate.model_copy(deep=True)
        if candidate_id not in self.candidates:
            self.candidates[candidate_id] = self.store.load_candidate(candidate_id)
            if len(self.candidates) > 16:
                self.candidates.popitem(last=False)
        self.candidates.move_to_end(candidate_id)
        return self.candidates[candidate_id].model_copy(deep=True)

    def status(self):
        pending = native(get(self.store.table, PENDING_KEY) or {})
        return {
            "active_revision": self.current.snapshot_id if self.current else None,
            "pending_revision": pending.get("pending_revision"),
            "stale": self.stale,
        }
