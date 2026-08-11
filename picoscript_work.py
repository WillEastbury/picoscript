"""Caller-owned bounded tracing and resumable work budgets."""

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class TraceEvent:
    kind: int
    sequence: int
    token: int = 0
    node: int = 0
    edge: int = 0
    amplitude: int = 0
    status: int = 0
    payload: bytes = b""


class TraceSink:
    def __init__(self, capacity):
        self.capacity = max(0, capacity)
        self.events = []
        self.dropped = 0
        self._read = 0

    def append(self, event):
        if len(self.events) >= self.capacity:
            self.dropped += 1
            return False
        self.events.append(event)
        return True

    def next(self):
        if self._read >= len(self.events):
            return None
        event = self.events[self._read]
        self._read += 1
        return event

    def clear(self):
        self.events.clear(); self._read = 0; self.dropped = 0

    def digest(self):
        body = [e.__dict__ | {"payload": e.payload.hex()} for e in self.events]
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class WorkBudget:
    steps: int = 0
    records: int = 0
    bytes: int = 0
    cancelled: bool = False

    def consume(self, steps=1, records=0, bytes=0):
        if self.cancelled or min(self.steps - steps, self.records - records, self.bytes - bytes) < 0:
            return False
        self.steps -= steps; self.records -= records; self.bytes -= bytes
        return True

    def cancel(self):
        self.cancelled = True
