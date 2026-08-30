"""Explicit installer for the bridge shipped in the Python distribution."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sysconfig
from typing import Sequence


BRIDGE_FILENAME = "ce_mcp_bridge.lua"
INSTALLED_FILENAME = "ce_mcp_bridge.lua"


def packaged_bridge_path() -> Path:
    installed = Path(sysconfig.get_path("data")) / "share" / "ce-mcp-backend" / "bridge" / BRIDGE_FILENAME
    if installed.is_file():
        return installed
    checkout = Path(__file__).resolve().parents[1] / "bridge" / BRIDGE_FILENAME
    if checkout.is_file():
        return checkout
    return installed


def install_bridge(source: Path, ce_directory: Path, *, replace: bool = False) -> Path:
    source = source.resolve(strict=True)
    ce_directory = ce_directory.resolve(strict=True)
    if not any((ce_directory / name).is_file() for name in (
        "cheatengine-x86_64.exe", "cheatengine-i386.exe", "Cheat Engine.exe",
    )):
        raise ValueError("selected directory does not contain a recognized Cheat Engine executable")
    autorun = (ce_directory / "autorun").resolve(strict=True)
    if autorun.parent != ce_directory:
        raise ValueError("Cheat Engine autorun directory escapes the selected installation")
    destination = (autorun / INSTALLED_FILENAME).resolve()
    if destination.parent != autorun:
        raise ValueError("bridge destination escapes the Cheat Engine autorun directory")
    if destination.exists() and not replace:
        raise FileExistsError(f"bridge already exists: {destination}; pass --replace explicitly")
    temporary = autorun / f".{INSTALLED_FILENAME}.tmp"
    if temporary.exists():
        raise FileExistsError(f"temporary installer path already exists: {temporary}")
    try:
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-install-bridge")
    parser.add_argument("--ce-dir", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    options = parser.parse_args(argv)
    destination = install_bridge(packaged_bridge_path(), options.ce_dir, replace=options.replace)
    print(f"Installed CE MCP bridge: {destination}")


if __name__ == "__main__":
    main()
