"""Disposable target for bounded exact-AOB uniqueness probing."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
from time import monotonic, sleep


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    options = parser.parse_args()

    region = (ctypes.c_ubyte * 65536)()
    signature = bytes.fromhex("D3 71 A9 4C 26 F0 8B 5E 19 C7 42 AD 63 E8 10 95")
    target_offset = 32768
    duplicate_offset = 8192
    for index, byte in enumerate(signature):
        region[target_offset + index] = byte
    for index, byte in enumerate(signature[:8]):
        region[duplicate_offset + index] = byte
    region[duplicate_offset + 8] = signature[8] ^ 0xFF
    base = ctypes.addressof(region)
    options.info.write_text(
        "\n".join((
            f"pid={os.getpid()}", f"base={base:X}", f"end={base + len(region) - 1:X}",
            f"target={base + target_offset:X}", "expected_min=9",
        )) + "\n",
        encoding="ascii",
    )
    deadline = monotonic() + options.seconds
    while monotonic() < deadline:
        sleep(0.05)


if __name__ == "__main__":
    main()
