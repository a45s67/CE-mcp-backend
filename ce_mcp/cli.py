"""Local smoke-test CLI for the sidecar service boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .service import BackendService, BridgeClient
from .policy import Policy
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ce-mcp-backend")
    parser.add_argument("tool", help="public tool name, e.g. ce.status")
    parser.add_argument(
        "arguments",
        nargs="?",
        default="{}",
        help="tool arguments as a JSON object",
    )
    parser.add_argument(
        "--arguments-file",
        type=Path,
        help="read the JSON arguments object from a local file (development CLI only)",
    )
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--ce-pid", type=int, help="select one CE instance when auto-discovery is ambiguous")
    parser.add_argument("--deadline-ms", type=int, default=5_000)
    parser.add_argument("--policy-config", type=Path)
    parser.add_argument(
        "--contracts",
        type=Path,
        default=Path(__file__).resolve().parent / "contracts" / "v1" / "tools",
    )
    return parser


def run(argv: Sequence[str] | None = None, *, bridge: BridgeClient | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        arguments_text = (
            options.arguments_file.read_text(encoding="utf-8")
            if options.arguments_file is not None else options.arguments
        )
        arguments = json.loads(arguments_text)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": {"code": "INVALID_PARAMS", "message": str(exc)}}))
        return 2
    if not isinstance(arguments, dict):
        print(
            json.dumps(
                {"error": {"code": "INVALID_PARAMS", "message": "arguments must be an object"}}
            )
        )
        return 2
    try:
        if options.ce_pid is not None and options.pipe != DEFAULT_PIPE_NAME:
            raise ValueError("--ce-pid and an explicit --pipe are mutually exclusive")
        actual_bridge = bridge or WindowsNamedPipeBridgeClient(options.pipe, ce_pid=options.ce_pid)
        service = BackendService(
            actual_bridge,
            options.contracts,
            request_deadline_ms=options.deadline_ms,
            policy=Policy.load(options.policy_config),
        )
        outcome = service.call_tool(options.tool, arguments)
        print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
        return 0 if outcome.result is not None else 2
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "CONFIGURATION_ERROR",
                        "message": str(exc),
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
