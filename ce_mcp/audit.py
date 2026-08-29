"""Bounded JSONL security audit without request bodies or credentials."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any, Mapping


class AuditLogError(OSError):
    pass


class JsonlAuditLog:
    def __init__(self, root: Path, *, max_bytes: int = 10 * 1024 * 1024, retained_files: int = 5) -> None:
        self.root = root.resolve()
        if max_bytes < 1024 or not 1 <= retained_files <= 20:
            raise ValueError("invalid audit retention limits")
        self.max_bytes, self.retained_files = max_bytes, retained_files
        self._lock = Lock()

    def record(self, event: Mapping[str, Any]) -> None:
        # The caller supplies metadata only. This class intentionally has no API
        # for tool arguments, payload bytes, HTTP credentials, or bridge tokens.
        value = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **dict(event),
        }
        encoded = (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > 4096:
            raise AuditLogError("audit event exceeds 4096 bytes")
        try:
            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root / "audit.jsonl"
                if path.exists() and path.stat().st_size + len(encoded) > self.max_bytes:
                    oldest = self.root / f"audit.{self.retained_files - 1}.jsonl"
                    oldest.unlink(missing_ok=True)
                    for index in range(self.retained_files - 1, 0, -1):
                        source = self.root / ("audit.jsonl" if index == 1 else f"audit.{index - 1}.jsonl")
                        destination = self.root / f"audit.{index}.jsonl"
                        if source.exists():
                            os.replace(source, destination)
                with path.open("ab") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as exc:
            raise AuditLogError(f"audit write failed: {exc}") from exc
