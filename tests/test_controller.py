from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ce_controller.cli import parse_options
from ce_controller.config import ConfigurationError, ControllerConfig
from ce_controller.core import ControlFailure, Controller, Options, encode_result
from ce_controller.http_client import McpObservation, ObservationError
from ce_controller.platform import HostProcess, StopResult, is_host_filename


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakePlatform:
    def __init__(self, hosts=()) -> None:
        self.hosts = list(hosts)
        self.started: list[Path] = []
        self.stop_result = StopResult(True, False, 1)
        self.stop_calls = []
        self.start_host: HostProcess | None = None

    def list_hosts(self, _root: Path):
        if self.started and not self.hosts and self.start_host is not None:
            self.hosts = [self.start_host]
        return list(self.hosts)

    def start(self, executable: Path, _root: Path) -> None:
        self.started.append(executable)

    def stop(self, process: HostProcess, timeout_ms: int, force: bool) -> StopResult:
        self.stop_calls.append((process, timeout_ms, force))
        if self.stop_result.exited:
            self.hosts = []
        return self.stop_result


class FakeObserver:
    def __init__(self, result=None, error: bool = False) -> None:
        self.result = result or McpObservation(True, False, "0.1.0")
        self.error = error

    def observe(self):
        if self.error:
            raise ObservationError("unavailable")
        return self.result


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Cheat Engine"
        self.root.mkdir()
        (self.root / "Cheat Engine.exe").write_bytes(b"launcher")
        self.host = HostProcess(42, self.root / "cheatengine-x86_64.exe")
        self.clock = FakeClock()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def controller(self, platform: FakePlatform, observation=None, error=False):
        return Controller(
            platform,
            lambda _timeout: FakeObserver(observation, error),
            clock=self.clock,
            sleep=self.clock.sleep,
        )

    def test_status_distinguishes_stopped_ready_and_unobservable(self) -> None:
        stopped = self.controller(FakePlatform()).status(Options("status", self.root))
        self.assertEqual(stopped["hostState"], "stopped")
        ready = self.controller(FakePlatform([self.host])).status(Options("status", self.root))
        self.assertEqual((ready["mcpState"], ready["targetState"], ready["safeToStop"]), ("ready", "absent", True))
        unavailable = self.controller(FakePlatform([self.host]), error=True).status(Options("status", self.root))
        self.assertEqual((unavailable["mcpState"], unavailable["targetState"], unavailable["safeToStop"]), ("unavailable", "unobservable", False))

    def test_multiple_hosts_fail_closed(self) -> None:
        other = HostProcess(43, self.root / "cheatengine-i386.exe")
        with self.assertRaises(ControlFailure) as raised:
            self.controller(FakePlatform([self.host, other])).status(Options("status", self.root))
        self.assertEqual(raised.exception.code, "MULTIPLE_HOSTS")

    def test_start_is_idempotent_only_when_ready(self) -> None:
        platform = FakePlatform([self.host])
        value = self.controller(platform).start(Options("start", self.root))
        self.assertFalse(value["started"])
        self.assertEqual(platform.started, [])
        with self.assertRaises(ControlFailure) as raised:
            self.controller(platform, error=True).start(Options("start", self.root))
        self.assertEqual(raised.exception.code, "HOST_NOT_READY")

    def test_start_uses_launcher_and_waits_for_ready_host(self) -> None:
        platform = FakePlatform()
        platform.start_host = self.host
        value = self.controller(platform).start(Options("start", self.root, timeout_ms=1000))
        self.assertTrue(value["started"])
        self.assertEqual(platform.started, [self.root / "Cheat Engine.exe"])

    def test_stop_refuses_unobservable_or_attached_state(self) -> None:
        platform = FakePlatform([self.host])
        with self.assertRaises(ControlFailure) as unavailable:
            self.controller(platform, error=True).stop(Options("stop", self.root))
        self.assertEqual(unavailable.exception.code, "MCP_UNOBSERVABLE")
        attached = McpObservation(True, True, "0.1.0")
        with self.assertRaises(ControlFailure) as active:
            self.controller(platform, attached).stop(Options("stop", self.root))
        self.assertEqual(active.exception.code, "TARGET_ATTACHED")
        self.assertEqual(platform.stop_calls, [])

    def test_force_is_explicit_and_timeout_is_unknown(self) -> None:
        platform = FakePlatform([self.host])
        platform.stop_result = StopResult(False, True, 0)
        with self.assertRaises(ControlFailure) as raised:
            self.controller(platform, error=True).stop(Options("stop", self.root, force=True, timeout_ms=1000))
        self.assertEqual((raised.exception.code, raised.exception.outcome, raised.exception.safe_to_retry), ("HOST_STOP_TIMEOUT", "unknown", False))
        self.assertTrue(platform.stop_calls[0][2])

    def test_restart_stops_then_starts_with_remaining_deadline(self) -> None:
        platform = FakePlatform([self.host])
        platform.start_host = HostProcess(44, self.host.path)
        value = self.controller(platform).restart(Options("restart", self.root, timeout_ms=1000))
        self.assertTrue(value["stopped"])
        self.assertTrue(value["started"])
        self.assertEqual(len(platform.stop_calls), 1)
        self.assertEqual(len(platform.started), 1)

    def test_result_is_bounded_and_contains_no_pretty_print_lines(self) -> None:
        encoded = encode_result({"status": "ok", "hostState": "stopped"})
        self.assertNotIn("\n", encoded)
        self.assertEqual(json.loads(encoded)["status"], "ok")


class ControllerArgumentTests(unittest.TestCase):
    def test_strict_arguments_and_installed_root_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Cheat Engine"
            mcp = root / "mcp"
            mcp.mkdir(parents=True)
            inferred = parse_options(["status"], mcp / "ce-mcp-control.exe")
            self.assertEqual(inferred.root, root.resolve())
            with self.assertRaises(ControlFailure):
                parse_options(["start", "--force", "--root", str(root)])
            with self.assertRaises(ControlFailure):
                parse_options(["stop", "--timeout-ms", "999", "--root", str(root)])
            with self.assertRaises(ControlFailure):
                parse_options(["status", "--root", str(root), "--root", str(root)])

    def test_host_filename_policy(self) -> None:
        self.assertTrue(is_host_filename("Cheat Engine.exe"))
        self.assertTrue(is_host_filename("cheatengine-x86_64-SSE4-AVX2.exe"))
        self.assertFalse(is_host_filename("ceregreset.exe"))
        self.assertFalse(is_host_filename("..\\cheatengine-x86_64.exe"))


class ControllerConfigTests(unittest.TestCase):
    def test_strict_config_and_token_precedence_without_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mcp = root / "mcp"
            mcp.mkdir()
            secret = "f" * 48
            (mcp / "http.token").write_text(secret, encoding="utf-8")
            (mcp / "config.json").write_text(json.dumps({
                "transport": "streamable-http", "host": "127.0.0.1", "port": 8001,
                "tokenFile": "http.token", "requestDeadlineMs": 5000,
                "maxOutputBytes": 1048576, "exitWhenCeExits": True,
            }), encoding="utf-8")
            loaded = ControllerConfig.load(root, {})
            self.assertEqual(loaded.token, secret)
            self.assertEqual(ControllerConfig.load(root, {"CE_MCP_TOKEN": "e" * 48}).token, "e" * 48)
            (mcp / "config.json").write_text('{"transport":"streamable-http","host":"0.0.0.0","port":1,"tokenFile":"http.token"}', encoding="utf-8")
            with self.assertRaises(ConfigurationError) as raised:
                ControllerConfig.load(root, {})
            self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
