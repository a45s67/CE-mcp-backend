"""Private immutable artifact store for bounded analysis output."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
from threading import RLock
from typing import Any, Iterable, Mapping
from uuid import uuid4


ARTIFACT_ID = re.compile(r"^art-[0-9a-f]{32}$")


class ArtifactStoreError(ValueError):
    pass


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
            raise ArtifactStoreError("invalid artifact ID")
        data = (self.root / f"{artifact_id}.bin").resolve()
        metadata = (self.root / f"{artifact_id}.json").resolve()
        if data.parent != self.root or metadata.parent != self.root:
            raise ArtifactStoreError("artifact path escaped the configured root")
        return data, metadata

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
                with metadata_temp.open("x", encoding="utf-8", newline="\n") as stream:
                    json.dump(metadata, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(data_temp, data_path)
                os.replace(metadata_temp, metadata_path)
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
                created_at = datetime.fromisoformat(str(value["createdAt"]))
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                continue
            if value.get("artifactId") != artifact_id or created_at.tzinfo is None:
                continue
            entries.append((created_at.astimezone(timezone.utc), artifact_id))
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
        return sorted(removals)

    def prune(self) -> list[str]:
        """Remove expired/excess owned artifact pairs and return their IDs."""
        with self._lock:
            return self._prune_locked(datetime.now(timezone.utc))

    def metadata(self, artifact_id: str) -> dict[str, Any]:
        with self._lock:
            data_path, metadata_path = self._paths(artifact_id)
            if not data_path.is_file() or not metadata_path.is_file():
                raise ArtifactStoreError("artifact does not exist")
            try:
                value = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ArtifactStoreError("artifact metadata is unreadable") from exc
            if value.get("artifactId") != artifact_id or value.get("size") != data_path.stat().st_size:
                raise ArtifactStoreError("artifact metadata is inconsistent")
            digest = hashlib.sha256()
            with data_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(256 * 1024), b""):
                    digest.update(chunk)
            if value.get("sha256") != digest.hexdigest():
                raise ArtifactStoreError("artifact content hash does not match metadata")
            return value

    def list(self, *, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            self._prune_locked(datetime.now(timezone.utc))
            identifiers = [artifact_id for _, artifact_id in self._owned_entries_locked()]
            items = []
            for artifact_id in identifiers[offset : offset + limit]:
                try:
                    items.append(self.metadata(artifact_id))
                except ArtifactStoreError:
                    continue
            return items, len(identifiers)

    def preview(self, artifact_id: str, *, offset: int, size: int) -> tuple[dict[str, Any], bytes]:
        metadata = self.metadata(artifact_id)
        data_path, _ = self._paths(artifact_id)
        if offset > metadata["size"]:
            raise ArtifactStoreError("preview offset is outside the artifact")
        with data_path.open("rb") as stream:
            stream.seek(offset)
            return metadata, stream.read(size)

    def delete(self, artifact_id: str) -> None:
        with self._lock:
            data_path, metadata_path = self._paths(artifact_id)
            if not data_path.exists() and not metadata_path.exists():
                raise ArtifactStoreError("artifact does not exist")
            data_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
