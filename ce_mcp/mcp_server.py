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
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .mcp_adapter import create_mcp_server
from .audit import JsonlAuditLog
from .artifacts import ArtifactStore
from .service import BackendService
from .policy import Policy
from .server_config import ServerConfig
from .transport import (
    DEFAULT_PIPE_NAME,
    WindowsNamedPipeBridgeClient,
    enumerate_cheat_engine_pids,
)


class StaticTokenVerifier:
    def __init__(self, token: str) -> None:
        if len(token) < 32:
            raise ValueError("Streamable HTTP bearer token must contain at least 32 characters")
        self._token = token

    def matches(self, token: str) -> bool:
        return hmac.compare_digest(token, self._token)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self.matches(token):
            return None
        return AccessToken(
            token=token,
            client_id="dynamic-analysis-gateway",
            scopes=["ce:tools"],
            subject="dynamic-analysis-gateway",
        )


def build_parser(config: ServerConfig | None = None) -> argparse.ArgumentParser:
    config = config or ServerConfig()
    parser = argparse.ArgumentParser(prog="ce-mcp-backend")
    parser.add_argument("--config", type=Path, help="strict JSON server configuration")
    parser.add_argument(
        "--transport", choices=("stdio", "streamable-http"), default=config.transport
    )
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument(
        "--token-file",
        type=Path,
        default=config.token_file,
        help="read the Streamable HTTP bearer token from a local file",
    )
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--ce-pid", type=int, help="select one CE instance when auto-discovery is ambiguous")
    parser.add_argument("--deadline-ms", type=int, default=config.request_deadline_ms)
    parser.add_argument(
        "--exit-when-ce-exits",
        action=argparse.BooleanOptionalAction,
        default=config.exit_when_ce_exits,
        help="stop an HTTP server when its explicitly selected CE PID exits",
    )
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


def parse_options(argv: Sequence[str] | None = None):
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    preliminary, _ = pre_parser.parse_known_args(argv)
    config = ServerConfig.load(preliminary.config)
    return build_parser(config).parse_args(argv)


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
    verifier = StaticTokenVerifier(token)
    url_host = "[::1]" if host == "::1" else host
    issuer = f"http://{url_host}:{port}"

    async def live(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def ready(request: Request) -> JSONResponse:
        authorization = request.headers.get("authorization", "")
        scheme, separator, credential = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not verifier.matches(credential):
            return JSONResponse(
                {"error": {"code": "UNAUTHENTICATED", "message": "authentication required"}},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        outcome = await anyio.to_thread.run_sync(service.call_tool, "ce.status", {})
        if outcome.result is not None:
            return JSONResponse(
                {
                    "status": "ready",
                    "bridge_connected": True,
                    "diagnostic_code": None,
                }
            )
        code = outcome.error.code if outcome.error is not None else "BRIDGE_UNAVAILABLE"
        return JSONResponse(
            {
                "status": "not_ready",
                "bridge_connected": False,
                "diagnostic_code": code,
            },
            status_code=503,
        )

    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=1024 * 1024,
        host=host,
        auth=AuthSettings(
            issuer_url=issuer,
            resource_server_url=f"{issuer}/mcp",
            required_scopes=["ce:tools"],
        ),
        token_verifier=verifier,
        custom_starlette_routes=[
            Route("/health/live", live, methods=["GET"]),
            Route("/health/ready", ready, methods=["GET"]),
        ],
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{host}:{port}", host],
            allowed_origins=[],
        ),
    )


async def _watch_ce_exit(server, ce_pid: int, poll_seconds: float = 0.5) -> None:
    while True:
        await anyio.sleep(poll_seconds)
        if ce_pid not in enumerate_cheat_engine_pids():
            server.should_exit = True
            return


async def _run_http(
    service: BackendService,
    host: str,
    port: int,
    token: str,
    ce_pid: int | None,
    exit_when_ce_exits: bool,
) -> None:
    import uvicorn

    app = create_http_app(service, host, port, token)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
    if not exit_when_ce_exits:
        await server.serve()
        return
    assert ce_pid is not None
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(_watch_ce_exit, server, ce_pid)
        try:
            await server.serve()
        finally:
            tasks.cancel_scope.cancel()


def run_http(
    service: BackendService,
    host: str,
    port: int,
    token: str,
    *,
    ce_pid: int | None = None,
    exit_when_ce_exits: bool = False,
) -> None:
    anyio.run(_run_http, service, host, port, token, ce_pid, exit_when_ce_exits)


def load_http_token(token_file: Path | None) -> str:
    token = os.environ.get("CE_MCP_TOKEN")
    source = "CE_MCP_TOKEN"
    if token is None:
        if token_file is None:
            raise ValueError("CE_MCP_TOKEN or --token-file is required for Streamable HTTP")
        source = "--token-file"
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"cannot read HTTP token file: {exc}") from exc
    if not token:
        raise ValueError(f"{source} must provide a Streamable HTTP bearer token")
    if "\n" in token or "\r" in token:
        raise ValueError("Streamable HTTP bearer token must be a single line")
    return token


def run(argv: Sequence[str] | None = None) -> None:
    options = parse_options(argv)
    if options.exit_when_ce_exits and (
        options.transport != "streamable-http" or options.ce_pid is None
    ):
        raise ValueError(
            "--exit-when-ce-exits requires Streamable HTTP and an explicit --ce-pid"
        )
    service = create_service(options)
    if options.transport == "stdio":
        anyio.run(run_stdio, service)
        return
    token = load_http_token(options.token_file)
    run_http(
        service,
        options.host,
        options.port,
        token,
        ce_pid=options.ce_pid,
        exit_when_ce_exits=options.exit_when_ce_exits,
    )


def main() -> None:
    run()


if __name__ == "__main__":
    main()
