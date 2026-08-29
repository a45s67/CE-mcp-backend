from io import BytesIO
from threading import Event
import unittest

from ce_mcp.framing import decode_frame, encode_frame
from ce_mcp.models import ContractViolation
from ce_mcp.protocol import BridgeRequest, BridgeTransportError
from ce_mcp.transport import (
    DEFAULT_PIPE_NAME,
    FramedStreamBridgeClient,
    WindowsNamedPipeBridgeClient,
    cheat_engine_pipe_name,
)


class DuplexFixture(BytesIO):
    """Return a preset response while recording the request written first."""

    def __init__(self, response: dict) -> None:
        super().__init__()
        self.response = encode_frame(response)
        self.request_frame = b""
        self._reading = False

    def flush(self) -> None:
        self.request_frame = self.getvalue()
        self._reading = True

    def read(self, size=-1):
        if not self._reading:
            return b""
        value, self.response = self.response[:size], self.response[size:]
        return value


class PersistentDuplex:
    def __init__(self, responses):
        self.responses = bytearray(b"".join(encode_frame(value) for value in responses))
        self.writes = bytearray()
        self.closed = False

    def write(self, value):
        self.writes.extend(value)
        return len(value)

    def flush(self):
        pass

    def read(self, size=-1):
        if not self.responses:
            return b""
        if size < 0:
            size = len(self.responses)
        value = bytes(self.responses[:size])
        del self.responses[:size]
        return value

    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def test_framed_client_round_trip_and_request_correlation(self) -> None:
        fixture = DuplexFixture(
            {"protocolVersion": 1, "requestId": "req-00000001", "result": {"ok": True}}
        )
        client = FramedStreamBridgeClient(lambda: fixture)
        response = client.call(BridgeRequest("req-00000001", "status.get", {}))
        self.assertEqual(response.result, {"ok": True})
        request = decode_frame(fixture.request_frame)
        self.assertEqual(request["method"], "status.get")

    def test_request_id_mismatch_is_contract_violation(self) -> None:
        fixture = DuplexFixture(
            {"protocolVersion": 1, "requestId": "req-00000002", "result": {}}
        )
        client = FramedStreamBridgeClient(lambda: fixture)
        with self.assertRaises(ContractViolation):
            client.call(BridgeRequest("req-00000001", "status.get", {}))

    def test_truncated_stream_is_transport_error(self) -> None:
        client = FramedStreamBridgeClient(lambda: BytesIO(b""))
        with self.assertRaises(BridgeTransportError):
            client.call(BridgeRequest("req-00000001", "status.get", {}))


class FakeWindowsApi:
    def __init__(self, pid=4321) -> None:
        self.waits = []
        self.cancelled = Event()
        self.pid = pid

    def wait(self, pipe_name, timeout_ms):
        self.waits.append((pipe_name, timeout_ms))

    def cancel(self, stream):
        self.cancelled.set()

    def server_pid(self, stream):
        return self.pid


class BlockingPipe(BytesIO):
    def __init__(self, api):
        super().__init__()
        self.api = api
        self.written = b""

    def flush(self):
        self.written = self.getvalue()

    def read(self, size=-1):
        if self.api.cancelled.wait(1):
            raise OSError(995, "operation cancelled")
        raise AssertionError("deadline cancellation did not fire")


