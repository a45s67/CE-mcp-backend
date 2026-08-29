"""Local capability profiles and bridge authorization material.

The public MCP arguments never contain the bridge authorization token.  It is
loaded from a local sidecar configuration file and injected only into private
sidecar-to-bridge requests for hypervisor methods.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


PROFILES = ("inspect", "debug", "hypervisor")


@dataclass(frozen=True)
class Policy:
    profile: str = "debug"
    bridge_authorization_token: str | None = None

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"profile must be one of: {', '.join(PROFILES)}")
        token = self.bridge_authorization_token
        if self.profile == "hypervisor":
            if token is None or len(token) < 32:
                raise ValueError(
                    "hypervisor profile requires bridgeAuthorizationToken with at least 32 characters"
                )
        elif token is not None:
            raise ValueError("bridgeAuthorizationToken is only valid for the hypervisor profile")

    @classmethod
    def load(cls, path: Path | None) -> "Policy":
        if path is None:
            return cls()
        try:
            value: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load policy config {path}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ValueError("policy config must be a JSON object")
        allowed = {"profile", "bridgeAuthorizationToken"}
        extra = set(value).difference(allowed)
        if extra:
            raise ValueError(f"unknown policy config fields: {sorted(extra)}")
        profile = value.get("profile", "debug")
        token = value.get("bridgeAuthorizationToken")
        if not isinstance(profile, str):
            raise ValueError("policy profile must be a string")
        if token is not None and not isinstance(token, str):
            raise ValueError("bridgeAuthorizationToken must be a string")
        return cls(profile=profile, bridge_authorization_token=token)

    def private_bridge_params(self) -> dict[str, str]:
        if self.profile != "hypervisor":
            return {}
        assert self.bridge_authorization_token is not None
        return {
            "_policyProfile": "hypervisor",
            "_authorizationToken": self.bridge_authorization_token,
        }
