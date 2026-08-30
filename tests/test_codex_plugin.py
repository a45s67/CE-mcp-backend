import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CodexPluginTests(unittest.TestCase):
    def test_manifest_points_to_existing_mcp_and_skill_components(self) -> None:
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "ce-mcp-backend")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertTrue((ROOT / ".mcp.json").is_file())
        self.assertTrue((ROOT / "skills" / "cheat-engine-debugging" / "SKILL.md").is_file())

    def test_mcp_server_runs_locked_from_plugin_root_without_machine_paths(self) -> None:
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["cheat-engine"]
        self.assertEqual(server["command"], "uv")
        self.assertEqual(server["cwd"], ".")
        self.assertEqual(
            server["args"],
            ["run", "--locked", "ce-mcp-backend", "--transport", "stdio"],
        )
        serialized = json.dumps(config)
        self.assertNotIn("Users\\\\fish", serialized)
        self.assertNotIn("CE_MCP_PIPE_NAME", serialized)


if __name__ == "__main__":
    unittest.main()
