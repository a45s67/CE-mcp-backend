"""Stable protocol models shared by the MCP sidecar and CE bridge adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import re
from typing import Any, Mapping


_HEX_ADDRESS = re.compile(r"^0x[0-9A-F]+$")
_OPAQUE_ID = re.compile(r"^[a-z][a-z0-9-]{7,127}$")


class ContractViolation(ValueError):
    """Raised when data violates a public protocol invariant."""


class SessionState(str, Enum):
    ONLINE = "online"
    RUNNING = "running"
    PAUSED = "paused"
    EXITED = "exited"


@dataclass(frozen=True)
class Address:
    """A resolved address; numeric addresses never cross JSON as numbers."""

    address: str
    expression: str | None = None
    module: str | None = None
    rva: str | None = None
    pointer_width: int = 64

    def __post_init__(self) -> None:
        canonical = self.address.upper().replace("0X", "0x", 1)
        object.__setattr__(self, "address", canonical)
        if not _HEX_ADDRESS.fullmatch(canonical):
            raise ContractViolation("address must be a canonical 0x-prefixed hex string")
        if self.rva is not None:
            canonical_rva = self.rva.upper().replace("0X", "0x", 1)
            object.__setattr__(self, "rva", canonical_rva)
            if not _HEX_ADDRESS.fullmatch(canonical_rva):
                raise ContractViolation("rva must be a canonical 0x-prefixed hex string")
        if self.pointer_width not in (32, 64):
            raise ContractViolation("pointer_width must be 32 or 64")
        max_value = (1 << self.pointer_width) - 1
        if int(canonical, 16) > max_value:
            raise ContractViolation("address exceeds pointer width")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "address": self.address,
            "pointerWidth": self.pointer_width,
        }
        if self.expression is not None:
            result["expression"] = self.expression
        if self.module is not None:
            result["module"] = self.module
        if self.rva is not None:
            result["rva"] = self.rva
        return result


@dataclass(frozen=True)
class Session:
    session_id: str
    generation: int
    state: SessionState
    pid: int
    architecture: str
    pointer_width: int

    def __post_init__(self) -> None:
        if not _OPAQUE_ID.fullmatch(self.session_id):
            raise ContractViolation("session_id must be a stable opaque ID")
        if self.generation < 1:
            raise ContractViolation("generation must be positive")
        if self.pid < 1:
            raise ContractViolation("pid must be positive")
        if self.architecture not in ("x86", "x86_64"):
            raise ContractViolation("unsupported architecture")
        expected_width = 32 if self.architecture == "x86" else 64
        if self.pointer_width != expected_width:
            raise ContractViolation("pointer width does not match architecture")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "generation": self.generation,
            "state": self.state.value,
            "pid": self.pid,
            "architecture": self.architecture,
            "pointerWidth": self.pointer_width,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Session":
        try:
            return cls(
                session_id=value["sessionId"],
                generation=value["generation"],
                state=SessionState(value["state"]),
                pid=value["pid"],
                architecture=value["architecture"],
                pointer_width=value["pointerWidth"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractViolation("invalid session object") from exc


@dataclass(frozen=True)
class ErrorDetail:
    code: str
    message: str
    recoverable: bool
    safe_to_retry: bool
    current_state: str | None = None
    suggested_action: str | None = None
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", self.code):
            raise ContractViolation("error code must be stable SCREAMING_SNAKE_CASE")
        if not self.message or len(self.message) > 512:
            raise ContractViolation("error message must contain 1..512 characters")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            "safeToRetry": self.safe_to_retry,
        }
        if self.current_state is not None:
            result["currentState"] = self.current_state
        if self.suggested_action is not None:
            result["suggestedAction"] = self.suggested_action
        if self.details is not None:
            result["details"] = dict(self.details)
        return result


def require_expected_generation(
    session: Session, expected_generation: int | None
) -> ErrorDetail | None:
    """Return a stable stale-session error rather than executing a mutation."""

    if expected_generation == session.generation:
        return None
    return ErrorDetail(
        code="STALE_SESSION",
        message="Target session generation does not match the observed generation",
        recoverable=True,
        safe_to_retry=False,
        current_state=session.state.value,
        suggested_action="Call ce.status and re-resolve target addresses",
        details={
            "expectedGeneration": expected_generation,
            "actualGeneration": session.generation,
        },
    )
