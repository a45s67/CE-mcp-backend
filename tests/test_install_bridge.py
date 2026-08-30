from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ce_mcp.install_bridge import install_bridge, packaged_bridge_path


class InstallBridgeTests(unittest.TestCase):
    def test_packaged_path_falls_back_to_source_checkout_for_editable_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "checkout" / "ce_mcp" / "install_bridge.py"
            bridge = root / "checkout" / "bridge" / "ce_mcp_bridge.lua"
            bridge.parent.mkdir(parents=True)
            bridge.write_text("return true\n", encoding="utf-8")
            with (
                patch("ce_mcp.install_bridge.sysconfig.get_path", return_value=str(root / "data")),
                patch("ce_mcp.install_bridge.__file__", str(module)),
            ):
                self.assertEqual(packaged_bridge_path(), bridge)

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
