import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import anyio

from ce_mcp.mcp_server import _watch_ce_exit, load_http_token, parse_options
from ce_mcp.server_config import ServerConfig


ROOT = Path(__file__).resolve().parents[1]


class ServerConfigTests(unittest.TestCase):
    def test_relative_token_file_is_bound_to_config_directory(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "transport": "streamable-http",
                        "host": "127.0.0.1",
                        "port": 43180,
                        "tokenFile": "http.token",
                        "requestDeadlineMs": 7000,
                        "maxOutputBytes": 65536,
                        "exitWhenCeExits": True,
                    }
                ),
                encoding="utf-8",
            )
            config = ServerConfig.load(path)
            self.assertEqual(config.token_file, root / "http.token")
            options = parse_options(["--config", str(path), "--ce-pid", "77"])
            self.assertEqual(options.transport, "streamable-http")
            self.assertEqual(options.port, 43180)
            self.assertEqual(options.deadline_ms, 7000)
            self.assertEqual(options.max_output_bytes, 65536)
            self.assertTrue(options.exit_when_ce_exits)

    def test_cli_overrides_non_secret_config_values(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"port":43180}', encoding="utf-8")
            options = parse_options(["--config", str(path), "--port", "43181"])
            self.assertEqual(options.port, 43181)

    def test_exit_lifecycle_requires_http_and_explicit_ce_pid(self) -> None:
        from ce_mcp.mcp_server import run

        with self.assertRaisesRegex(ValueError, "requires Streamable HTTP"):
            run(["--exit-when-ce-exits"])

    def test_unknown_or_invalid_config_is_rejected(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "config.json"
            for value in (
                {"unknown": True}, {"port": 0}, {"exitWhenCeExits": "yes"},
                {"maxOutputBytes": 4095}, {"maxOutputBytes": 4194305},
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    ServerConfig.load(path)

    def test_environment_token_overrides_configured_file(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            token_file = Path(directory) / "http.token"
            token_file.write_text("a" * 32, encoding="utf-8")
            with patch.dict(os.environ, {"CE_MCP_TOKEN": "b" * 32}):
                self.assertEqual(load_http_token(token_file), "b" * 32)

    def test_ce_exit_watcher_requests_http_shutdown(self) -> None:
        class FakeServer:
            should_exit = False

        server = FakeServer()
        observations = iter(([77], []))
        with patch(
            "ce_mcp.mcp_server.enumerate_cheat_engine_pids",
            side_effect=lambda: next(observations),
        ):
            anyio.run(_watch_ce_exit, server, 77, 0.001)
        self.assertTrue(server.should_exit)
