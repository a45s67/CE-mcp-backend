from io import BytesIO
import struct
import unittest

from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.framing import decode_frame, encode_frame, read_frame, write_frame
from ce_mcp.models import ContractViolation, ErrorDetail, NextAction
from ce_mcp.protocol import BridgeRequest, BridgeResponse


class FramingTests(unittest.TestCase):
    def test_frame_round_trip_preserves_unicode_json(self) -> None:
        value = {"message": "測試", "address": "0xFFFFFFFFFFFFFFFF"}
        self.assertEqual(decode_frame(encode_frame(value)), value)

    def test_stream_round_trip(self) -> None:
        stream = BytesIO()
        write_frame(stream, {"ok": True})
        stream.seek(0)
        self.assertEqual(read_frame(stream), {"ok": True})

    def test_rejects_oversize_before_reading_payload(self) -> None:
        stream = BytesIO(struct.pack("<I", 1025))
        with self.assertRaises(ContractViolation):
            read_frame(stream, max_bytes=1024)

    def test_rejects_length_mismatch(self) -> None:
        with self.assertRaises(ContractViolation):
            decode_frame(struct.pack("<I", 4) + b"{}")


class EnvelopeTests(unittest.TestCase):
    def test_request_round_trip(self) -> None:
        request = BridgeRequest(
            request_id="req-00000001",
            session_id="ce-01jabcdef",
            method="memory.read",
            params={"address": "0x1234", "size": 4},
        )
        self.assertEqual(BridgeRequest.from_dict(request.to_dict()), request)

    def test_request_rejects_unknown_fields_and_bad_session(self) -> None:
        value = BridgeRequest("req-00000001", "memory.read", {}).to_dict()
        value["unexpected"] = True
        with self.assertRaises(ContractViolation):
            BridgeRequest.from_dict(value)
        with self.assertRaises(ContractViolation):
            BridgeRequest("req-00000001", "memory.read", {}, session_id="bad")

    def test_response_requires_exactly_one_outcome(self) -> None:
        with self.assertRaises(ContractViolation):
            BridgeResponse(request_id="req-00000001")
        with self.assertRaises(ContractViolation):
            BridgeResponse(
                request_id="req-00000001",
                result={"ok": True},
                error=ErrorDetail(
                    code="INTERNAL_ERROR",
                    message="invalid dual outcome fixture",
                    recoverable=False,
                    safe_to_retry=False,
                ),
            )

    def test_response_round_trip(self) -> None:
        response = BridgeResponse("req-00000001", result={"ok": True})
        self.assertEqual(BridgeResponse.from_dict(response.to_dict()), response)

        advised = BridgeResponse(
            "req-00000002",
            error=ErrorDetail(
                "STALE_SESSION", "Session is stale", True, False,
                suggested_action="Refresh status.",
                next_actions=(NextAction(
                    "REFRESH_STATUS", "required_before_retry",
                    "Obtain the current generation.", tool="ce.status", arguments={},
                ),),
            ),
        )
        self.assertEqual(BridgeResponse.from_dict(advised.to_dict()), advised)

    def test_response_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ContractViolation):
            BridgeResponse.from_dict(
                {
                    "protocolVersion": 1,
                    "requestId": "req-00000001",
                    "result": {},
                    "unexpected": True,
                }
            )


class FakeBridgeTests(unittest.TestCase):
    def test_dispatches_registered_method_and_records_call(self) -> None:
        bridge = FakeBridge()
        bridge.register("status.get", lambda params: {"connected": True})
        request = BridgeRequest("req-00000001", "status.get", {})
        response = bridge.call(request)
        self.assertEqual(response.result, {"connected": True})
        self.assertEqual(bridge.calls, [request])

    def test_unknown_method_is_structured_error(self) -> None:
        bridge = FakeBridge()
        response = bridge.call(BridgeRequest("req-00000001", "missing.call", {}))
        assert response.error is not None
        self.assertEqual(response.error.code, "METHOD_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
