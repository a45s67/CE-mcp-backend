from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseScriptTests(unittest.TestCase):
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
        self.assertNotIn("cheatengine", source.casefold())
