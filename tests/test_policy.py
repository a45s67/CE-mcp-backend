import json
from pathlib import Path
import tempfile
import unittest

from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.policy import Policy
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"


class PolicyTests(unittest.TestCase):
    def test_default_is_debug_without_secret(self) -> None:
        policy = Policy.load(None)
        self.assertEqual(policy.profile, "debug")
        self.assertEqual(policy.private_bridge_params(), {})

    def test_hypervisor_requires_long_secret_and_keeps_it_private(self) -> None:
        token = "x" * 32
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps({
                "profile": "hypervisor", "bridgeAuthorizationToken": token,
            }), encoding="utf-8")
            policy = Policy.load(path)
        self.assertEqual(policy.private_bridge_params()["_authorizationToken"], token)
        with self.assertRaises(ValueError):
            Policy(profile="hypervisor", bridge_authorization_token="short")

    def test_unknown_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"profile":"debug","surprise":true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown policy"):
                Policy.load(path)

    def test_inspect_profile_blocks_mutation_before_bridge(self) -> None:
        bridge = FakeBridge()
        service = BackendService(bridge, CONTRACTS, policy=Policy(profile="inspect"))
        outcome = service.call_tool("ce.process", {"action": "attach", "pid": 1234})
        self.assertEqual(outcome.error.code, "PROFILE_DISABLED")  # type: ignore[union-attr]
        self.assertEqual(bridge.calls, [])

    def test_status_removes_bridge_claimed_dbvm_enablement_under_debug_profile(self) -> None:
        bridge = FakeBridge()
        bridge.register("status.get", lambda _: {
            "bridge": {"connected": True},
            "capabilities": {
                "available": ["dbvm.watch"], "enabled": ["dbvm.watch"],
                "disabledReasons": {}, "limits": {},
            },
        })
        service = BackendService(bridge, CONTRACTS)
        outcome = service.call_tool("ce.status", {})
        self.assertNotIn("dbvm.watch", outcome.result["capabilities"]["enabled"])  # type: ignore[index]
        self.assertIn("dbvm.watch", outcome.result["capabilities"]["disabledReasons"])  # type: ignore[index]

    def test_hypervisor_secret_is_injected_only_after_public_validation(self) -> None:
        token = "s" * 32
        bridge = FakeBridge()
        bridge.register("process.attach", lambda _: {"session": {
            "sessionId": "ce-01jabcdef", "generation": 7, "state": "running",
            "pid": 4242, "architecture": "x86_64", "pointerWidth": 64,
        }})
        bridge.register("dbvm.watch.status", lambda _: {
            "session": {"sessionId": "ce-01jabcdef", "generation": 7, "state": "running",
                "pid": 4242, "architecture": "x86_64", "pointerWidth": 64},
            "items": [], "truncated": False,
        })
        service = BackendService(bridge, CONTRACTS, policy=Policy("hypervisor", token))
        service.call_tool("ce.process", {"action": "attach", "pid": 4242})
        invalid = service.call_tool("ce.dbvm_watch", {
            "action": "status", "expectedGeneration": 7, "_authorizationToken": token,
        })
        self.assertEqual(invalid.error.code, "INVALID_PARAMS")  # type: ignore[union-attr]
        valid = service.call_tool("ce.dbvm_watch", {"action": "status", "expectedGeneration": 7})
        self.assertIsNone(valid.error)
        self.assertEqual(bridge.calls[-1].params["_authorizationToken"], token)
        self.assertEqual(bridge.calls[-1].params["_policyProfile"], "hypervisor")


if __name__ == "__main__":
    unittest.main()
