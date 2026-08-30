"""Durable sinks for cryptographically chained audit records."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional


class AuditPersistenceError(RuntimeError):
    """Raised when an explicitly durable audit record cannot be persisted."""


class JSONLFileAuditSink:
    """Append-only JSONL sink with optional fsync acknowledgement.

    This provides durable local evidence, not storage-level WORM semantics. A
    WORM claim additionally requires an immutable retention mechanism such as
    object lock on the system receiving this file.
    """

    def __init__(self, path: str, *, fsync: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self._lock = threading.Lock()

    def append(self, serialized_record: str) -> None:
        if "\n" in serialized_record or "\r" in serialized_record:
            raise AuditPersistenceError("Audit records must be serialized as a single JSONL line")
        try:
            json.loads(serialized_record)
        except json.JSONDecodeError as exc:
            raise AuditPersistenceError("Refusing to persist invalid audit JSON") from exc

        try:
            with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized_record)
                stream.write("\n")
                stream.flush()
                if self.fsync:
                    os.fsync(stream.fileno())
        except OSError as exc:
            raise AuditPersistenceError(f"Unable to persist audit record to {self.path}: {exc}") from exc

    def last_record(self) -> Optional[dict[str, Any]]:
        """Return the last valid record for chain recovery, if any."""
        if not self.path.exists():
            return None
        last: Optional[dict[str, Any]] = None
        try:
            with self._lock, self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        last = json.loads(line)
        except (OSError, json.JSONDecodeError) as exc:
            raise AuditPersistenceError(f"Unable to recover audit chain from {self.path}: {exc}") from exc
        return last
