"""Length-prefixed UTF-8 JSON framing used by the local CE bridge."""

from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO, Mapping

from .models import ContractViolation


MAX_FRAME_BYTES = 8 * 1024 * 1024
_HEADER = struct.Struct("<I")


def encode_frame(value: Mapping[str, Any], max_bytes: int = MAX_FRAME_BYTES) -> bytes:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if not payload or len(payload) > max_bytes:
        raise ContractViolation("frame size is outside allowed range")
    return _HEADER.pack(len(payload)) + payload


def decode_frame(frame: bytes, max_bytes: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    if len(frame) < _HEADER.size:
        raise ContractViolation("truncated frame header")
    (length,) = _HEADER.unpack_from(frame)
    if length < 1 or length > max_bytes:
        raise ContractViolation("frame size is outside allowed range")
    if len(frame) != _HEADER.size + length:
        raise ContractViolation("frame payload length mismatch")
    try:
        value = json.loads(frame[_HEADER.size :].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractViolation("frame payload is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractViolation("frame payload must be a JSON object")
    return value


def read_frame(stream: BinaryIO, max_bytes: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    header = _read_exact(stream, _HEADER.size)
    (length,) = _HEADER.unpack(header)
    if length < 1 or length > max_bytes:
        raise ContractViolation("frame size is outside allowed range")
    return decode_frame(header + _read_exact(stream, length), max_bytes=max_bytes)


def write_frame(
    stream: BinaryIO, value: Mapping[str, Any], max_bytes: int = MAX_FRAME_BYTES
) -> None:
    stream.write(encode_frame(value, max_bytes=max_bytes))
    if hasattr(stream, "flush"):
        stream.flush()


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise EOFError("bridge stream closed during a frame")
        chunks.extend(chunk)
    return bytes(chunks)
