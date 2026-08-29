"""Bounded, sidecar-owned structure definitions."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Mapping, Sequence


class StructureWorkspaceError(ValueError):
    pass


_FIXED_WIDTHS = {
    "u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4,
    "u64": 8, "i64": 8, "f32": 4, "f64": 8,
}
_VARIABLE_TYPES = {"pointer", "bytes", "string", "wstring"}


@dataclass(frozen=True)
class StructureDefinition:
    structure_id: str
    revision: int
    name: str
    size: int
    fields: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "structureId": self.structure_id, "revision": self.revision,
            "name": self.name, "size": self.size,
            "fields": [dict(field) for field in self.fields],
        }


class StructureWorkspace:
    def __init__(self, *, max_structures: int = 128, max_fields: int = 256, max_size: int = 65536) -> None:
        self.max_structures = max_structures
        self.max_fields = max_fields
        self.max_size = max_size
        self._items: dict[str, StructureDefinition] = {}
        self._counter = 0
        self._lock = Lock()

    def create(self, name: str, size: int, fields: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        with self._lock:
            if len(self._items) >= self.max_structures:
                raise StructureWorkspaceError("structure workspace limit reached")
            normalized = self._validate(name, size, fields)
            self._counter += 1
            structure_id = f"struct-{self._counter:08x}"
            item = StructureDefinition(structure_id, 1, name, size, normalized)
            self._items[structure_id] = item
            return item.to_dict()

    def update(
        self, structure_id: str, expected_revision: int, name: str,
        size: int, fields: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        with self._lock:
            current = self._require(structure_id)
            if current.revision != expected_revision:
                raise StructureWorkspaceError("structure revision is stale")
            normalized = self._validate(name, size, fields)
            item = StructureDefinition(structure_id, current.revision + 1, name, size, normalized)
            self._items[structure_id] = item
            return item.to_dict()

    def get(self, structure_id: str) -> Mapping[str, Any]:
        with self._lock:
            return self._require(structure_id).to_dict()

    def list(self) -> list[Mapping[str, Any]]:
        with self._lock:
            return [self._items[key].to_dict() for key in sorted(self._items)]

    def delete(self, structure_id: str, expected_revision: int) -> None:
        with self._lock:
            current = self._require(structure_id)
            if current.revision != expected_revision:
                raise StructureWorkspaceError("structure revision is stale")
            del self._items[structure_id]

    def _require(self, structure_id: str) -> StructureDefinition:
        item = self._items.get(structure_id)
        if item is None:
            raise StructureWorkspaceError("structure does not exist")
        return item

    def _validate(
        self, name: str, size: int, fields: Sequence[Mapping[str, Any]],
    ) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(name, str) or not 1 <= len(name) <= 128:
            raise StructureWorkspaceError("structure name must contain 1 to 128 characters")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= self.max_size:
            raise StructureWorkspaceError(f"structure size must be between 1 and {self.max_size}")
        if not 1 <= len(fields) <= self.max_fields:
            raise StructureWorkspaceError(f"field count must be between 1 and {self.max_fields}")
        normalized: list[Mapping[str, Any]] = []
        names: set[str] = set()
        for raw in fields:
            field_name = raw.get("name")
            field_type = raw.get("type")
            offset = raw.get("offset")
            if not isinstance(field_name, str) or not 1 <= len(field_name) <= 128 or field_name in names:
                raise StructureWorkspaceError("field names must be unique and contain 1 to 128 characters")
            if field_type not in _FIXED_WIDTHS and field_type not in _VARIABLE_TYPES:
                raise StructureWorkspaceError(f"unsupported field type: {field_type!r}")
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                raise StructureWorkspaceError("field offset must be a non-negative integer")
            width = _FIXED_WIDTHS.get(str(field_type))
            if field_type == "pointer":
                width = 8
            elif field_type in {"bytes", "string", "wstring"}:
                width = raw.get("size")
                if not isinstance(width, int) or isinstance(width, bool) or width < 1 or width > 4096:
                    raise StructureWorkspaceError("variable-width field size must be between 1 and 4096")
            assert width is not None
            if offset + width > size:
                raise StructureWorkspaceError("field extends beyond structure size")
            item: dict[str, Any] = {"name": field_name, "offset": offset, "type": field_type}
            if field_type in {"bytes", "string", "wstring"}:
                item["size"] = width
            normalized.append(item)
            names.add(field_name)
        normalized.sort(key=lambda field: (int(field["offset"]), str(field["name"])))
        return tuple(normalized)
