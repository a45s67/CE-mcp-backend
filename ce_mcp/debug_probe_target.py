"""Disposable cooperative target for the native debugger lifecycle probe."""

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

    watched = ctypes.c_uint32(0)
    options.info.write_text(
        f"pid={os.getpid()}\naddress={ctypes.addressof(watched):X}\n",
        encoding="ascii",
    )
    deadline = monotonic() + options.seconds
    while monotonic() < deadline:
        watched.value = (watched.value + 1) & 0xFFFFFFFF
        sleep(0.02)


if __name__ == "__main__":
    main()
