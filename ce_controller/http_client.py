from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import ControllerConfig


MAX_RESPONSE_BYTES = 64 * 1024


class ObservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpObservation:
    ready: bool
    session_present: bool | None
    backend_version: str | None = None


class McpObserver:
    def __init__(self, config: ControllerConfig, timeout_seconds: float) -> None:
        self._config = config
        self._deadline = time.monotonic() + max(0.05, timeout_seconds)

    def _json_request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self._config.token}"}
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers.update({
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-06-18",
            })
        request = Request(self._config.base_url + path, data=data, headers=headers)
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationError("authenticated MCP observation timed out")
        try:
            with urlopen(request, timeout=remaining) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise ObservationError("authenticated MCP endpoint is unavailable") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ObservationError("authenticated MCP response is oversized")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ObservationError("authenticated MCP response is malformed") from exc

    def observe(self) -> McpObservation:
        ready = self._json_request("/health/ready")
        if not isinstance(ready, dict) or ready.get("status") != "ready" or ready.get("bridge_connected") is not True:
            raise ObservationError("authenticated MCP readiness is unavailable")
        initialized = self._json_request("/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "ce-mcp-control", "version": "1"},
            },
        })
        try:
            server_name = initialized["result"]["serverInfo"]["name"]
        except (KeyError, TypeError) as exc:
            raise ObservationError("MCP initialization response is invalid") from exc
        if server_name != "ce-mcp-backend":
            raise ObservationError("MCP endpoint identity does not match CE MCP")
        called = self._json_request("/mcp", {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "ce.status", "arguments": {}},
        })
        try:
            result = called["result"]
            if result.get("isError") is True:
                raise ObservationError("ce.status returned an error")
            status = result["structuredContent"]
            backend_version = status["backend"]["version"]
            connected = status["bridge"]["connected"]
        except (KeyError, TypeError) as exc:
            raise ObservationError("ce.status response is invalid") from exc
        if connected is not True or not isinstance(backend_version, str) or len(backend_version) > 32:
            raise ObservationError("ce.status identity is invalid")
        return McpObservation(True, "session" in status, backend_version)
