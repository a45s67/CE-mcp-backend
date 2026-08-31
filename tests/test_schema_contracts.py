import json
from pathlib import Path
import unittest

from ce_mcp.models import ErrorDetail, ContractViolation, NextAction
from ce_mcp.protocol import BridgeRequest, BridgeResponse
from ce_mcp.schema import validate


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "ce_mcp" / "contracts" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


class BridgeSchemaTests(unittest.TestCase):
    def test_request_model_matches_checked_in_schema(self) -> None:
        value = BridgeRequest(
            request_id="req-00000001",
            method="memory.read",
            params={"address": "0x1234", "size": 4},
            session_id="ce-01jabcdef",
        ).to_dict()
        validate(load("bridge-request.schema.json"), value)

    def test_success_and_error_responses_match_schema(self) -> None:
        schema = load("bridge-response.schema.json")
        validate(
            schema,
            BridgeResponse("req-00000001", result={"connected": True}).to_dict(),
        )
        error = ErrorDetail(
            code="TARGET_NOT_PAUSED",
            message="Target must be paused",
            recoverable=True,
            safe_to_retry=True,
            current_state="running",
            suggested_action="ce.debug_control(action='pause')",
            next_actions=(NextAction(
                "PAUSE_TARGET", "suggested", "Pause before reading registers.",
                tool="ce.debug_control", arguments={"action": "pause"},
            ),),
        )
        validate(schema, BridgeResponse("req-00000002", error=error).to_dict())
        validate(load("error.schema.json"), error.to_dict())

    def test_error_code_pattern_is_enforced(self) -> None:
        value = {
            "code": "bad-code",
            "message": "bad",
            "recoverable": False,
            "safeToRetry": False,
        }
        with self.assertRaises(ContractViolation):
            validate(load("error.schema.json"), value)


if __name__ == "__main__":
    unittest.main()
