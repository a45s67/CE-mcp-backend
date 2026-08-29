"""Disposable packed target layout for structure workspace reads."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
from time import monotonic, sleep


class ControlledLayout(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("tag", ctypes.c_ubyte * 4), ("flags", ctypes.c_uint32),
        ("counter", ctypes.c_uint64), ("ratio", ctypes.c_float),
        ("name", ctypes.c_char * 12), ("pointer", ctypes.c_void_p),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    options = parser.parse_args()
    value = ControlledLayout()
    value.tag[:] = (0x43, 0x45, 0x4D, 0x43)
    value.flags = 0xA1B2C3D4
    value.counter = 0x1122334455667788
    value.ratio = 3.25
    value.name = b"CE-MCP"
    base = ctypes.addressof(value)
    value.pointer = base + ControlledLayout.flags.offset
    options.info.write_text(
        "\n".join((
            f"pid={os.getpid()}", f"base={base:X}", f"size={ctypes.sizeof(value)}",
            f"pointer={value.pointer:X}",
        )) + "\n",
        encoding="ascii",
    )
    deadline = monotonic() + options.seconds
    while monotonic() < deadline:
        sleep(0.05)


if __name__ == "__main__":
    main()
