"""Offline scripted verification for a compiled CE MCP server executable."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def request_json(url: str, *, token: str | None = None, payload=None):
    headers = {}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            }
        )
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


async def verify_stdio(server: Path, temporary: Path) -> None:
    parameters = StdioServerParameters(
        command=str(server),
        args=[
            "--transport", "stdio", "--deadline-ms", "50",
            "--audit-root", str(temporary / "stdio-audit"),
            "--artifact-root", str(temporary / "stdio-artifacts"),
            "--pipe", r"\\.\pipe\CE_MCP_compiled_test_intentionally_absent",
        ],
        cwd=server.parent,
        env=dict(os.environ),
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "ce-mcp-backend"
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            assert names == sorted(names) and "ce.status" in names
            status = await session.call_tool("ce.status", {})
            assert status.is_error
            assert status.structured_content["error"]["code"] == "BRIDGE_UNAVAILABLE"
            assert status.content[0].text.startswith("ce.status failed: BRIDGE_UNAVAILABLE;")
            assert not status.content[0].text.lstrip().startswith("{")


def verify_http_auth_source(
    server: Path,
    temporary: Path,
    *,
    label: str,
    environment_token: str | None,
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    file_token = ("f" if environment_token is not None else "t") * 48
    active_token = environment_token or file_token
    token_file = temporary / f"{label}.token"
    token_file.write_text(file_token, encoding="utf-8")
    config = temporary / f"{label}.json"
    config.write_text(
        json.dumps(
            {
                "transport": "streamable-http",
                "host": "127.0.0.1",
                "port": port,
                "tokenFile": token_file.name,
                "requestDeadlineMs": 50,
                "maxOutputBytes": 1048576,
                "exitWhenCeExits": False,
            }
        ),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    if environment_token is not None:
        environment["CE_MCP_TOKEN"] = environment_token
    else:
        environment.pop("CE_MCP_TOKEN", None)
    process = subprocess.Popen(
        [
            str(server), "--config", str(config),
            "--audit-root", str(temporary / f"{label}-audit"),
            "--artifact-root", str(temporary / f"{label}-artifacts"),
            "--pipe", r"\\.\pipe\CE_MCP_compiled_test_intentionally_absent",
        ],
        cwd=server.parent,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"compiled HTTP server exited with {process.returncode}")
            try:
                status, body = request_json(base + "/health/live")
                if status == 200 and body == {"status": "ok"}:
                    break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("compiled HTTP server did not become live")
            time.sleep(0.1)
        if environment_token is not None:
            assert request_json(base + "/health/ready", token=file_token)[0] == 401
        ready_status, ready = request_json(base + "/health/ready", token=active_token)
        assert ready_status == 503 and ready["diagnostic_code"] == "BRIDGE_UNAVAILABLE"
        initialize_status, initialized = request_json(
            base + "/mcp",
            token=active_token,
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "compiled-gate", "version": "1"},
                },
            },
        )
        assert initialize_status == 200
        assert initialized["result"]["serverInfo"]["name"] == "ce-mcp-backend"
        list_status, listed = request_json(
            base + "/mcp",
            token=active_token,
            payload={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert list_status == 200
        assert "ce.status" in {tool["name"] for tool in listed["result"]["tools"]}
        call_status, called = request_json(
            base + "/mcp",
            token=active_token,
            payload={
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "ce.status", "arguments": {}},
            },
        )
        assert call_status == 200
        result = called["result"]
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "BRIDGE_UNAVAILABLE"
        assert result["content"][0]["text"].startswith(
            "ce.status failed: BRIDGE_UNAVAILABLE;"
        )
        assert len(json.dumps(called).encode("utf-8")) < 1048576
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=Path, required=True)
    options = parser.parse_args()
    server = options.server.resolve()
    if not server.is_file():
        raise SystemExit(f"compiled server does not exist: {server}")
    help_result = subprocess.run(
        [str(server), "--help"], cwd=server.parent, capture_output=True, text=True, timeout=15
    )
    assert help_result.returncode == 0 and "streamable-http" in help_result.stdout
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        anyio.run(verify_stdio, server, temporary)
        verify_http_auth_source(
            server, temporary, label="environment", environment_token="e" * 48
        )
        verify_http_auth_source(
            server, temporary, label="token-file", environment_token=None
        )
    print("compiled server verification passed")


if __name__ == "__main__":
    main()
