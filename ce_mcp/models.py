"""Stable protocol models shared by the MCP sidecar and CE bridge adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
import re
from typing import Any, Mapping


_HEX_ADDRESS = re.compile(r"^0x[0-9A-F]+$")
_OPAQUE_ID = re.compile(r"^[a-z][a-z0-9-]{7,127}$")
_ACTION_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


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
class NextAction:
    code: str
    execution: str
    reason: str
    tool: str | None = None
    arguments: Mapping[str, Any] | None = None
    arguments_patch: Mapping[str, Any] | None = None
    preserve_arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ACTION_CODE.fullmatch(self.code):
            raise ContractViolation("next action code must be stable SCREAMING_SNAKE_CASE")
        if self.execution not in {"suggested", "required_before_retry", "manual"}:
            raise ContractViolation("invalid next action execution semantics")
        if not self.reason or len(self.reason) > 256:
            raise ContractViolation("next action reason must contain 1..256 characters")
        if self.tool is not None and not _TOOL_NAME.fullmatch(self.tool):
            raise ContractViolation("invalid next action tool name")
        if self.tool is None and (self.arguments is not None or self.arguments_patch is not None):
            raise ContractViolation("next action arguments require a tool")
        if self.arguments is not None and not isinstance(self.arguments, Mapping):
            raise ContractViolation("next action arguments must be an object")
        if self.arguments_patch is not None and not isinstance(self.arguments_patch, Mapping):
            raise ContractViolation("next action arguments patch must be an object")
        if self.arguments is not None and len(self.arguments) > 32:
            raise ContractViolation("too many next action arguments")
        if self.arguments_patch is not None and len(self.arguments_patch) > 16:
            raise ContractViolation("too many next action argument patches")
        if len(self.preserve_arguments) > 16 or any(
            not isinstance(value, str) or not value or len(value) > 64
            for value in self.preserve_arguments
        ):
            raise ContractViolation("invalid preserved next action arguments")
        try:
            encoded = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ContractViolation("next action must contain JSON-compatible values") from exc
        if len(encoded.encode("utf-8")) > 4096:
            raise ContractViolation("next action exceeds 4096 bytes")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "execution": self.execution,
            "reason": self.reason,
        }
        if self.tool is not None:
            result["tool"] = self.tool
        if self.arguments is not None:
            result["arguments"] = dict(self.arguments)
        if self.arguments_patch is not None:
            result["argumentsPatch"] = dict(self.arguments_patch)
        if self.preserve_arguments:
            result["preserveArguments"] = list(self.preserve_arguments)
        return result


@dataclass(frozen=True)
class ErrorDetail:
    code: str
    message: str
    recoverable: bool
    safe_to_retry: bool
    current_state: str | None = None
    suggested_action: str | None = None
    details: Mapping[str, Any] | None = None
    advice_source: str | None = None
    next_actions: tuple[NextAction, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", self.code):
            raise ContractViolation("error code must be stable SCREAMING_SNAKE_CASE")
        if not self.message or len(self.message) > 512:
            raise ContractViolation("error message must contain 1..512 characters")
        if self.suggested_action is not None and len(self.suggested_action) > 512:
            raise ContractViolation("suggested action exceeds 512 characters")
        if self.advice_source is not None and self.advice_source != "ce-mcp-backend":
            raise ContractViolation("invalid advice source")
        if len(self.next_actions) > 4:
            raise ContractViolation("too many next actions")
        if (self.suggested_action is not None or self.next_actions) and self.advice_source is None:
            object.__setattr__(self, "advice_source", "ce-mcp-backend")

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
        if self.advice_source is not None:
            result["adviceSource"] = self.advice_source
        if self.next_actions:
            result["nextActions"] = [value.to_dict() for value in self.next_actions]
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
        next_actions=(NextAction(
            code="REFRESH_STATUS",
            execution="required_before_retry",
            reason="Obtain the current session generation before continuing.",
            tool="ce.status",
            arguments={},
        ),),
        details={
            "expectedGeneration": expected_generation,
            "actualGeneration": session.generation,
        },
    )
