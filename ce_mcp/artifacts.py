"""Private immutable artifact store for bounded analysis output."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
import re
from threading import RLock
from typing import Any, BinaryIO, Iterable, Mapping
from uuid import uuid4


ARTIFACT_ID = re.compile(r"^art-[0-9a-f]{32}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METADATA_FIELDS = {
    "artifactId", "kind", "mediaType", "size", "sha256", "createdAt",
    "sessionId", "generation", "source",
}
_StatFingerprint = tuple[int, int, int, int, int]


class ArtifactStoreError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "ARTIFACT_STORE_ERROR",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class ArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        max_artifact_bytes: int = 16 * 1024 * 1024,
        max_artifacts: int = 128,
        retention_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        self.root = root.resolve()
        self.max_artifact_bytes = max_artifact_bytes
        self.max_artifacts = max_artifacts
        self.retention_seconds = retention_seconds
        self._lock = RLock()
        self._verification_cache: dict[
            str, tuple[_StatFingerprint, _StatFingerprint, bytes, dict[str, Any]]
        ] = {}
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes must be positive")
        if max_artifacts < 1:
            raise ValueError("max_artifacts must be positive")
        if retention_seconds < 1:
            raise ValueError("retention_seconds must be positive")

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, artifact_id: str) -> tuple[Path, Path]:
        if ARTIFACT_ID.fullmatch(artifact_id) is None:
            raise ArtifactStoreError(
                "invalid artifact ID", code="INVALID_ARTIFACT_ID",
                details={"artifactId": artifact_id},
            )
        data = (self.root / f"{artifact_id}.bin").resolve()
        metadata = (self.root / f"{artifact_id}.json").resolve()
        if data.parent != self.root or metadata.parent != self.root:
            raise ArtifactStoreError("artifact path escaped the configured root")
        return data, metadata

    @staticmethod
    def _fingerprint(value: os.stat_result) -> _StatFingerprint:
        return (
            value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns,
        )

    @classmethod
    def _stream_fingerprint(cls, stream: BinaryIO) -> _StatFingerprint:
        observed = cls._fingerprint(os.fstat(stream.fileno()))
        if os.name != "nt":
            return observed

        import ctypes
        import msvcrt

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_int64),
                ("LastAccessTime", ctypes.c_int64),
                ("LastWriteTime", ctypes.c_int64),
                ("ChangeTime", ctypes.c_int64),
                ("FileAttributes", ctypes.c_uint32),
            ]

        info = FileBasicInfo()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetFileInformationByHandleEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        ]
        kernel32.GetFileInformationByHandleEx.restype = ctypes.c_int
        handle = msvcrt.get_osfhandle(stream.fileno())
        if not kernel32.GetFileInformationByHandleEx(
            handle, 0, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise OSError(ctypes.get_last_error(), "cannot fingerprint artifact file")
        return (*observed[:4], int(info.ChangeTime))

    @classmethod
    def _path_matches(cls, path: Path, fingerprint: _StatFingerprint) -> bool:
        # Windows can expose a different ctime for a path and its open handle.
        # Identity, size, and mtime still bind the handle to the current path.
        observed = cls._fingerprint(path.stat())
        return observed[:4] == fingerprint[:4]

    @staticmethod
    def _metadata_error(
        artifact_id: str, message: str, *, field: str | None = None
    ) -> ArtifactStoreError:
        details = {"artifactId": artifact_id}
        if field is not None:
            details["field"] = field
        return ArtifactStoreError(
            message, code="INVALID_ARTIFACT_METADATA", details=details,
        )

    def _validate_metadata_value(
        self, value: Any, artifact_id: str
    ) -> tuple[dict[str, Any], datetime]:
        if not isinstance(value, dict):
            raise self._metadata_error(artifact_id, "artifact metadata must be an object")
        if set(value) != _METADATA_FIELDS:
            raise self._metadata_error(artifact_id, "artifact metadata fields are invalid")
        if value["artifactId"] != artifact_id:
            raise self._metadata_error(
                artifact_id, "artifact metadata ID is inconsistent", field="artifactId",
            )
        for field in ("kind", "mediaType", "sessionId"):
            if not isinstance(value[field], str) or not value[field]:
                raise self._metadata_error(
                    artifact_id, f"artifact metadata {field} is invalid", field=field,
                )
        size = value["size"]
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= self.max_artifact_bytes:
            raise self._metadata_error(
                artifact_id, "artifact metadata size is invalid", field="size",
            )
        if not isinstance(value["sha256"], str) or SHA256.fullmatch(value["sha256"]) is None:
            raise self._metadata_error(
                artifact_id, "artifact metadata sha256 is invalid", field="sha256",
            )
        try:
            created_at = datetime.fromisoformat(value["createdAt"])
        except (TypeError, ValueError) as exc:
            raise self._metadata_error(
                artifact_id, "artifact metadata createdAt is invalid", field="createdAt",
            ) from exc
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise self._metadata_error(
                artifact_id, "artifact metadata createdAt must include a timezone",
                field="createdAt",
            )
        generation = value["generation"]
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise self._metadata_error(
                artifact_id, "artifact metadata generation is invalid", field="generation",
            )
        if not isinstance(value["source"], dict):
            raise self._metadata_error(
                artifact_id, "artifact metadata source must be an object", field="source",
            )
        try:
            json.dumps(value["source"], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise self._metadata_error(
                artifact_id, "artifact metadata source must contain JSON values", field="source",
            ) from exc
        return dict(value), created_at.astimezone(timezone.utc)

    @staticmethod
    def _content_probe(stream: BinaryIO, size: int) -> bytes:
        positions = sorted({0, max(0, size // 2 - 32), max(0, size - 64)})
        samples = []
        for position in positions:
            stream.seek(position)
            samples.append(stream.read(64))
        return b"".join(samples)

    def _open_verified_locked(
        self, artifact_id: str
    ) -> tuple[dict[str, Any], BinaryIO, BinaryIO, _StatFingerprint, _StatFingerprint]:
        data_path, metadata_path = self._paths(artifact_id)
        metadata_stream: BinaryIO | None = None
        try:
            metadata_stream = metadata_path.open("rb")
            data_stream = data_path.open("rb")
        except (FileNotFoundError, NotADirectoryError) as exc:
            if metadata_stream is not None:
                metadata_stream.close()
            raise ArtifactStoreError(
                "artifact does not exist", code="ARTIFACT_NOT_FOUND",
                details={"artifactId": artifact_id},
            ) from exc
        except OSError as exc:
            if metadata_stream is not None:
                metadata_stream.close()
            raise ArtifactStoreError(
                "artifact is unreadable", code="ARTIFACT_UNREADABLE",
                details={"artifactId": artifact_id},
            ) from exc
        try:
            metadata_fingerprint = self._stream_fingerprint(metadata_stream)
            data_fingerprint = self._stream_fingerprint(data_stream)
            if not stat.S_ISREG(os.fstat(metadata_stream.fileno()).st_mode) or not stat.S_ISREG(
                os.fstat(data_stream.fileno()).st_mode
            ):
                raise ArtifactStoreError(
                    "artifact files must be regular files", code="ARTIFACT_UNREADABLE",
                    details={"artifactId": artifact_id},
                )
            cached = self._verification_cache.get(artifact_id)
            cache_hit = (
                cached is not None
                and cached[:2] == (metadata_fingerprint, data_fingerprint)
                and cached[2] == self._content_probe(data_stream, data_fingerprint[2])
            )
            if cache_hit:
                assert cached is not None
                metadata = deepcopy(cached[3])
            else:
                data_stream.seek(0)
                try:
                    value = json.load(metadata_stream)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ArtifactStoreError(
                        "artifact metadata is unreadable", code="INVALID_ARTIFACT_METADATA",
                        details={"artifactId": artifact_id},
                    ) from exc
                metadata, _ = self._validate_metadata_value(value, artifact_id)
                if metadata["size"] != data_fingerprint[2]:
                    raise self._metadata_error(
                        artifact_id, "artifact metadata size is inconsistent", field="size",
                    )
                digest = hashlib.sha256()
                for chunk in iter(lambda: data_stream.read(256 * 1024), b""):
                    digest.update(chunk)
                if metadata["sha256"] != digest.hexdigest():
                    raise ArtifactStoreError(
                        "artifact content hash does not match metadata",
                        code="ARTIFACT_INTEGRITY_ERROR",
                        details={"artifactId": artifact_id},
                    )
            if (
                self._stream_fingerprint(metadata_stream) != metadata_fingerprint
                or self._stream_fingerprint(data_stream) != data_fingerprint
                or not self._path_matches(metadata_path, metadata_fingerprint)
                or not self._path_matches(data_path, data_fingerprint)
            ):
                raise ArtifactStoreError(
                    "artifact changed during verification", code="ARTIFACT_CHANGED",
                    details={"artifactId": artifact_id},
                )
            self._verification_cache[artifact_id] = (
                metadata_fingerprint, data_fingerprint,
                self._content_probe(data_stream, data_fingerprint[2]), deepcopy(metadata),
            )
            return (
                metadata, metadata_stream, data_stream,
                metadata_fingerprint, data_fingerprint,
            )
        except ArtifactStoreError:
            metadata_stream.close()
            data_stream.close()
            raise
        except OSError as exc:
            metadata_stream.close()
            data_stream.close()
            raise ArtifactStoreError(
                "artifact changed during verification", code="ARTIFACT_CHANGED",
                details={"artifactId": artifact_id},
            ) from exc
        except Exception:
            metadata_stream.close()
            data_stream.close()
            raise

    def create(
        self,
        chunks: Iterable[bytes],
        *,
        kind: str,
        media_type: str,
        session_id: str,
        generation: int,
        source: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            artifact_id = f"art-{uuid4().hex}"
            self._ensure_root()
            data_path, metadata_path = self._paths(artifact_id)
            data_temp = self.root / f".{artifact_id}.{uuid4().hex}.tmp"
            metadata_temp = self.root / f".{artifact_id}.{uuid4().hex}.json.tmp"
            digest = hashlib.sha256()
            size = 0
            try:
                with data_temp.open("xb") as stream:
                    for chunk in chunks:
                        if not isinstance(chunk, bytes):
                            raise ArtifactStoreError("artifact chunks must be bytes")
                        size += len(chunk)
                        if size > self.max_artifact_bytes:
                            raise ArtifactStoreError("artifact exceeds configured size limit")
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                metadata = {
                    "artifactId": artifact_id,
                    "kind": kind,
                    "mediaType": media_type,
                    "size": size,
                    "sha256": digest.hexdigest(),
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "sessionId": session_id,
                    "generation": generation,
                    "source": dict(source),
                }
                self._validate_metadata_value(metadata, artifact_id)
                with metadata_temp.open("x", encoding="utf-8", newline="\n") as stream:
                    json.dump(metadata, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(data_temp, data_path)
                os.replace(metadata_temp, metadata_path)
                self._verification_cache.pop(artifact_id, None)
                self._prune_locked(datetime.now(timezone.utc))
                return metadata
            except Exception:
                data_temp.unlink(missing_ok=True)
                metadata_temp.unlink(missing_ok=True)
                data_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                raise

    def _owned_entries_locked(self) -> list[tuple[datetime, str]]:
        if not self.root.is_dir():
            return []
        entries: list[tuple[datetime, str]] = []
        for metadata_path in self.root.glob("art-*.json"):
            artifact_id = metadata_path.stem
            if ARTIFACT_ID.fullmatch(artifact_id) is None:
                continue
            data_path, _ = self._paths(artifact_id)
            if not data_path.is_file():
                continue
            try:
                value = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata, created_at = self._validate_metadata_value(value, artifact_id)
            except (OSError, ArtifactStoreError, json.JSONDecodeError):
                continue
            try:
                if metadata["size"] != data_path.stat().st_size:
                    continue
            except OSError:
                continue
            entries.append((created_at, artifact_id))
        return sorted(entries)

    def _prune_locked(self, now: datetime) -> list[str]:
        entries = self._owned_entries_locked()
        cutoff = now.astimezone(timezone.utc).timestamp() - self.retention_seconds
        expired = {artifact_id for created_at, artifact_id in entries if created_at.timestamp() < cutoff}
        retained = [entry for entry in entries if entry[1] not in expired]
        excess = max(0, len(retained) - self.max_artifacts)
        removals = expired | {artifact_id for _, artifact_id in retained[:excess]}
        for artifact_id in sorted(removals):
            data_path, metadata_path = self._paths(artifact_id)
            data_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            self._verification_cache.pop(artifact_id, None)
        return sorted(removals)

    def prune(self) -> list[str]:
        """Remove expired/excess owned artifact pairs and return their IDs."""
        with self._lock:
            return self._prune_locked(datetime.now(timezone.utc))

    def metadata(self, artifact_id: str) -> dict[str, Any]:
        with self._lock:
            metadata_stream: BinaryIO | None = None
            data_stream: BinaryIO | None = None
            try:
                value, metadata_stream, data_stream, _, _ = self._open_verified_locked(artifact_id)
                return value
            finally:
                if metadata_stream is not None:
                    metadata_stream.close()
                if data_stream is not None:
                    data_stream.close()

    def list(self, *, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            self._prune_locked(datetime.now(timezone.utc))
            identifiers = [artifact_id for _, artifact_id in self._owned_entries_locked()]
            validated = []
            for artifact_id in identifiers:
                try:
                    validated.append(self.metadata(artifact_id))
                except ArtifactStoreError:
                    continue
            return validated[offset : offset + limit], len(validated)

    def preview(self, artifact_id: str, *, offset: int, size: int) -> tuple[dict[str, Any], bytes]:
        with self._lock:
            data_path, metadata_path = self._paths(artifact_id)
            metadata_stream: BinaryIO | None = None
            data_stream: BinaryIO | None = None
            try:
                metadata, metadata_stream, data_stream, metadata_fp, data_fp = (
                    self._open_verified_locked(artifact_id)
                )
                if offset > metadata["size"]:
                    raise ArtifactStoreError(
                        "preview offset is outside the artifact", code="INVALID_PREVIEW_RANGE",
                        details={"artifactId": artifact_id, "offset": offset},
                    )
                data_stream.seek(offset)
                preview = data_stream.read(size)
                if (
                    self._stream_fingerprint(metadata_stream) != metadata_fp
                    or self._stream_fingerprint(data_stream) != data_fp
                    or not self._path_matches(metadata_path, metadata_fp)
                    or not self._path_matches(data_path, data_fp)
                ):
                    raise ArtifactStoreError(
                        "artifact changed during preview", code="ARTIFACT_CHANGED",
                        details={"artifactId": artifact_id},
                    )
                return metadata, preview
            except OSError as exc:
                raise ArtifactStoreError(
                    "artifact changed during preview", code="ARTIFACT_CHANGED",
                    details={"artifactId": artifact_id},
                ) from exc
            finally:
                if metadata_stream is not None:
                    metadata_stream.close()
                if data_stream is not None:
                    data_stream.close()

    def delete(self, artifact_id: str) -> None:
        with self._lock:
            data_path, metadata_path = self._paths(artifact_id)
            if not data_path.exists() and not metadata_path.exists():
                raise ArtifactStoreError("artifact does not exist")
            data_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            self._verification_cache.pop(artifact_id, None)
