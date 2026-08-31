"""Verify the standalone release layout and its SHA-256 manifest."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    options = parser.parse_args()
    root = options.release.resolve()
    required = {
        "autorun/ce_mcp_bridge.lua",
        "mcp/server.exe",
        "mcp/config.example.json",
        "README.md",
        "CE_MCP_TOOLS.md",
        "install.ps1",
        "VERSION",
        "SHA256SUMS",
    }
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    missing = required.difference(actual)
    if missing:
        raise SystemExit(f"release files are missing: {sorted(missing)}")
    lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    recorded = {}
    for line in lines:
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64:
            raise SystemExit(f"invalid checksum line: {line!r}")
        normalized = PurePosixPath(relative)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise SystemExit(f"unsafe checksum path: {relative!r}")
        if relative in recorded:
            raise SystemExit(f"duplicate checksum path: {relative}")
        recorded[relative] = digest
    expected = actual.difference({"SHA256SUMS"})
    if set(recorded) != expected:
        raise SystemExit("checksum manifest does not cover the exact release file set")
    for relative, expected_digest in recorded.items():
        digest = hashlib.sha256((root / Path(relative)).read_bytes()).hexdigest()
        if digest != expected_digest:
            raise SystemExit(f"checksum mismatch: {relative}")
    print("release verification passed")


if __name__ == "__main__":
    main()
