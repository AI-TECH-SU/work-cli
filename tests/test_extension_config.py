from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_manager.app_compat import configured_apps
from workspace_manager.config import load_project, render_project_config, toml_quote
from workspace_manager.errors import WorkError


class ExtensionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.config_home = self.base / "config" / "focus"
        self.directory = self.config_home / "projects"
        self.directory.mkdir(parents=True)
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "config")})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def load(self, settings: str):
        (self.directory / "demo.toml").write_text(
            f"version = 1\nroot = {toml_quote(str(self.root))}\n{settings}\n",
            encoding="utf-8",
        )
        return load_project("demo")

    def test_generated_project_uses_extension_resources(self) -> None:
        text = render_project_config(
            name="demo", root=self.root, workspace=self.root / "Demo.code-workspace",
            vscode_profile="Demo", chrome_profile="Profile 2", urls=["https://example.test"],
            context=self.root / "AGENT.md", startup="npm run dev", desktop=True,
        )
        data = tomllib.loads(text)
        self.assertTrue({"vscode", "chrome", "context", "tmux", "startup"}.isdisjoint(data))
        self.assertEqual([app["extension"] for app in data["apps"]], ["tmux", "vscode", "chrome"])
        self.assertEqual(data["apps"][0]["options"], {
            "session": "focus-demo", "presentation": "current", "startup_window": "dev",
            "startup_command": "npm run dev",
        })
        self.assertEqual(data["apps"][1]["options"], {
            "workspace": "Demo.code-workspace", "profile": "Demo", "context": "AGENT.md",
        })
        (self.directory / "demo.toml").write_text(text, encoding="utf-8")
        project = load_project("demo")
        self.assertFalse(project.vscode.enabled)
        self.assertFalse(project.chrome.enabled)
        self.assertFalse(project.tmux.enabled)
        self.assertEqual(len(configured_apps(project)), 3)
        self.assertTrue(project.space.enabled)

    def test_generated_disabled_browser_stays_disabled(self) -> None:
        text = render_project_config(
            name="demo", root=self.root, workspace=None, vscode_profile=None,
            chrome_profile=None, urls=[], context=None, startup=None,
        )
        (self.directory / "demo.toml").write_text(text, encoding="utf-8")
        services, vscode, chrome = configured_apps(load_project("demo"))
        self.assertTrue(services.enabled)
        self.assertTrue(vscode.enabled)
        self.assertFalse(chrome.enabled)

    def test_legacy_and_explicit_options_resolve_equally(self) -> None:
        legacy = configured_apps(self.load('''
[vscode]
workspace = "Demo.code-workspace"
profile = "Demo"
separate_instance = true
reuse_existing = false
[context]
path = "AGENT.md"
[chrome]
profile = "Profile 2"
urls = ["https://example.test"]
reopen_on_restore = false
'''))
        explicit = configured_apps(self.load('''
[[apps]]
name = "vscode"
extension = "vscode"
launch_mode = "instance"
reuse_existing = false
[apps.options]
workspace = "Demo.code-workspace"
profile = "Demo"
context = "AGENT.md"
[[apps]]
name = "chrome"
extension = "chrome"
[apps.options]
profile = "Profile 2"
urls = ["https://example.test"]
reopen_on_restore = false
'''))
        legacy = tuple(app for app in legacy if app.extension != "tmux")
        for old, new in zip(legacy, explicit):
            with self.subTest(app=old.name):
                self.assertEqual(old.options, new.options)
                self.assertEqual((old.extension, old.launch_mode, old.reuse_existing),
                                 (new.extension, new.launch_mode, new.reuse_existing))
                self.assertEqual(old.legacy_slot, old.name)
                self.assertIsNone(new.legacy_slot)
        self.assertFalse(legacy[1].restore_missing)

    def test_legacy_context_is_optional_and_not_duplicated(self) -> None:
        for settings in ('[context]\nopen_in_vscode = false',
                         '[vscode]\nworkspace = "PROJECT.md"\n[context]\npath = "PROJECT.md"'):
            with self.subTest(settings=settings):
                app = next(app for app in configured_apps(self.load(settings)) if app.extension == "vscode")
                self.assertIsNone(app.options["context"])

    def test_explicit_apps_list_suppresses_all_implicit_defaults(self) -> None:
        project = self.load('[[apps]]\nname = "editor"\nextension = "vscode"')
        self.assertFalse(project.vscode.enabled)
        self.assertFalse(project.chrome.enabled)
        self.assertFalse(project.tmux.enabled)
        self.assertEqual([app.name for app in configured_apps(project)], ["editor"])
        self.assertEqual(project.apps[0].options["workspace"], str(self.root.resolve()))

    def test_multiple_instances_of_one_extension_are_allowed(self) -> None:
        project = self.load('''
[chrome]
enabled = false
[[apps]]
name = "editor"
extension = "vscode"
[[apps]]
name = "docs"
extension = "vscode"
[apps.options]
workspace = "docs"
''')
        self.assertEqual([app.name for app in configured_apps(project)], ["editor", "docs"])

    def test_explicit_disabled_extension_suppresses_implicit_default(self) -> None:
        project = self.load('[[apps]]\nname = "browser"\nextension = "chrome"\nenabled = false')
        self.assertFalse(project.chrome.enabled)
        self.assertFalse(configured_apps(project)[-1].enabled)

    def test_explicit_enabled_legacy_and_extension_conflict(self) -> None:
        for extension in ("vscode", "chrome"):
            for legacy in ("", "enabled = true"):
                with self.subTest(extension=extension, legacy=legacy):
                    with self.assertRaisesRegex(WorkError, "configure the same app"):
                        self.load(f'[{extension}]\n{legacy}\n[[apps]]\nname = "custom"\nextension = "{extension}"')
            project = self.load(f'[{extension}]\nenabled = false\n[[apps]]\nname = "custom"\nextension = "{extension}"')
            self.assertTrue(project.apps[0].enabled)

    def test_plain_app_is_authoritative_and_names_remain_unique(self) -> None:
        project = self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"')
        self.assertEqual([app.name for app in configured_apps(project)], ["notes"])
        with self.assertRaisesRegex(WorkError, "duplicate app name.*vscode"):
            self.load('[vscode]\nenabled = true\n[[apps]]\nname = "vscode"\nbundle_id = "com.example.Notes"')

    def test_empty_apps_list_disables_defaults_but_explicit_legacy_tables_opt_in(self) -> None:
        self.assertEqual(configured_apps(self.load('apps = []')), ())
        project = self.load('apps = []\n[chrome]\nprofile = "Profile 2"')
        app, = configured_apps(project)
        self.assertEqual(app.extension, "chrome")
        self.assertEqual(app.options["profile"], "Profile 2")

    def test_invalid_extension_ids_and_unknown_extensions_fail_at_load(self) -> None:
        for value in ('""', '"../editor"', '"two words"', 'true'):
            with self.subTest(value=value), self.assertRaises(WorkError):
                self.load(f'[[apps]]\nname = "editor"\nextension = {value}')
        with self.assertRaisesRegex(WorkError, "unknown extension"):
            self.load('[[apps]]\nname = "editor"\nextension = "does-not-exist"')

    def test_extension_options_are_strict_even_for_disabled_resources(self) -> None:
        for settings in ('profile = true', 'workspace = ["folder"]', 'unknown = "value"'):
            with self.subTest(settings=settings), self.assertRaises(WorkError):
                self.load('[[apps]]\nname = "editor"\nextension = "vscode"\nenabled = false\n'
                          '[apps.options]\n' + settings)
        with self.assertRaisesRegex(WorkError, "options must be a TOML table"):
            self.load('[[apps]]\nname = "editor"\nextension = "vscode"\noptions = "invalid"')
        with self.assertRaisesRegex(WorkError, "URL schemes"):
            self.load('[[apps]]\nname = "browser"\nextension = "chrome"\n'
                      '[apps.options]\nurls = ["file:///tmp/index.html"]')

    def test_extension_launch_definition_cannot_be_overridden_inline(self) -> None:
        for setting in ('bundle_id = "com.example.Editor"', 'open = []',
                        'window_command = ["editor"]', 'instance_command = ["editor"]'):
            with self.subTest(setting=setting), self.assertRaisesRegex(WorkError, "extension cannot be combined"):
                self.load('[[apps]]\nname = "editor"\nextension = "vscode"\n' + setting)
        with self.assertRaisesRegex(WorkError, "options requires an extension"):
            self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\n[apps.options]')

    def test_resource_matchers_and_policy_are_independent_of_extension_options(self) -> None:
        project = self.load('''[[apps]]
name = "editor"
extension = "vscode"
launch_mode = "instance"
reuse_existing = false
match_title = "*Demo*"
match_document = "/project/docs/*"
''')
        app = project.apps[0]
        self.assertEqual((app.launch_mode, app.reuse_existing), ("instance", False))
        self.assertEqual((app.match_title, app.match_document), ("*Demo*", "/project/docs/*"))

    def test_custom_extension_loads_without_core_app_definition(self) -> None:
        directory = self.config_home / "extensions"
        directory.mkdir()
        (directory / "editor.toml").write_text('''version = 1
id = "editor"
bundle_id = "com.example.Editor"
[command]
name = "editor"
[inputs.file]
type = "path"
default = "{root}"
[launch.window]
argv = ["{command}", "--new-window", "{file}"]
[launch.instance]
argv = ["{command}", "--new-instance", "{file}"]
''', encoding="utf-8")
        project = self.load('''[vscode]
enabled = false
[chrome]
enabled = false
[[apps]]
name = "writing"
extension = "editor"
[apps.options]
file = "My draft.txt"
''')
        app, = configured_apps(project)
        self.assertEqual(app.extension, "editor")
        self.assertEqual(app.options["file"], str((self.root / "My draft.txt").resolve()))


if __name__ == "__main__":
    unittest.main()
