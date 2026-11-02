from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_manager.app_compat import configured_apps
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
        directory.mkdir(parents=True, exist_ok=True)
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

        services, vscode, chrome = configured_apps(config)
        self.assertFalse(config.tmux.enabled)
        self.assertEqual(services.extension, "tmux")
        self.assertEqual(services.options["session"], "focus-demo")
        self.assertEqual(vscode.extension, "vscode")
        self.assertEqual(vscode.options["profile"], "Demo Profile")
        self.assertFalse(config.vscode.enabled)
        self.assertFalse(config.chrome.enabled)
        for app in (services, vscode, chrome):
            self.assertEqual(app.launch_mode, 'window')
            self.assertTrue(app.reuse_existing)
        self.assertEqual(chrome.options["urls"], ("http://localhost:3000",))
        self.assertEqual(services.options["startup_command"], "npm run dev")
        self.assertEqual(vscode.options["context"], str((self.project_root / "PROJECT.md").resolve()))

    def test_invalid_project_name_is_rejected(self) -> None:
        with self.assertRaises(WorkError):
            validate_project_name("../other")

    def test_per_app_launch_and_reuse_choices_are_independent(self):
        for section in ('tmux', 'vscode', 'chrome'):
            for mode in ('window', 'instance'):
                for reuse in (True, False):
                    with self.subTest(section=section, mode=mode, reuse=reuse):
                        self.write_project(f'version = 1\nroot = "{self.project_root}"\n'
                                           f'[{section}]\nlaunch_mode = "{mode}"\nreuse_existing = {str(reuse).lower()}\n')
                        project = load_project('demo')
                        app = getattr(project, section)
                        self.assertEqual((app.launch_mode, app.reuse_existing), (mode, reuse))
                        for other in {'tmux', 'vscode', 'chrome'} - {section}:
                            self.assertEqual(getattr(project, other).launch_mode, 'window')
                            self.assertTrue(getattr(project, other).reuse_existing)

    def test_invalid_launch_policy_is_rejected(self):
        for section in ('tmux', 'vscode', 'chrome'):
            for setting in ('launch_mode = "tab"', 'launch_mode = true', 'reuse_existing = "false"'):
                with self.subTest(section=section, setting=setting):
                    self.write_project(f'version = 1\nroot = "{self.project_root}"\n[{section}]\n{setting}\n')
                    with self.assertRaises(WorkError):
                        load_project('demo')

    def test_legacy_vscode_instance_setting_maps_to_launch_mode(self):
        self.write_project(f'version = 1\nroot = "{self.project_root}"\n[vscode]\nseparate_instance = true\n')
        self.assertEqual(load_project('demo').vscode.launch_mode, 'instance')
        self.write_project(f'version = 1\nroot = "{self.project_root}"\n[vscode]\n'
                           'separate_instance = true\nlaunch_mode = "window"\n')
        with self.assertRaisesRegex(WorkError, 'conflicts'):
            load_project('demo')

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

    def test_tmux_windows_resolve_roots_and_commands(self) -> None:
        api = self.project_root / "api"
        web = self.project_root / "web"
        api.mkdir()
        web.mkdir()
        self.write_project(
            f'''version = 1
root = "{self.project_root}"

[tmux]
session = "work-demo"

[[tmux.windows]]
name = "api"
root = "api"
command = "php artisan serve"

[[tmux.windows]]
name = "web"
root = "web"
'''
        )

        config = load_project("demo")

        self.assertEqual([window.name for window in config.tmux.windows], ["api", "web"])
        self.assertEqual(config.tmux.windows[0].root, api.resolve())
        self.assertEqual(config.tmux.windows[0].command, "php artisan serve")
        self.assertEqual(config.tmux.windows[1].root, web.resolve())
        self.assertIsNone(config.tmux.windows[1].command)

    def test_duplicate_tmux_window_names_are_rejected(self) -> None:
        self.write_project(
            f'''version = 1
root = "{self.project_root}"
[tmux]
[[tmux.windows]]
name = "api"
[[tmux.windows]]
name = "api"
'''
        )
        with self.assertRaisesRegex(WorkError, "duplicate tmux window name"):
            load_project("demo")

    def test_space_adapter_is_optional_and_validated(self) -> None:
        self.write_project(
            f'''version = 1
root = "{self.project_root}"

[space]
enabled = true
manager = "desktop-names"
target = "current"
name = "Demo Desktop"
'''
        )

        config = load_project("demo")

        self.assertTrue(config.space.enabled)
        self.assertEqual(config.space.manager, "desktop-names")
        self.assertEqual(config.space.target, "current")
        self.assertEqual(config.space.name, "Demo Desktop")

    def test_unsupported_space_target_is_rejected(self) -> None:
        self.write_project(
            f'''version = 1
root = "{self.project_root}"
[space]
enabled = true
target = "new"
'''
        )
        with self.assertRaisesRegex(WorkError, "space.target must be 'current'"):
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
