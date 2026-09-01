from __future__ import annotations

import os
from pathlib import Path
import sys

from .config import ConfigurationError, ControllerConfig
from .core import ControlFailure, Controller, Options, encode_result
from .http_client import McpObserver
from .platform import PlatformError, WindowsPlatform


def _invalid(message: str) -> ControlFailure:
    return ControlFailure(2, "INVALID_ARGUMENT", message, recoverable=False, safe_to_retry=False)


def parse_options(argv: list[str], executable_path: Path | None = None) -> Options:
    if not 1 <= len(argv) <= 8:
        raise _invalid("expected one action and bounded options")
    action = argv[0]
    if action not in {"status", "start", "stop", "restart"}:
        raise _invalid("action must be status, start, stop, or restart")
    root_value: str | None = None
    executable: str | None = None
    timeout_ms = 20_000
    timeout_seen = False
    force = False
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag == "--force":
            if force:
                raise _invalid("--force must not be repeated")
            force = True
            index += 1
            continue
        if index + 1 >= len(argv):
            raise _invalid("option requires a value")
        value = argv[index + 1]
        if not value or any(ord(character) < 32 for character in value):
            raise _invalid("option value is empty or contains control characters")
        if flag == "--root":
            if root_value is not None or len(value) > 1024:
                raise _invalid("--root is repeated or oversized")
            root_value = value
        elif flag == "--executable":
            if executable is not None or len(value) > 260:
                raise _invalid("--executable is repeated or oversized")
            executable = value
        elif flag == "--timeout-ms":
            if timeout_seen or not value.isascii() or not value.isdigit():
                raise _invalid("--timeout-ms must be a unique decimal integer")
            timeout_ms = int(value)
            if not 1000 <= timeout_ms <= 60000:
                raise _invalid("--timeout-ms must be from 1000 through 60000")
            timeout_seen = True
        else:
            raise _invalid("unknown option")
        index += 2
    if force and action not in {"stop", "restart"}:
        raise _invalid("--force is valid only with stop or restart")
    if root_value is None:
        executable_path = executable_path or Path(sys.executable)
        if executable_path.parent.name.casefold() != "mcp":
            raise _invalid("--root is required outside an installed mcp directory")
        root = executable_path.parent.parent
    else:
        root = Path(root_value)
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise _invalid("Cheat Engine root does not exist") from exc
    if not root.is_dir():
        raise _invalid("Cheat Engine root must be a directory")
    return Options(action, root, executable, timeout_ms, force)


def run(argv: list[str], executable_path: Path | None = None) -> tuple[int, dict]:
    try:
        options = parse_options(argv, executable_path)
        config = ControllerConfig.load(options.root)
        controller = Controller(WindowsPlatform(), lambda timeout: McpObserver(config, timeout))
        return 0, controller.run(options)
    except ControlFailure as exc:
        return exc.exit_code, exc.value()
    except ConfigurationError as exc:
        failure = ControlFailure(2, "CONFIGURATION_ERROR", str(exc), recoverable=True, safe_to_retry=False)
        return failure.exit_code, failure.value()
    except PlatformError as exc:
        failure = ControlFailure(5, "PLATFORM_ERROR", str(exc), recoverable=False, safe_to_retry=False)
        return failure.exit_code, failure.value()
    except Exception:
        failure = ControlFailure(5, "INTERNAL_ERROR", "Controller failed without exposing internal details", recoverable=False, safe_to_retry=False, outcome="unknown")
        return failure.exit_code, failure.value()


def main() -> None:
    exit_code, value = run(sys.argv[1:])
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    sys.stdout.write(encode_result(value) + "\n")
    raise SystemExit(exit_code)
