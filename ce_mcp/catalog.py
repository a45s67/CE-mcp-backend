"""Load and validate the checked-in MCP tool catalog deterministically."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .models import ContractViolation


_TOOL_NAME = re.compile(r"^ce\.[a-z][a-z0-9_]{1,63}$")
_ANNOTATIONS = {"readOnlyHint", "destructiveHint", "idempotentHint"}


def load_catalog(directory: Path) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    names: set[str] = set()
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        try:
            tool = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractViolation(f"invalid tool contract {path.name}") from exc
        _validate_definition(tool, path)
        name = tool["name"]
        if name in names:
            raise ContractViolation(f"duplicate tool name: {name}")
        names.add(name)
        tools.append(tool)
    tools.sort(key=lambda tool: tool["name"])
    return tools


def _validate_definition(tool: Any, path: Path) -> None:
    if not isinstance(tool, dict):
        raise ContractViolation(f"tool contract must be an object: {path.name}")
    required = {"name", "description", "inputSchema", "outputSchema", "annotations"}
    missing = required.difference(tool)
    if missing:
        raise ContractViolation(f"{path.name}: missing fields {sorted(missing)}")
    name = tool["name"]
    if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
        raise ContractViolation(f"{path.name}: invalid tool name")
    if path.stem != name:
        raise ContractViolation(f"{path.name}: filename must match tool name")
    description = tool["description"]
    if not isinstance(description, str) or not 1 <= len(description) <= 512:
        raise ContractViolation(f"{path.name}: invalid description")
    for schema_name in ("inputSchema", "outputSchema"):
        schema = tool[schema_name]
        if not isinstance(schema, dict) or not ({"type", "oneOf"} & set(schema)):
            raise ContractViolation(f"{path.name}: invalid {schema_name}")
    annotations = tool["annotations"]
    if not isinstance(annotations, dict) or set(annotations) != _ANNOTATIONS:
        raise ContractViolation(f"{path.name}: annotations must be explicit")
    if any(not isinstance(value, bool) for value in annotations.values()):
        raise ContractViolation(f"{path.name}: annotation values must be boolean")
