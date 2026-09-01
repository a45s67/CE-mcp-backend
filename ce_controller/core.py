from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Callable, Protocol

from .http_client import McpObservation, ObservationError
from .platform import HostProcess, PlatformError, StopResult, is_host_filename, same_path


MAX_OUTPUT_BYTES = 8192


class Observer(Protocol):
    def observe(self) -> McpObservation: ...


class HostPlatform(Protocol):
    def list_hosts(self, root: Path) -> list[HostProcess]: ...
    def start(self, executable: Path, root: Path) -> None: ...
    def stop(self, process: HostProcess, timeout_ms: int, force: bool) -> StopResult: ...


class ControlFailure(RuntimeError):
    def __init__(
        self, exit_code: int, code: str, message: str, *, recoverable: bool,
        safe_to_retry: bool, outcome: str = "known",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.safe_to_retry = safe_to_retry
        self.outcome = outcome

    def value(self) -> dict[str, Any]:
        return {
            "status": "error", "code": self.code, "message": self.message,
            "recoverable": self.recoverable, "safeToRetry": self.safe_to_retry,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class Options:
    action: str
    root: Path
    executable: str | None = None
    timeout_ms: int = 20_000
    force: bool = False


def encode_result(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise RuntimeError("controller result exceeded its fixed output bound")
    return encoded


class Controller:
    def __init__(
        self, platform: HostPlatform, observer_factory: Callable[[float], Observer],
        *, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._platform = platform
        self._observer_factory = observer_factory
        self._clock = clock
        self._sleep = sleep

    def _hosts(self, root: Path) -> list[HostProcess]:
        try:
            hosts = self._platform.list_hosts(root)
        except PlatformError as exc:
            raise ControlFailure(5, "HOST_OBSERVATION_FAILED", str(exc), recoverable=True, safe_to_retry=True) from exc
        if len(hosts) > 1:
            raise ControlFailure(3, "MULTIPLE_HOSTS", "Multiple Cheat Engine instances match the configured root", recoverable=True, safe_to_retry=False)
        return hosts

    def _observe(self, timeout_seconds: float) -> McpObservation | None:
        try:
            return self._observer_factory(max(0.05, timeout_seconds)).observe()
        except ObservationError:
            return None

    def _host_value(self, process: HostProcess, observation: McpObservation | None) -> dict[str, Any]:
        ready = observation is not None and observation.ready
        target_state = "unobservable" if observation is None else ("attached" if observation.session_present else "absent")
        value: dict[str, Any] = {
            "hostState": "running", "mcpState": "ready" if ready else "unavailable",
            "targetState": target_state, "safeToStop": bool(ready and observation and not observation.session_present),
            "hostPid": process.pid, "hostExecutable": process.path.name,
        }
        if observation is not None and observation.backend_version is not None:
            value["backendVersion"] = observation.backend_version
        return value

    def status(self, options: Options) -> dict[str, Any]:
        hosts = self._hosts(options.root)
        if not hosts:
            return {"status": "ok", "action": "status", "hostState": "stopped", "mcpState": "stopped", "targetState": "absent", "safeToStop": True}
        return {"status": "ok", "action": "status", **self._host_value(hosts[0], self._observe(options.timeout_ms / 1000.0))}

    def _resolve_executable(self, options: Options) -> Path:
        name = options.executable
        if name is not None:
            if Path(name).name != name or len(name) > 260 or not is_host_filename(name):
                raise ControlFailure(2, "INVALID_ARGUMENT", "--executable must be a recognized filename", recoverable=False, safe_to_retry=False)
            candidate = options.root / name
        else:
            launcher = options.root / "Cheat Engine.exe"
            if launcher.is_file():
                candidate = launcher
            else:
                candidates = sorted(
                    path for path in options.root.iterdir()
                    if path.is_file() and is_host_filename(path.name)
                )
                if len(candidates) != 1:
                    raise ControlFailure(3, "HOST_EXECUTABLE_AMBIGUOUS", "A unique Cheat Engine launch executable could not be selected", recoverable=True, safe_to_retry=False)
                candidate = candidates[0]
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ControlFailure(3, "HOST_EXECUTABLE_MISSING", "Cheat Engine launch executable is unavailable", recoverable=True, safe_to_retry=False) from exc
        if not resolved.is_file() or not same_path(resolved.parent, options.root):
            raise ControlFailure(3, "HOST_EXECUTABLE_INVALID", "Cheat Engine launch executable escaped the configured root", recoverable=False, safe_to_retry=False)
        return resolved

    def start(self, options: Options) -> dict[str, Any]:
        hosts = self._hosts(options.root)
        if hosts:
            observation = self._observe(options.timeout_ms / 1000.0)
            if observation is None:
                raise ControlFailure(3, "HOST_NOT_READY", "The existing Cheat Engine host is not MCP-ready", recoverable=True, safe_to_retry=True)
            return {"status": "ok", "action": "start", "started": False, **self._host_value(hosts[0], observation)}
        executable = self._resolve_executable(options)
        try:
            self._platform.start(executable, options.root)
        except (OSError, PlatformError) as exc:
            raise ControlFailure(5, "HOST_START_FAILED", "Cheat Engine could not be started", recoverable=True, safe_to_retry=False, outcome="unknown") from exc
        deadline = self._clock() + options.timeout_ms / 1000.0
        while self._clock() < deadline:
            try:
                hosts = self._platform.list_hosts(options.root)
            except PlatformError as exc:
                raise ControlFailure(5, "HOST_OBSERVATION_FAILED", str(exc), recoverable=True, safe_to_retry=True) from exc
            if len(hosts) == 1:
                observation = self._observe(deadline - self._clock())
                if observation is not None:
                    return {"status": "ok", "action": "start", "started": True, **self._host_value(hosts[0], observation)}
            self._sleep(0.1)
        raise ControlFailure(4, "HOST_START_TIMEOUT", "Cheat Engine readiness was not observed before the deadline", recoverable=True, safe_to_retry=False, outcome="unknown")

    def stop(self, options: Options) -> dict[str, Any]:
        deadline = self._clock() + options.timeout_ms / 1000.0
        hosts = self._hosts(options.root)
        if not hosts:
            return {"status": "ok", "action": "stop", "stopped": False, "forced": False, "hostState": "stopped", "mcpState": "stopped"}
        process = hosts[0]
        observation = self._observe(deadline - self._clock())
        if not options.force:
            if observation is None:
                raise ControlFailure(3, "MCP_UNOBSERVABLE", "Authenticated MCP state is unavailable; refusing to close Cheat Engine", recoverable=True, safe_to_retry=True)
            if observation.session_present:
                raise ControlFailure(3, "TARGET_ATTACHED", "A target session is attached; refusing to close Cheat Engine", recoverable=True, safe_to_retry=False)
        try:
            remaining_ms = max(1, int((deadline - self._clock()) * 1000))
            stopped = self._platform.stop(process, remaining_ms, options.force)
        except PlatformError as exc:
            raise ControlFailure(5, "HOST_STOP_FAILED", str(exc), recoverable=True, safe_to_retry=False, outcome="unknown") from exc
        if not stopped.exited:
            raise ControlFailure(4, "HOST_STOP_TIMEOUT", "Cheat Engine exit was not observed before the deadline", recoverable=True, safe_to_retry=False, outcome="unknown")
        return {"status": "ok", "action": "stop", "stopped": True, "forced": stopped.forced, "hostState": "stopped", "mcpState": "stopped"}

    def restart(self, options: Options) -> dict[str, Any]:
        deadline = self._clock() + options.timeout_ms / 1000.0
        stopped = self.stop(Options("stop", options.root, options.executable, options.timeout_ms, options.force))
        remaining_ms = int((deadline - self._clock()) * 1000)
        if remaining_ms < 1:
            raise ControlFailure(4, "HOST_RESTART_TIMEOUT", "Restart deadline elapsed after Cheat Engine stopped", recoverable=True, safe_to_retry=False, outcome="unknown")
        started = self.start(Options("start", options.root, options.executable, remaining_ms, False))
        return {
            "status": "ok", "action": "restart", "stopped": stopped["stopped"],
            "forced": stopped["forced"], "started": started["started"],
            **{key: value for key, value in started.items() if key not in {"status", "action", "started"}},
        }

    def run(self, options: Options) -> dict[str, Any]:
        if options.action == "status":
            return self.status(options)
        if options.action == "start":
            return self.start(options)
        if options.action == "stop":
            return self.stop(options)
        if options.action == "restart":
            return self.restart(options)
        raise ControlFailure(2, "INVALID_ARGUMENT", "unknown controller action", recoverable=False, safe_to_retry=False)
