from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.app_compat import configured_apps
from workspace_manager.config import default_commands, load_project, toml_quote
from workspace_manager.errors import WorkError
from workspace_manager.models import WorkspaceStatus


class SessionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.config = self.base / "config"
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def load(self, text: str, *, namespace: str = "focus"):
        directory = self.config / namespace / "projects"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "demo.toml").write_text(
            f"version = 1\nroot = {toml_quote(str(self.root))}\n{text}\n", encoding="utf-8"
        )
        return load_project("demo")

    def test_explicit_session_resource_normalizes_nested_units_and_policies(self) -> None:
        project = self.load('''
[[apps]]
name = "services"
extension = "tmux"
launch_mode = "instance"
reuse_existing = false
[apps.options]
session = "my-services"
presentation = "windows"
startup_command = "npm run dev"
startup_window = "dev"
startup_check = "curl -f localhost:3000"
windows = [
  { name = "api", root = "server", command = "python app.py" },
  { name = "web", root = "." },
]
''')
        app, = configured_apps(project)
        self.assertFalse(project.tmux.enabled)
        self.assertEqual(app.options["session"], "my-services")
        self.assertEqual(app.options["presentation"], "windows")
        self.assertEqual((app.launch_mode, app.reuse_existing), ("instance", False))
        self.assertEqual(app.options["startup_command"], "npm run dev")
        self.assertEqual(app.options["startup_check"], "curl -f localhost:3000")
        self.assertEqual(app.options["windows"][0], {
            "name": "api", "root": str((self.root / "server").resolve()), "command": "python app.py",
        })
        self.assertEqual(app.options["windows"][1]["root"], str(self.root.resolve()))
        self.assertIsNone(app.options["windows"][1]["command"])

    def test_legacy_tmux_and_startup_translate_without_changing_identity(self) -> None:
        project = self.load('''
[tmux]
session = "work-existing"
presentation = "windows"
launch_mode = "instance"
reuse_existing = false
[[tmux.windows]]
name = "api"
root = "server"
command = "python app.py"
[startup]
command = "npm run dev"
window = "web"
check = "curl -f localhost:3000"
''', namespace="work")
        app = configured_apps(project)[0]
        self.assertEqual((app.name, app.extension, app.legacy_slot), ("tmux", "tmux", "tmux"))
        self.assertEqual(app.options["session"], "work-existing")
        self.assertEqual(app.options["startup_command"], project.startup.command)
        self.assertEqual(app.options["startup_window"], "web")
        self.assertEqual(app.options["startup_check"], project.startup.check)
        self.assertEqual(app.options["windows"][0]["root"], str((self.root / "server").resolve()))
        self.assertEqual((app.launch_mode, app.reuse_existing), ("instance", False))

    def test_legacy_default_session_preserves_work_config_namespace(self) -> None:
        app = configured_apps(self.load("", namespace="work"))[0]
        self.assertEqual(app.options["session"], "work-demo")

    def test_no_tmux_resource_when_apps_list_is_authoritative(self) -> None:
        for text in ('apps = []', '[[apps]]\nname = "editor"\nextension = "vscode"'):
            with self.subTest(text=text):
                project = self.load(text)
                self.assertFalse(project.tmux.enabled)
                self.assertFalse(any(app.extension == "tmux" for app in configured_apps(project)))

    def test_explicit_legacy_startup_opts_into_tmux(self) -> None:
        project = self.load('apps = []\n[startup]\ncommand = "npm run dev"')
        app, = configured_apps(project)
        self.assertTrue(project.tmux.enabled)
        self.assertEqual(app.extension, "tmux")
        self.assertEqual(app.options["startup_command"], "npm run dev")

    def test_legacy_startup_cannot_be_silently_dropped(self) -> None:
        for text in (
            '[tmux]\nenabled = false\n[startup]\ncommand = "npm run dev"',
            '[startup]\ncommand = "npm run dev"\n[[apps]]\nname = "services"\nextension = "tmux"',
            '[tmux]\nsession = "old"\n[[apps]]\nname = "services"\nextension = "tmux"',
        ):
            with self.subTest(text=text), self.assertRaises(WorkError):
                self.load(text)

    def test_disabled_legacy_tmux_can_coexist_with_explicit_extension(self) -> None:
        project = self.load('[tmux]\nenabled = false\n[[apps]]\nname = "services"\nextension = "tmux"')
        app, = configured_apps(project)
        self.assertTrue(app.enabled)
        self.assertIsNone(app.legacy_slot)

    def test_disabled_session_does_not_turn_implicit_services_on(self) -> None:
        project = self.load('[[apps]]\nname = "services"\nextension = "tmux"\nenabled = false')
        self.assertFalse(project.tmux.enabled)
        app, = configured_apps(project)
        self.assertFalse(app.enabled)

    def test_duplicate_enabled_session_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(WorkError, "duplicate enabled session identity"):
            self.load('''
[[apps]]
name = "api"
extension = "tmux"
[apps.options]
session = "shared"
presentation = "none"
[[apps]]
name = "web"
extension = "tmux"
[apps.options]
session = "shared"
presentation = "windows"
''')

    def test_only_one_enabled_foreground_session_allowed(self) -> None:
        entries = '''[[apps]]
name = "api"
extension = "tmux"
[[apps]]
name = "web"
extension = "tmux"
'''
        with self.assertRaisesRegex(WorkError, "only one enabled session"):
            self.load(entries)
        self.assertEqual(len(configured_apps(self.load(entries + "enabled = false\n"))), 2)
        self.assertEqual(len(configured_apps(self.load(entries + '[apps.options]\npresentation = "none"'))), 2)

    def test_session_rejects_window_matchers_and_malformed_options(self) -> None:
        for field in ('match_title = "*api*"', 'match_document = "/project/*"'):
            with self.subTest(field=field), self.assertRaisesRegex(WorkError, "window matching"):
                self.load('[[apps]]\nname = "services"\nextension = "tmux"\n' + field)
        for options in (
            'session = "bad:session"', 'presentation = "tabs"',
            'windows = ["api"]', 'windows = [{name = "api", extra = true}]',
            'windows = [{name = "api"}, {name = "api"}]',
            'windows = [{name = "api", root = false}]',
            'windows = [{name = "api", command = false}]',
        ):
            with self.subTest(options=options), self.assertRaises(WorkError):
                self.load('[[apps]]\nname = "services"\nextension = "tmux"\n[apps.options]\n' + options)

    def test_tmux_command_discovery_uses_the_extension_specification(self) -> None:
        from workspace_manager.extensions import load_extension

        declarations = {name: load_extension(name) for name in ("tmux", "vscode", "chrome")}
        declarations["tmux"].data["command"] = {"name": "alternate-tmux", "candidates": ["/custom/tmux"]}
        with patch("workspace_manager.config.load_extension", side_effect=declarations.__getitem__), \
                patch("workspace_manager.config._discover_command", side_effect=lambda name, candidates=(): name) as discover:
            commands = default_commands()
        self.assertEqual(commands.tmux, "alternate-tmux")
        discover.assert_any_call("alternate-tmux", ("/custom/tmux",))

    def test_booted_can_follow_enabled_resources_without_tmux(self) -> None:
        self.assertTrue(WorkspaceStatus("demo", None, None, None, None, resources_ready=True).booted)
        self.assertFalse(WorkspaceStatus("demo", True, None, None, None, resources_ready=False).booted)
        self.assertTrue(WorkspaceStatus("demo", True, None, None, None).booted)
        self.assertFalse(WorkspaceStatus("demo", None, None, None, None).booted)


if __name__ == "__main__":
    unittest.main()
