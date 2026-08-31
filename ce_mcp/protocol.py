"""Versioned local bridge envelopes and validation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .models import ContractViolation, ErrorDetail, NextAction


PROTOCOL_VERSION = 1
DEFAULT_DEADLINE_MS = 5_000
MAX_DEADLINE_MS = 300_000
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_METHOD = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SESSION_ID = re.compile(r"^[a-z][a-z0-9-]{7,127}$")


class BridgeTransportError(ConnectionError):
    """The bridge connection failed before a complete response was received."""


@dataclass(frozen=True)
class BridgeRequest:
    request_id: str
    method: str
    params: Mapping[str, Any]
    deadline_ms: int = DEFAULT_DEADLINE_MS
    session_id: str | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ContractViolation("unsupported protocol version")
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ContractViolation("invalid request ID")
        if not _METHOD.fullmatch(self.method):
            raise ContractViolation("invalid bridge method")
        if self.session_id is not None and not _SESSION_ID.fullmatch(self.session_id):
            raise ContractViolation("invalid session ID")
        if not isinstance(self.params, Mapping):
            raise ContractViolation("params must be an object")
        if not 1 <= self.deadline_ms <= MAX_DEADLINE_MS:
            raise ContractViolation("deadline is outside allowed range")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocolVersion": self.protocol_version,
            "requestId": self.request_id,
            "method": self.method,
            "params": dict(self.params),
            "deadlineMs": self.deadline_ms,
        }
        if self.session_id is not None:
            result["sessionId"] = self.session_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BridgeRequest":
        required = {"protocolVersion", "requestId", "method", "params", "deadlineMs"}
        missing = required.difference(value)
        if missing:
            raise ContractViolation(f"missing request fields: {sorted(missing)}")
        allowed = required | {"sessionId"}
        extra = set(value).difference(allowed)
        if extra:
            raise ContractViolation(f"unknown request fields: {sorted(extra)}")
        return cls(
            protocol_version=value["protocolVersion"],
            request_id=value["requestId"],
            session_id=value.get("sessionId"),
            method=value["method"],
            params=value["params"],
            deadline_ms=value["deadlineMs"],
        )


@dataclass(frozen=True)
class BridgeResponse:
    request_id: str
    result: Mapping[str, Any] | None = None
    error: ErrorDetail | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ContractViolation("unsupported protocol version")
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ContractViolation("invalid request ID")
        if (self.result is None) == (self.error is None):
            raise ContractViolation("response must contain exactly one of result or error")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "protocolVersion": self.protocol_version,
            "requestId": self.request_id,
        }
        if self.result is not None:
            value["result"] = dict(self.result)
        else:
            assert self.error is not None
            value["error"] = self.error.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BridgeResponse":
        required = {"protocolVersion", "requestId"}
        missing = required.difference(value)
        if missing:
            raise ContractViolation(f"missing response fields: {sorted(missing)}")
        allowed = required | {"result", "error"}
        extra = set(value).difference(allowed)
        if extra:
            raise ContractViolation(f"unknown response fields: {sorted(extra)}")
        error_value = value.get("error")
        error = None
        if error_value is not None:
            if not isinstance(error_value, Mapping):
                raise ContractViolation("response error must be an object")
            try:
                error = ErrorDetail(
                    code=error_value["code"],
                    message=error_value["message"],
                    recoverable=error_value["recoverable"],
                    safe_to_retry=error_value["safeToRetry"],
                    current_state=error_value.get("currentState"),
                    suggested_action=error_value.get("suggestedAction"),
                    details=error_value.get("details"),
                    advice_source=error_value.get("adviceSource"),
                    next_actions=tuple(
                        NextAction(
                            code=item["code"],
                            execution=item["execution"],
                            reason=item["reason"],
                            tool=item.get("tool"),
                            arguments=item.get("arguments"),
                            arguments_patch=item.get("argumentsPatch"),
                            preserve_arguments=tuple(item.get("preserveArguments", ())),
                        )
                        for item in error_value.get("nextActions", ())
                    ),
                )
            except (KeyError, TypeError) as exc:
                raise ContractViolation("invalid response error") from exc
        result = value.get("result")
        if result is not None and not isinstance(result, Mapping):
            raise ContractViolation("response result must be an object")
        return cls(
            protocol_version=value["protocolVersion"],
            request_id=value["requestId"],
            result=result,
            error=error,
        )