class WindowsNamedPipeTests(unittest.TestCase):
    def test_success_uses_request_deadline_and_local_pipe(self) -> None:
        api = FakeWindowsApi()
        fixture = DuplexFixture(
            {"protocolVersion": 1, "requestId": "req-00000001", "result": {}}
        )
        opened = []

        def opener(name, mode, buffering):
            opened.append((name, mode, buffering))
            return fixture

        client = WindowsNamedPipeBridgeClient(
            api=api, opener=opener, process_enumerator=lambda: [4321]
        )
        response = client.call(
            BridgeRequest("req-00000001", "status.get", {}, deadline_ms=250)
        )
        self.assertEqual(response.result, {})
        expected_pipe = cheat_engine_pipe_name(4321)
        self.assertEqual(api.waits, [(expected_pipe, 250)])
        self.assertEqual(opened, [(expected_pipe, "r+b", 0)])
        client.close()
        self.assertTrue(fixture.closed)

    def test_reuses_connection_until_explicit_close(self) -> None:
        api = FakeWindowsApi()
        stream = PersistentDuplex(
            [
                {"protocolVersion": 1, "requestId": "req-00000001", "result": {}},
                {"protocolVersion": 1, "requestId": "req-00000002", "result": {}},
            ]
        )
        opens = 0

        def opener(*args, **kwargs):
            nonlocal opens
            opens += 1
            return stream

        client = WindowsNamedPipeBridgeClient(
            api=api, opener=opener, process_enumerator=lambda: [4321]
        )
        client.call(BridgeRequest("req-00000001", "status.get", {}))
        client.call(BridgeRequest("req-00000002", "status.get", {}))
        self.assertEqual(opens, 1)
        self.assertEqual(len(api.waits), 1)
        self.assertFalse(stream.closed)
        client.close()
        self.assertTrue(stream.closed)

    def test_deadline_cancels_blocking_io(self) -> None:
        api = FakeWindowsApi()
        stream = BlockingPipe(api)
        client = WindowsNamedPipeBridgeClient(
            api=api, opener=lambda *args, **kwargs: stream,
            process_enumerator=lambda: [4321],
        )
        with self.assertRaises(BridgeTransportError):
            client.call(BridgeRequest("req-00000001", "status.get", {}, deadline_ms=10))
        self.assertTrue(api.cancelled.is_set())

    def test_rejects_remote_or_malformed_pipe_names(self) -> None:
        api = FakeWindowsApi()
        for name in (r"\\server\pipe\bad", r"C:\pipe\bad", r"\\.\pipe\bad\child"):
            with self.assertRaises(ValueError):
                WindowsNamedPipeBridgeClient(name, api=api)

    def test_auto_discovery_fails_closed_for_zero_or_multiple_instances(self) -> None:
        request = BridgeRequest("req-00000001", "status.get", {}, deadline_ms=10)
        for pids, message in (([], "No Cheat Engine"), ([1, 2], "Multiple Cheat Engine")):
            client = WindowsNamedPipeBridgeClient(
                api=FakeWindowsApi(), opener=lambda *args, **kwargs: None,
                process_enumerator=lambda values=pids: values,
            )
            with self.assertRaisesRegex(BridgeTransportError, message):
                client.call(request)

    def test_explicit_ce_pid_builds_deterministic_local_pipe(self) -> None:
        api = FakeWindowsApi(pid=77)
        fixture = DuplexFixture(
            {"protocolVersion": 1, "requestId": "req-00000001", "result": {}}
        )
        client = WindowsNamedPipeBridgeClient(ce_pid=77, api=api, opener=lambda *args, **kwargs: fixture)
        client.call(BridgeRequest("req-00000001", "status.get", {}, deadline_ms=50))
        self.assertEqual(api.waits, [(cheat_engine_pipe_name(77), 50)])

    def test_pipe_server_pid_must_match_selected_ce_instance(self) -> None:
        api = FakeWindowsApi(pid=9999)
        fixture = DuplexFixture(
            {"protocolVersion": 1, "requestId": "req-00000001", "result": {}}
        )
        client = WindowsNamedPipeBridgeClient(
            ce_pid=77, api=api, opener=lambda *args, **kwargs: fixture,
        )
        with self.assertRaisesRegex(BridgeTransportError, "bridge call failed"):
            client.call(BridgeRequest("req-00000001", "status.get", {}, deadline_ms=50))
        self.assertEqual(fixture.request_frame, b"")

    def test_custom_pipe_server_must_still_be_a_recognized_ce_process(self) -> None:
        api = FakeWindowsApi(pid=88)
        fixture = DuplexFixture(
            {"protocolVersion": 1, "requestId": "req-00000001", "result": {}}
        )
        client = WindowsNamedPipeBridgeClient(
            r"\\.\pipe\custom-test", api=api, opener=lambda *args, **kwargs: fixture,
            process_enumerator=lambda: [77],
        )
        with self.assertRaises(BridgeTransportError):
            client.call(BridgeRequest("req-00000001", "status.get", {}, deadline_ms=50))
        self.assertEqual(fixture.request_frame, b"")

if __name__ == "__main__":
    unittest.main()
