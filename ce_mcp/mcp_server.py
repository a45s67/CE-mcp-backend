"""Production entry point for stdio or localhost Streamable HTTP MCP."""

from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
from typing import Sequence

import anyio
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.stdio import stdio_server
from mcp.server.transport_security import TransportSecuritySettings

from .mcp_adapter import create_mcp_server
from .audit import JsonlAuditLog
from .artifacts import ArtifactStore
from .service import BackendService
from .policy import Policy
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


class StaticTokenVerifier:
    def __init__(self, token: str) -> None:
        if len(token) < 32:
            raise ValueError("CE_MCP_TOKEN must contain at least 32 characters")
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id="dynamic-analysis-gateway",
            scopes=["ce:tools"],
            subject="dynamic-analysis-gateway",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ce-mcp-backend")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--ce-pid", type=int, help="select one CE instance when auto-discovery is ambiguous")
    parser.add_argument("--deadline-ms", type=int, default=5_000)
    parser.add_argument(
        "--policy-config", type=Path,
        help="local JSON capability policy; defaults to the debug profile",
    )
    parser.add_argument(
        "--artifact-root", type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "CE-MCP" / "artifacts",
    )
    parser.add_argument("--max-artifacts", type=int, default=128)
    parser.add_argument("--artifact-retention-seconds", type=int, default=7 * 24 * 60 * 60)
    parser.add_argument(
        "--audit-root", type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "CE-MCP" / "audit",
    )
    parser.add_argument(
        "--contracts",
        type=Path,
        default=Path(__file__).resolve().parent / "contracts" / "v1" / "tools",
    )
    return parser


def create_service(options) -> BackendService:
    if options.ce_pid is not None and options.pipe != DEFAULT_PIPE_NAME:
        raise ValueError("--ce-pid and an explicit --pipe are mutually exclusive")
    return BackendService(
        WindowsNamedPipeBridgeClient(options.pipe, ce_pid=options.ce_pid),
        options.contracts,
        request_deadline_ms=options.deadline_ms,
        artifact_store=ArtifactStore(
            options.artifact_root,
            max_artifacts=options.max_artifacts,
            retention_seconds=options.artifact_retention_seconds,
        ),
        policy=Policy.load(options.policy_config),
        audit_sink=JsonlAuditLog(options.audit_root),
    )


async def run_stdio(service: BackendService) -> None:
    server = create_mcp_server(service)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def create_http_app(service: BackendService, host: str, port: int, token: str):
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("CE backend HTTP must bind to localhost")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    server = create_mcp_server(service)
    url_host = "[::1]" if host == "::1" else host
    issuer = f"http://{url_host}:{port}"
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        max_request_body_size=1024 * 1024,
        host=host,
        auth=AuthSettings(
            issuer_url=issuer,
            resource_server_url=f"{issuer}/mcp",
            required_scopes=["ce:tools"],
        ),
        token_verifier=StaticTokenVerifier(token),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{host}:{port}", host],
            allowed_origins=[],
        ),
    )


def run_http(service: BackendService, host: str, port: int, token: str) -> None:
    import uvicorn

    app = create_http_app(service, host, port, token)
    uvicorn.run(app, host=host, port=port, log_level="info")


def run(argv: Sequence[str] | None = None) -> None:
    options = build_parser().parse_args(argv)
    service = create_service(options)
    if options.transport == "stdio":
        anyio.run(run_stdio, service)
        return
    token = os.environ.get("CE_MCP_TOKEN", "")
    if not token:
        raise ValueError("CE_MCP_TOKEN is required for Streamable HTTP")
    run_http(service, options.host, options.port, token)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
