"""THROWAWAY acceptance harness; stdlib fakes only, never deploy.

Models the locked Mnemosyne L1 -> Hindsight L2 contract with deterministic,
file-backed outbox snapshots.  It is intentionally not a live adapter.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


TOOLS = ["memory_search", "memory_store", "memory_delete"]


def identity(profile: str, bank: str, source_id: str) -> str:
    return f"{profile}/{bank}/{source_id}"


def idem(op: str, origin: str, revision: int) -> str:
    raw = f"{op}:{origin}:{revision}".encode()
    return hashlib.sha256(raw).hexdigest()[:20]


@dataclass
class Record:
    profile: str
    bank: str
    source_id: str
    payload: str
    revision: int = 1
    tombstone: bool = False
    consolidated: bool = False
    metadata: dict[str, Any] | None = None

    @property
    def origin(self) -> str:
        return identity(self.profile, self.bank, self.source_id)


class FakeL1:
    def __init__(self):
        self.data: dict[str, Record] = {}
        self.events: list[tuple] = []

    def get(self, profile, bank, source_id):
        key = identity(profile, bank, source_id)
        self.events.append(("read", key))
        return self.data.get(key)

    def put(self, record: Record):
        self.data[record.origin] = record
        self.events.append(("write", record.origin, record.revision, record.tombstone))

    def delete(self, profile, bank, source_id):
        prior = self.get(profile, bank, source_id)
        revision = (prior.revision + 1) if prior else 1
        record = Record(profile, bank, source_id, "", revision, True,
                        metadata={"deleted": True, "origin": identity(profile, bank, source_id)})
        self.put(record)
        return record

    def eligible(self):
        return [r for r in self.data.values() if r.consolidated]


class L2Unavailable(RuntimeError):
    pass


class FakeL2:
    """Durable-ish fake keyed by origin identity; no Hermes tools exposed."""
    def __init__(self):
        self.data: dict[str, Record] = {}
        self.events: list[tuple] = []
        self.available = True
        self.failures_before_success = 0
        self.reflect_called = False

    def search(self, profile, bank, source_id):
        if not self.available:
            self.events.append(("search_failed", identity(profile, bank, source_id)))
            raise L2Unavailable("simulated outage")
        origin = identity(profile, bank, source_id)
        self.events.append(("search", origin))
        rec = self.data.get(origin)
        return None if rec is None or rec.tombstone else rec

    def retain(self, record: Record):
        if not self.available:
            self.events.append(("retain_failed", record.origin, record.revision))
            raise L2Unavailable("simulated outage")
        if self.failures_before_success:
            self.failures_before_success -= 1
            self.events.append(("retain_failed", record.origin, record.revision))
            raise TimeoutError("simulated transient timeout")
        old = self.data.get(record.origin)
        if old and old.revision > record.revision:
            self.events.append(("retain_noop_older", record.origin, record.revision))
            return
        if old and old.revision == record.revision and old.payload == record.payload and old.tombstone == record.tombstone:
            self.events.append(("retain_noop_duplicate", record.origin, record.revision))
            return
        if old and old.revision < record.revision:
            self.events.append(("tombstone", record.origin, old.revision))
        self.data[record.origin] = record
        self.events.append(("retain", record.origin, record.revision, record.tombstone))


class DurableOutbox:
    def __init__(self, path: str | Path, max_attempts: int = 3):
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.entries: list[dict[str, Any]] = []
        if self.path.exists():
            self.entries = json.loads(self.path.read_text())

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, sort_keys=True) + "\n")

    def enqueue(self, record: Record, operation: str = "retain"):
        key = idem(operation, record.origin, record.revision)
        if any(e["idempotency_key"] == key for e in self.entries):
            return key
        self.entries.append({"idempotency_key": key, "operation": operation,
                             "record": asdict(record), "attempts": 0, "status": "pending"})
        self._save()  # enqueue is durable before dispatch
        return key

    def pending(self):
        return [e for e in self.entries if e["status"] == "pending"]

    def drain(self, l2: FakeL2):
        for entry in self.pending():
            try:
                l2.retain(Record(**entry["record"]))
            except Exception as exc:
                entry["attempts"] += 1
                entry["last_error"] = type(exc).__name__
                if entry["attempts"] >= self.max_attempts:
                    entry["status"] = "quarantined"
            else:
                entry["status"] = "complete"
            self._save()

    def replay_quarantine(self):
        for entry in self.entries:
            if entry["status"] == "quarantined":
                entry["status"] = "pending"
                entry["attempts"] = 0
        self._save()

    def replay_completed(self):
        """Explicit operator/reconciliation replay, including completed work."""
        for entry in self.entries:
            if entry["status"] == "complete":
                entry["status"] = "pending"
        self._save()


class CompositeProvider:
    name = "mnemosyne+hindsight"
    tools = TOOLS

    def __init__(self, l1: FakeL1, l2: FakeL2, outbox: DurableOutbox):
        self.l1, self.l2, self.outbox = l1, l2, outbox
        self.trace: list[tuple] = []
        self.failures: list[str] = []

    def _metadata(self, record, **extra):
        base = {"origin": record.origin, "profile": record.profile, "bank": record.bank,
                "mnemosyne_id": record.source_id, "revision": record.revision,
                "source": "mnemosyne", "fact_type": "episodic_summary",
                "confidence": 0.8, "importance": 0.5, "consolidated": record.consolidated}
        base.update(extra)
        return base

    def search(self, profile, bank, source_id):
        rec = self.l1.get(profile, bank, source_id)
        stale = bool(rec and rec.metadata and rec.metadata.get("stale"))
        if rec and not rec.tombstone and not stale:
            self.trace.append(("l1_hit", rec.origin))
            return {"payload": rec.payload, "stale": False, "source": "l1", "metadata": rec.metadata}
        try:
            remote = self.l2.search(profile, bank, source_id)
        except L2Unavailable:
            if rec and not rec.tombstone:
                self.outbox.enqueue(rec, "refresh")
                self.trace.append(("stale_while_revalidate", rec.origin))
                return {"payload": rec.payload, "stale": True, "source": "l1", "metadata": rec.metadata}
            self.trace.append(("miss_unavailable", identity(profile, bank, source_id)))
            return None
        if remote:
            backfill = Record(**asdict(remote))
            backfill.metadata = dict(remote.metadata or {})
            self.l1.put(backfill)
            self.trace.append(("l2_backfill", remote.origin))
            return {"payload": remote.payload, "stale": False, "source": "l2", "metadata": remote.metadata}
        return None

    def store(self, profile, bank, source_id, payload, *, consolidated=False, **meta):
        prior = self.l1.get(profile, bank, source_id)
        revision = prior.revision + 1 if prior else 1
        rec = Record(profile, bank, source_id, payload, revision, False, consolidated)
        rec.metadata = self._metadata(rec, **meta)
        self.l1.put(rec)
        self.trace.append(("l1_write_complete", rec.origin, revision))
        self.outbox.enqueue(rec)
        self.trace.append(("durable_outbox_enqueued", rec.origin, revision))
        return {"l1": "written", "l2": "queued", "origin": rec.origin, "revision": revision}

    def delete(self, profile, bank, source_id):
        rec = self.l1.delete(profile, bank, source_id)
        self.outbox.enqueue(rec, "tombstone")
        self.trace.append(("tombstone_enqueued", rec.origin, rec.revision))

    def consolidate(self, profile, bank, source_ids):
        for source_id in source_ids:
            rec = self.l1.get(profile, bank, source_id)
            if rec and not rec.tombstone:
                rec.consolidated = True
                rec.metadata = self._metadata(rec, consolidated=True, trigger="consolidation")
                self.l1.put(rec)
                self.outbox.enqueue(rec, "retain")
                self.trace.append(("promotion_triggered", rec.origin, rec.revision))

    def reconcile(self):
        for rec in self.l1.eligible():
            self.outbox.enqueue(rec, "retain")
        self.trace.append(("reconciliation", len(self.l1.eligible())))

    def drain(self):
        self.outbox.drain(self.l2)


class HermesLoader:
    def __init__(self):
        self.providers: list[str] = []
        self.tools: list[str] = []

    def load(self, provider):
        duplicates = set(self.tools) & set(provider.tools)
        if duplicates:
            raise RuntimeError(f"duplicate namespace: {sorted(duplicates)}")
        self.providers.append(provider.name)
        self.tools.extend(provider.tools)
