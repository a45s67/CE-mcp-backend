import unittest

from ce_mcp.structures import StructureWorkspace, StructureWorkspaceError


class StructureWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = StructureWorkspace(max_structures=2)

    def test_create_update_list_and_delete_with_revision_guard(self) -> None:
        created = self.workspace.create(
            "Header", 32,
            [{"name": "flags", "offset": 4, "type": "u32"}, {"name": "tag", "offset": 0, "type": "bytes", "size": 4}],
        )
        self.assertEqual(created["structureId"], "struct-00000001")
        self.assertEqual([field["name"] for field in created["fields"]], ["tag", "flags"])
        updated = self.workspace.update(
            created["structureId"], 1, "HeaderV2", 32,
            [{"name": "value", "offset": 8, "type": "u64"}],
        )
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(StructureWorkspaceError):
            self.workspace.delete(created["structureId"], 1)
        self.workspace.delete(created["structureId"], 2)
        self.assertEqual(self.workspace.list(), [])

    def test_rejects_overflow_duplicate_names_and_unsupported_types(self) -> None:
        bad_fields = (
            [{"name": "x", "offset": 7, "type": "u16"}],
            [{"name": "x", "offset": 0, "type": "u8"}, {"name": "x", "offset": 1, "type": "u8"}],
            [{"name": "x", "offset": 0, "type": "object"}],
        )
        for fields in bad_fields:
            with self.assertRaises(StructureWorkspaceError):
                self.workspace.create("Bad", 8, fields)


if __name__ == "__main__":
    unittest.main()
