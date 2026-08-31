from pathlib import Path
import re
import unittest

from ce_mcp import __version__


ROOT = Path(__file__).resolve().parents[1]


class ReleaseScriptTests(unittest.TestCase):
    def test_release_version_is_consistent(self) -> None:
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        bridge = (ROOT / "bridge" / "ce_mcp_bridge.lua").read_text(encoding="utf-8")
        project_match = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
        bridge_match = re.search(
            r'^local BRIDGE_VERSION = "([^"]+)"$', bridge, re.MULTILINE
        )
        self.assertIsNotNone(project_match)
        self.assertIsNotNone(bridge_match)
        self.assertEqual(project_match.group(1), __version__)
        self.assertEqual(bridge_match.group(1), __version__)

    def test_installer_preserves_config_and_token_on_normal_upgrade(self) -> None:
        source = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
        self.assertIn('$RotateToken -or -not (Test-Path -LiteralPath $tokenPath)', source)
        self.assertIn('if (-not (Test-Path -LiteralPath $configPath))', source)
        self.assertIn('icacls.exe $tokenPath "/inheritance:r" "/grant:r"', source)
        self.assertIn('close all Cheat Engine instances', source)
        self.assertNotIn("Write-Output $token", source)

    def test_ci_is_pinned_to_scripted_windows_runner_without_ce(self) -> None:
        source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: windows-2022", source)
        self.assertIn("verify-compiled.py", source)
        self.assertIn("verify-installer.ps1", source)
        self.assertIn("build-standalone.ps1", source)
        self.assertIn("ce-mcp-windows-x64.zip.sha256", source)
        self.assertIn("gh release create", source)
        self.assertIn("tag $env:GITHUB_REF_NAME does not match packaged version", source)
        self.assertNotIn("cheatengine", source.casefold())
