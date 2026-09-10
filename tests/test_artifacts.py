import tempfile
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from ce_mcp.artifacts import ArtifactStore, ArtifactStoreError
from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.policy import Policy
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"
SESSION = {
    "sessionId": "ce-01jartifact",
    "generation": 3,
    "state": "running",
    "pid": 4242,
    "architecture": "x86_64",
    "pointerWidth": 64,
}


class ArtifactStoreTests(unittest.TestCase):
    @staticmethod
    def _create(store: ArtifactStore, value: bytes = b"x") -> dict:
        return store.create(
            [value], kind="test", media_type="application/octet-stream",
            session_id="session", generation=1, source={},
        )

    def test_create_preview_integrity_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory), max_artifact_bytes=16)
            metadata = store.create(
                [b"AB", b"CD"], kind="test", media_type="application/octet-stream",
                session_id="session", generation=1, source={"test": True},
            )
            observed, preview = store.preview(metadata["artifactId"], offset=1, size=2)
            self.assertEqual(preview, b"BC")
            self.assertEqual(observed["sha256"], metadata["sha256"])
            data_path = Path(directory) / f"{metadata['artifactId']}.bin"
            data_path.write_bytes(b"WXYZ")
            with self.assertRaisesRegex(ArtifactStoreError, "hash"):
                store.metadata(metadata["artifactId"])
            store.delete(metadata["artifactId"])
            self.assertFalse(data_path.exists())

    def test_ids_cannot_escape_root_and_oversize_creation_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, max_artifact_bytes=3)
            with self.assertRaisesRegex(ArtifactStoreError, "invalid artifact ID"):
                store.metadata("../outside")
            with self.assertRaisesRegex(ArtifactStoreError, "size limit"):
                store.create(
                    [b"four"], kind="test", media_type="application/octet-stream",
                    session_id="session", generation=1, source={},
                )
            self.assertEqual(list(root.iterdir()), [])

    def test_retention_removes_only_valid_owned_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, max_artifacts=2, retention_seconds=3600)
            oldest = self._create(store, b"oldest")
            metadata_path = root / f"{oldest['artifactId']}.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["createdAt"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            newer = [self._create(store, value) for value in (b"new-1", b"new-2")]

            self.assertFalse((root / f"{oldest['artifactId']}.bin").exists())
            self.assertEqual({item["artifactId"] for item in store.list(offset=0, limit=10)[0]},
                             {item["artifactId"] for item in newer})

            foreign = root / "notes.txt"
            foreign.write_text("leave me", encoding="utf-8")
            malformed_id = "art-" + "a" * 32
            (root / f"{malformed_id}.bin").write_bytes(b"foreign")
            (root / f"{malformed_id}.json").write_text("{}", encoding="utf-8")
            self.assertEqual(store.prune(), [])
            self.assertTrue(foreign.exists())
            self.assertTrue((root / f"{malformed_id}.bin").exists())

    def test_time_retention_removes_data_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root, retention_seconds=1)
            artifact = self._create(store)
            metadata_path = root / f"{artifact['artifactId']}.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["createdAt"] = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            self.assertEqual(store.prune(), [artifact["artifactId"]])
            self.assertFalse(metadata_path.exists())
            self.assertFalse((root / f"{artifact['artifactId']}.bin").exists())

    def test_metadata_semantics_are_structured_and_list_pages_only_valid_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root)
            artifacts = [self._create(store, value) for value in (b"a", b"b", b"c")]
            invalid_path = root / f"{artifacts[1]['artifactId']}.json"
            invalid = json.loads(invalid_path.read_text(encoding="utf-8"))
            invalid["generation"] = True
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")

            with self.assertRaises(ArtifactStoreError) as raised:
                store.metadata(artifacts[1]["artifactId"])
            self.assertEqual(raised.exception.code, "INVALID_ARTIFACT_METADATA")
            self.assertEqual(raised.exception.details["field"], "generation")

            first, total = store.list(offset=0, limit=1)
            second, second_total = store.list(offset=1, limit=1)
            self.assertEqual(total, 2)
            self.assertEqual(second_total, 2)
            self.assertEqual(
                {first[0]["artifactId"], second[0]["artifactId"]},
                {artifacts[0]["artifactId"], artifacts[2]["artifactId"]},
            )

    def test_unchanged_artifact_uses_cached_full_file_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            artifact = self._create(store, b"cache me")
            with patch("ce_mcp.artifacts.hashlib.sha256", wraps=hashlib.sha256) as sha256:
                store.metadata(artifact["artifactId"])
                store.metadata(artifact["artifactId"])
            self.assertEqual(sha256.call_count, 1)

    def test_cache_rejects_same_size_timestamp_restored_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root)
            artifact = self._create(store, b"a" * 4096)
            store.metadata(artifact["artifactId"])
            data_path = root / f"{artifact['artifactId']}.bin"
            before = data_path.stat()
            with data_path.open("r+b") as stream:
                stream.seek(1000)
                stream.write(b"b")
            os.utime(data_path, ns=(before.st_atime_ns, before.st_mtime_ns))

            with self.assertRaises(ArtifactStoreError) as raised:
                store.metadata(artifact["artifactId"])
            self.assertEqual(raised.exception.code, "ARTIFACT_INTEGRITY_ERROR")


