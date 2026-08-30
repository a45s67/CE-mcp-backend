"""Disposable, self-describing target for authorized CE MCP live tests."""

from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import threading
import time


MAGIC = 0x13579BDF
MESSAGE = b"CE_MCP_LIVE_TARGET_8F3C2A91"


class Fixture(ctypes.Structure):
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("counter", ctypes.c_int32),
        ("ratio", ctypes.c_double),
        ("message", ctypes.c_char * 32),
        ("pointer", ctypes.c_void_p),
    ]


def _address(value: object) -> str:
    return f"0x{ctypes.addressof(value):016X}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-file", type=Path, required=True)
    parser.add_argument("--large-bytes", type=int, default=0)
    options = parser.parse_args()

    pointed_value = ctypes.c_uint64(0x1122334455667788)
    fixture = Fixture(MAGIC, 1000, 3.141592653589793, MESSAGE, ctypes.addressof(pointed_value))
    twin = (ctypes.c_ubyte * 64)(*range(64))
    twin_copy = (ctypes.c_ubyte * 64)(*range(64))
    different = (ctypes.c_ubyte * 64)(*range(63), 0xFF)
    large = ctypes.create_string_buffer(options.large_bytes) if options.large_bytes else None
    keep_running = threading.Event()
    keep_running.set()

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sleep = kernel32.Sleep
    sleep.argtypes = [ctypes.c_uint32]
    sleep.restype = None
    sleep_address = ctypes.cast(sleep, ctypes.c_void_p).value
    if sleep_address is None:
        raise RuntimeError("could not resolve kernel32!Sleep")

    marker_type = ctypes.CFUNCTYPE(None)

    @marker_type
    def marker() -> None:
        return None

    marker_address = ctypes.cast(marker, ctypes.c_void_p).value
    if marker_address is None:
        raise RuntimeError("could not create executable marker")

    metadata = {
        "pid": __import__("os").getpid(),
        "pointerWidth": ctypes.sizeof(ctypes.c_void_p) * 8,
        "fixture": _address(fixture),
        "fixtureSize": ctypes.sizeof(fixture),
        "fields": {name: getattr(Fixture, name).offset for name, _ in Fixture._fields_},
        "magic": MAGIC,
        "message": MESSAGE.decode("ascii"),
        "pointedValue": _address(pointed_value),
        "twin": _address(twin),
        "twinCopy": _address(twin_copy),
        "different": _address(different),
        "sleep": f"0x{sleep_address:016X}",
        "marker": f"0x{marker_address:016X}",
    }
    if large is not None:
        metadata["large"] = _address(large)
        metadata["largeSize"] = len(large)
    temporary = options.metadata_file.with_suffix(options.metadata_file.suffix + ".tmp")
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary.replace(options.metadata_file)

    def update_counter() -> None:
        while keep_running.is_set():
            time.sleep(3)
            fixture.counter += 1

    threading.Thread(target=update_counter, name="counter", daemon=True).start()

    # Give the client time to attach and install a breakpoint. Later hits are
    # far enough apart that one stop generation remains stable across several
    # MCP round trips.
    time.sleep(20)
    while keep_running.is_set():
        marker()
        time.sleep(60)


if __name__ == "__main__":
    main()
