from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ce_controller.config import ControllerConfig
from ce_controller.http_client import McpObservation, McpObserver, ObservationError


class ControllerHttpTests(unittest.TestCase):
    def observe(self, result):
        observer = McpObserver(ControllerConfig(Path.cwd(), "127.0.0.1", 8000, "test"), 1)
        with patch.object(observer, "_json_request", side_effect=[
            {"status": "ready", "bridge_connected": True},
            {"result": {"serverInfo": {"name": "ce-mcp-backend"}}},
            {"result": result},
        ]):
            return observer.observe()

    def test_direct_status_preserves_attached_state(self) -> None:
        for attached in (False, True):
            status = {"backend": {"version": "0.1.0"}, "bridge": {"connected": True}}
            if attached:
                status["session"] = {"generation": 1}
            with self.subTest(attached=attached):
                result = {"content": [{"type": "text", "text": json.dumps(status)}]}
                self.assertEqual(self.observe(result), McpObservation(True, attached, "0.1.0"))

    def test_rejects_legacy_and_malformed_content(self) -> None:
        status = {"backend": {"version": "0.1.0"}, "bridge": {"connected": True}}
        text = {"type": "text", "text": json.dumps(status)}
        cases = [
            None, [], {},
            {"structuredContent": status},
            {"structuredContent": status, "content": [text]},
            {"structuredContent": None, "content": [text]},
            {"content": []},
            {"content": [text, text]},
            {"content": text},
            {"content": [None]},
            {"content": [{"type": "image", "text": "{}"}]},
            {"content": [{"type": "text", "text": None}]},
            {"content": [text], "isError": True},
        ]
        cases.extend({"content": [{"type": "text", "text": value}]} for value in (
            "summary", "[]", "null", "1", "{}",
            json.dumps({"result": status}),
            json.dumps({**status, "error": {}}),
            json.dumps({**status, "bridge": {"connected": False}}),
        ))
        for result in cases:
            with self.subTest(result=result), self.assertRaises(ObservationError):
                self.observe(result)

    def test_error_envelope_fails_closed(self) -> None:
        with self.assertRaisesRegex(ObservationError, "returned an error"):
            self.observe({"isError": True, "content": [{
                "type": "text",
                "text": '{"error":{"code":"BRIDGE_UNAVAILABLE","message":"offline"}}',
            }]})


if __name__ == "__main__":
    unittest.main()
