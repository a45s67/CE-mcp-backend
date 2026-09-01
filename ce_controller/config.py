from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping


MAX_CONFIG_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 4096


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ControllerConfig:
    root: Path
    host: str
    port: int
    token: str

    @property
    def base_url(self) -> str:
        address = "[::1]" if self.host == "::1" else self.host
        return f"http://{address}:{self.port}"

    @classmethod
    def load(cls, root: Path, environment: Mapping[str, str] | None = None) -> "ControllerConfig":
        root = root.resolve(strict=True)
        path = root / "mcp" / "config.json"
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ConfigurationError("installed MCP configuration is unavailable") from exc
        if not raw or len(raw) > MAX_CONFIG_BYTES or b"\0" in raw:
            raise ConfigurationError("installed MCP configuration is empty or oversized")
        try:
            value: Any = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError("installed MCP configuration is malformed") from exc
        if not isinstance(value, Mapping):
            raise ConfigurationError("installed MCP configuration must be an object")
        allowed = {
            "transport", "host", "port", "tokenFile", "requestDeadlineMs",
            "exitWhenCeExits", "maxOutputBytes",
        }
        if set(value).difference(allowed):
            raise ConfigurationError("installed MCP configuration has unknown fields")
        if value.get("transport") != "streamable-http":
            raise ConfigurationError("controller requires Streamable HTTP transport")
        host = value.get("host")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ConfigurationError("controller requires a loopback MCP host")
        port = value.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ConfigurationError("installed MCP port is invalid")
        token_value = value.get("tokenFile")
        if not isinstance(token_value, str) or not token_value or len(token_value) > 1024:
            raise ConfigurationError("installed tokenFile is invalid")
        token_path = Path(token_value)
        if not token_path.is_absolute():
            token_path = path.parent / token_path
        env = os.environ if environment is None else environment
        token = env.get("CE_MCP_TOKEN")
        if token is None:
            try:
                token_bytes = token_path.read_bytes()
            except OSError as exc:
                raise ConfigurationError("installed MCP token is unavailable") from exc
            if len(token_bytes) > MAX_TOKEN_BYTES:
                raise ConfigurationError("installed MCP token is oversized")
            try:
                token = token_bytes.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise ConfigurationError("installed MCP token is malformed") from exc
        token_size = len(token.encode("utf-8"))
        if not 32 <= token_size <= MAX_TOKEN_BYTES or "\r" in token or "\n" in token:
            raise ConfigurationError("installed MCP token is invalid")
        return cls(root=root, host=host, port=port, token=token)

