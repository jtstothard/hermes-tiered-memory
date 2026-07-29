"""PROTOTYPE ONLY: fake Hermes memory boundary; never deploy."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Optional


class DuplicateToolError(RuntimeError):
    pass


@dataclass
class FakeMnemosyne:
    tools: list[str] = field(default_factory=list)
    data: dict[tuple[str, str, str], str] = field(default_factory=dict)
    events: list[tuple] = field(default_factory=list)

    def __post_init__(self):
        self.exposed_tools = list(self.tools)

    def read(self, profile: str, bank: str, key: str) -> Optional[str]:
        self.events.append(("read", profile, bank, key))
        return self.data.get((profile, bank, key))

    def write(self, profile: str, bank: str, key: str, value: str) -> None:
        self.data[(profile, bank, key)] = value
        self.events.append(("write", profile, bank, key, value))

    def keys(self):
        return self.data.keys()


@dataclass
class FakeHindsight:
    fail: bool = False
    data: dict[tuple[str, str, str], str] = field(default_factory=dict)
    events: list[tuple] = field(default_factory=list)

    def __post_init__(self):
        self.exposed_tools: list[str] = []

    def seed(self, profile: str, bank: str, key: str, value: str) -> None:
        self.data[(profile, bank, key)] = value

    def search(self, profile: str, bank: str, key: str) -> Optional[str]:
        self.events.append(("search", profile, bank, key))
        return self.data.get((profile, bank, key))

    def retain(self, profile: str, bank: str, key: str, value: str) -> None:
        if self.fail:
            self.events.append(("retain_failed", profile, bank, key, value))
            raise TimeoutError("fake Hindsight timeout")
        self.data[(profile, bank, key)] = value
        self.events.append(("retain", profile, bank, key, value))


class HermesMemoryLoader:
    """Minimal stand-in for the one configured Hermes provider slot."""

    def __init__(self):
        self.provider_names: list[str] = []
        self.tools: list[str] = []

    def load(self, provider) -> None:
        new_tools = list(provider.tools)
        duplicate = set(self.tools).intersection(new_tools)
        if duplicate:
            raise DuplicateToolError(f"duplicate memory tools: {sorted(duplicate)}")
        self.provider_names.append(provider.name)
        self.tools.extend(new_tools)


class CompositeMemoryProvider:
    """Only Hermes-facing object; lower layers remain non-tool stores."""

    name = "mnemosyne+hindsight"
    tools = ["memory_search", "memory_store"]

    def __init__(self, l1: FakeMnemosyne, l2: FakeHindsight):
        self.l1 = l1
        self.l2 = l2
        self.failures: list[dict] = []
        self.trace: list[tuple] = []
        self._jobs: queue.Queue[tuple[str, str, str, str] | None] = queue.Queue()
        self._worker = threading.Thread(target=self._retain_worker, daemon=True)
        self._worker.start()

    def search(self, profile: str, bank: str, key: str) -> Optional[str]:
        value = self.l1.read(profile, bank, key)
        if value is not None:
            return value
        return self.l2.search(profile, bank, key)

    def store(self, profile: str, bank: str, key: str, value: str) -> dict[str, str]:
        # The return boundary is deliberately after L1 write, before L2 completion.
        self.l1.write(profile, bank, key, value)
        self.trace.append(("l1_write_complete", profile, bank, key))
        self._jobs.put((profile, bank, key, value))
        self.trace.append(("l2_queued", profile, bank, key))
        return {"l1": "written", "l2": "queued"}

    def _retain_worker(self):
        while True:
            job = self._jobs.get()
            try:
                if job is None:
                    return
                self.l2.retain(*job)
            except Exception as exc:  # async failures must not affect the sync result
                profile, bank, key, _ = job
                self.failures.append({
                    "kind": "l2_retain", "profile": profile, "bank": bank,
                    "key": key, "error": type(exc).__name__,
                })
            finally:
                self._jobs.task_done()

    def wait_for_l2(self, timeout: float) -> bool:
        done = threading.Event()

        def waiter():
            self._jobs.join()
            done.set()

        threading.Thread(target=waiter, daemon=True).start()
        return done.wait(timeout)
