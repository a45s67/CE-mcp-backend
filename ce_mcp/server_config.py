"""Strict JSON configuration for the production MCP server."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ServerConfig:
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8001
    token_file: Path | None = None
    request_deadline_ms: int = 5_000
    exit_when_ce_exits: bool = False

    @classmethod
    def load(cls, path: Path | None) -> "ServerConfig":
        if path is None:
            return cls()
        try:
            value: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load server config {path}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ValueError("server config must be a JSON object")
        allowed = {
            "transport", "host", "port", "tokenFile", "requestDeadlineMs",
            "exitWhenCeExits",
        }
        extra = set(value).difference(allowed)
        if extra:
            raise ValueError(f"unknown server config fields: {sorted(extra)}")
        transport = value.get("transport", "stdio")
        host = value.get("host", "127.0.0.1")
        port = value.get("port", 8001)
        deadline = value.get("requestDeadlineMs", 5_000)
        exit_when_ce_exits = value.get("exitWhenCeExits", False)
        token_value = value.get("tokenFile")
        if transport not in {"stdio", "streamable-http"}:
            raise ValueError("transport must be stdio or streamable-http")
        if not isinstance(host, str):
            raise ValueError("host must be a string")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if (
            not isinstance(deadline, int) or isinstance(deadline, bool)
            or not 1 <= deadline <= 300_000
        ):
            raise ValueError("requestDeadlineMs must be between 1 and 300000")
        if not isinstance(exit_when_ce_exits, bool):
            raise ValueError("exitWhenCeExits must be a boolean")
        if token_value is not None and (not isinstance(token_value, str) or not token_value):
            raise ValueError("tokenFile must be a non-empty string")
        token_file = None
        if token_value is not None:
            token_file = Path(token_value)
            if not token_file.is_absolute():
                token_file = path.resolve().parent / token_file
        return cls(
            transport=transport,
            host=host,
            port=port,
            token_file=token_file,
            request_deadline_ms=deadline,
            exit_when_ce_exits=exit_when_ce_exits,
        )
