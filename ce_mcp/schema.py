"""Minimal JSON Schema validator for the checked-in Phase 0 contracts.

The implementation intentionally validates only the vocabulary used by our own
schemas. Production MCP integration may use a full JSON Schema implementation,
while these checks stay dependency-free for offline contract tests.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .models import ContractViolation


def validate(schema: Mapping[str, Any], value: Any, path: str = "$") -> None:
    if "oneOf" in schema:
        matches = 0
        errors: list[str] = []
        for candidate in schema["oneOf"]:
            try:
                validate(candidate, value, path)
                matches += 1
            except ContractViolation as exc:
                errors.append(str(exc))
        if matches != 1:
            raise ContractViolation(f"{path}: expected exactly one schema match")
        return

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ContractViolation(f"{path}: expected object")
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise ContractViolation(f"{path}: missing required property {name}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value).difference(properties)
            if extra:
                raise ContractViolation(f"{path}: unknown properties {sorted(extra)}")
        for name, item in value.items():
            if name in properties:
                validate(properties[name], item, f"{path}.{name}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ContractViolation(f"{path}: expected array")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ContractViolation(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ContractViolation(f"{path}: too many items")
        for index, item in enumerate(value):
            validate(schema["items"], item, f"{path}[{index}]")
    elif expected == "string":
        if not isinstance(value, str):
            raise ContractViolation(f"{path}: expected string")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ContractViolation(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractViolation(f"{path}: string is too long")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ContractViolation(f"{path}: string does not match pattern")
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ContractViolation(f"{path}: expected integer")
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractViolation(f"{path}: integer is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractViolation(f"{path}: integer exceeds maximum")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ContractViolation(f"{path}: expected boolean")

    if "enum" in schema and value not in schema["enum"]:
        raise ContractViolation(f"{path}: value is not in enum")
    if "const" in schema and value != schema["const"]:
        raise ContractViolation(f"{path}: value does not match const")