class ArtifactServiceTests(unittest.TestCase):
    def test_dbvm_trace_archive_collects_bounded_pages_into_json_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = FakeBridge()
            store = ArtifactStore(Path(directory))
            token = "t" * 32
            service = BackendService(
                bridge, TOOL_DIR, artifact_store=store,
                policy=Policy("hypervisor", token),
            )
            bridge.register("process.attach", lambda params: {"session": SESSION})
            service.call_tool("ce.process", {"action": "attach", "pid": 4242})

            def trace_results(params):
                self.assertEqual(params["_authorizationToken"], token)
                offset = int(params.get("cursor", "0"))
                all_items = [{"index": 1, "rip": "0x0000000000001000"},
                             {"index": 2, "rip": "0x0000000000001001"}]
                page = all_items[offset:offset + 1]
                result = {
                    "session": SESSION,
                    "trace": {"traceId": "trace-00000003-00000001", "generation": 3},
                    "items": page, "truncated": offset + len(page) < len(all_items),
                }
                if result["truncated"]:
                    result["nextCursor"] = str(offset + len(page))
                return result

            bridge.register("dbvm.trace.results", trace_results)
            outcome = service.call_tool("ce.dbvm_trace", {
                "action": "archive_results", "traceId": "trace-00000003-00000001",
                "expectedGeneration": 3,
            })
            self.assertIsNone(outcome.error)
            self.assertEqual(outcome.result["itemCount"], 2)  # type: ignore[index]
            artifact = outcome.result["artifact"]  # type: ignore[index]
            self.assertEqual(artifact["kind"], "dbvm-trace")
            _, encoded = store.preview(artifact["artifactId"], offset=0, size=4096)
            document = json.loads(encoded)
            self.assertEqual(document["format"], "ce-mcp-dbvm-trace-v1")
            self.assertEqual([item["index"] for item in document["items"]], [1, 2])
            self.assertEqual([call.method for call in bridge.calls].count("dbvm.trace.results"), 2)

    def test_memory_dump_round_trip_never_accepts_a_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = FakeBridge()
            store = ArtifactStore(Path(directory))
            service = BackendService(bridge, TOOL_DIR, artifact_store=store)
            bridge.register("process.attach", lambda params: {"session": SESSION})
            self.assertIsNone(service.call_tool("ce.process", {"action": "attach", "pid": 4242}).error)

            payload = bytes(range(256)) * 1100

            def read_memory(params):
                address = int(params["address"], 16)
                offset = address - 0x1000
                size = params["size"]
                return {
                    "session": SESSION,
                    "resolvedAddress": {"address": f"0x{address:016X}"},
                    "bytes": payload[offset : offset + size].hex(),
                    "encoding": "hex", "complete": True,
                }

            bridge.register("memory.read", read_memory)
            dumped = service.call_tool(
                "ce.artifacts",
                {"action": "memory_dump", "address": "0x1000", "size": len(payload), "expectedGeneration": 3},
            )
            self.assertIsNone(dumped.error)
            artifact_id = dumped.result["artifact"]["artifactId"]  # type: ignore[index]
            self.assertEqual([call.method for call in bridge.calls].count("memory.read"), 2)

            preview = service.call_tool(
                "ce.artifacts", {"action": "preview", "artifactId": artifact_id, "size": 16},
            )
            self.assertEqual(preview.result["bytes"], payload[:16].hex().upper())  # type: ignore[index]
            listed = service.call_tool("ce.artifacts", {"action": "list", "limit": 10})
            self.assertEqual(len(listed.result["items"]), 1)  # type: ignore[index]
            deleted = service.call_tool(
                "ce.artifacts", {"action": "delete", "artifactId": artifact_id},
            )
            self.assertTrue(deleted.result["deleted"])  # type: ignore[index]

            rejected = service.call_tool(
                "ce.artifacts",
                {"action": "memory_dump", "address": "0x1000", "size": 1, "path": "C:\\escape.bin", "expectedGeneration": 3},
            )
            self.assertEqual(rejected.error.code, "INVALID_PARAMS")  # type: ignore[union-attr]

    def test_memory_dump_requires_target_and_configured_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = BackendService(FakeBridge(), TOOL_DIR, artifact_store=ArtifactStore(Path(directory)))
            outcome = service.call_tool(
                "ce.artifacts",
                {"action": "memory_dump", "address": "0x1000", "size": 1, "expectedGeneration": 1},
            )
            self.assertEqual(outcome.error.code, "NO_TARGET")  # type: ignore[union-attr]
        unavailable = BackendService(FakeBridge(), TOOL_DIR).call_tool("ce.artifacts", {"action": "list"})
        self.assertEqual(unavailable.error.code, "CAPABILITY_UNAVAILABLE")  # type: ignore[union-attr]

    def test_invalid_metadata_error_keeps_structured_store_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root)
            artifact = ArtifactStoreTests._create(store)
            metadata_path = root / f"{artifact['artifactId']}.json"
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            value["sha256"] = "not-a-digest"
            metadata_path.write_text(json.dumps(value), encoding="utf-8")
            service = BackendService(FakeBridge(), TOOL_DIR, artifact_store=store)

            outcome = service.call_tool("ce.artifacts", {
                "action": "get_metadata", "artifactId": artifact["artifactId"],
            })
            self.assertEqual(outcome.error.code, "ARTIFACT_ERROR")  # type: ignore[union-attr]
            self.assertEqual(
                outcome.error.details["artifactErrorCode"],  # type: ignore[union-attr,index]
                "INVALID_ARTIFACT_METADATA",
            )
            self.assertEqual(outcome.error.details["field"], "sha256")  # type: ignore[union-attr,index]


if __name__ == "__main__":
    unittest.main()
