from pathlib import Path
import tempfile
import unittest

from ce_mcp.install_bridge import install_bridge


class InstallBridgeTests(unittest.TestCase):
    def test_installs_atomically_and_refuses_implicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.lua"
            source.write_text("return true\n", encoding="utf-8")
            ce = root / "CE"
            (ce / "autorun").mkdir(parents=True)
            (ce / "cheatengine-x86_64.exe").write_bytes(b"MZ")
            destination = install_bridge(source, ce)
            self.assertEqual(destination.read_text(encoding="utf-8"), "return true\n")
            with self.assertRaises(FileExistsError):
                install_bridge(source, ce)
            source.write_text("return false\n", encoding="utf-8")
            install_bridge(source, ce, replace=True)
            self.assertEqual(destination.read_text(encoding="utf-8"), "return false\n")
            self.assertFalse((ce / "autorun" / ".ce_mcp_bridge.lua.tmp").exists())

    def test_rejects_non_ce_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.lua"
            source.write_text("return true", encoding="utf-8")
            (root / "autorun").mkdir()
            with self.assertRaisesRegex(ValueError, "recognized Cheat Engine"):
                install_bridge(source, root)


if __name__ == "__main__":
    unittest.main()
