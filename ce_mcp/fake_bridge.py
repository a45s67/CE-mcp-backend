"""Deterministic in-memory bridge used by contract and sidecar tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping

from .models import ContractViolation, ErrorDetail
from .protocol import BridgeRequest, BridgeResponse


Handler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class FakeBridge:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self.calls: list[BridgeRequest] = []

    def register(self, method: str, handler: Handler) -> None:
        if method in self._handlers:
            raise ContractViolation(f"duplicate fake bridge method: {method}")
        self._handlers[method] = handler

    def call(self, request: BridgeRequest) -> BridgeResponse:
        self.calls.append(request)
        handler = self._handlers.get(request.method)
        if handler is None:
            return BridgeResponse(
                request_id=request.request_id,
                error=ErrorDetail(
                    code="METHOD_NOT_FOUND",
                    message=f"Bridge method is not registered: {request.method}",
                    recoverable=False,
                    safe_to_retry=True,
                ),
            )
        try:
            result = handler(request.params)
            if not isinstance(result, Mapping):
                raise ContractViolation("fake bridge handler must return an object")
            return BridgeResponse(request_id=request.request_id, result=result)
        except ContractViolation as exc:
            return BridgeResponse(
                request_id=request.request_id,
                error=ErrorDetail(
                    code="INVALID_PARAMS",
                    message=str(exc),
                    recoverable=True,
                    safe_to_retry=True,
                ),
            )
