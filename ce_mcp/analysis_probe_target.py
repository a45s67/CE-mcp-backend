"""Disposable target with controlled equal and differing byte regions."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
from pathlib import Path
from time import monotonic, sleep


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    options = parser.parse_args()

    payload = bytes((index * 17 + 3) & 0xFF for index in range(4096))
    left = ctypes.create_string_buffer(payload, len(payload))
    equal = ctypes.create_string_buffer(payload, len(payload))
    differing_payload = bytearray(payload)
    differing_payload[2057] ^= 0x5A
    differing = ctypes.create_string_buffer(bytes(differing_payload), len(payload))
    options.info.write_text(
        "\n".join((
            f"pid={os.getpid()}", f"left={ctypes.addressof(left):X}",
            f"equal={ctypes.addressof(equal):X}", f"differing={ctypes.addressof(differing):X}",
            f"size={len(payload)}", f"difference=2057", f"md5={hashlib.md5(payload).hexdigest()}",
        )) + "\n",
        encoding="ascii",
    )
    deadline = monotonic() + options.seconds
    while monotonic() < deadline:
        sleep(0.05)


if __name__ == "__main__":
    main()
