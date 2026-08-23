from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_manager.config import (
    load_commands,
    load_project,
    render_project_config,
    validate_project_name,
)
from workspace_manager.errors import WorkError


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_root = self.root / "source"
        self.project_root.mkdir()
        (self.project_root / "PROJECT.md").write_text("# context\n", encoding="utf-8")
        (self.project_root / "demo.code-workspace").write_text("{}\n", encoding="utf-8")
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def write_project(self, text: str) -> None:
        directory = self.root / "config" / "work" / "projects"
        directory.mkdir(parents=True)
        (directory / "demo.toml").write_text(text, encoding="utf-8")

    def test_rendered_config_round_trips(self) -> None:
        self.write_project(
            render_project_config(
                name="demo",
                root=self.project_root,
                workspace=self.project_root / "demo.code-workspace",
                vscode_profile="Demo Profile",
                chrome_profile="Profile 3",
                urls=["http://localhost:3000"],
                context=self.project_root / "PROJECT.md",
                startup="npm run dev",
            )
        )

        config = load_project("demo")

        self.assertEqual(config.tmux.session, "work-demo")
        self.assertEqual(config.vscode.profile, "Demo Profile")
        self.assertEqual(config.chrome.urls, ("http://localhost:3000",))
        self.assertEqual(config.startup.command, "npm run dev")
        self.assertEqual(config.context.path, (self.project_root / "PROJECT.md").resolve())

    def test_invalid_project_name_is_rejected(self) -> None:
        with self.assertRaises(WorkError):
            validate_project_name("../other")

    def test_command_name_is_rejected_as_project_name(self) -> None:
        with self.assertRaisesRegex(WorkError, "reserved"):
            validate_project_name("status")

    def test_invalid_url_is_rejected(self) -> None:
        self.write_project(
            f'''version = 1
root = "{self.project_root}"
[chrome]
urls = ["file:///etc/passwd"]
'''
        )
        with self.assertRaisesRegex(WorkError, "unsupported Chrome URL"):
            load_project("demo")

    def test_missing_root_is_rejected(self) -> None:
        self.write_project('version = 1\nroot = "/definitely/not/here"\n')
        with self.assertRaisesRegex(WorkError, "project root does not exist"):
            load_project("demo")

    @patch("workspace_manager.config.default_commands")
    def test_bare_tmux_fallback_adopts_new_homebrew_path(self, command_defaults) -> None:
        from workspace_manager.models import CommandConfig

        command_defaults.return_value = CommandConfig(
            "/opt/homebrew/bin/tmux",
            "code",
            "/Applications/Google Chrome",
        )
        global_path = self.root / "config" / "work" / "config.toml"
        global_path.parent.mkdir(parents=True)
        global_path.write_text(
            'version = 1\n[commands]\ntmux = "tmux"\ncode = "code"\nchrome = "chrome"\n',
            encoding="utf-8",
        )

        self.assertEqual(load_commands().tmux, "/opt/homebrew/bin/tmux")


if __name__ == "__main__":
    unittest.main()
