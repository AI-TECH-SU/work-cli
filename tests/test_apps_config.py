from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_manager.config import load_project, toml_quote
from workspace_manager.errors import WorkError


class AppsConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def load(self, settings: str):
        directory = self.root / "config" / "focus" / "projects"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "demo.toml").write_text(
            f"version = 1\nroot = {toml_quote(str(self.project_root))}\n{settings}\n",
            encoding="utf-8",
        )
        return load_project("demo")

    def test_apps_are_optional_and_preserve_builtin_defaults(self) -> None:
        project = self.load("")
        self.assertEqual(project.apps, ())
        self.assertTrue(project.vscode.enabled)
        self.assertTrue(project.chrome.enabled)

    def test_minimal_app_uses_window_reuse_without_requiring_a_desktop(self) -> None:
        project = self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"')
        app, = project.apps
        self.assertEqual(app.name, "notes")
        self.assertEqual(app.bundle_id, "com.example.Notes")
        self.assertTrue(app.enabled)
        self.assertEqual(app.launch_mode, "window")
        self.assertTrue(app.reuse_existing)
        self.assertEqual((app.open, app.window_command, app.instance_command), ((), (), ()))
        self.assertIsNone(app.match_title)
        self.assertIsNone(app.match_document)
        self.assertFalse(project.space.enabled)

    def test_app_choices_are_independent_and_names_are_not_cli_keywords(self) -> None:
        project = self.load('''[[apps]]
name = "stop"
bundle_id = "com.example.Notes"
launch_mode = "instance"
reuse_existing = false
enabled = false
[[apps]]
name = "notes-2"
bundle_id = "com.example.Notes"
''')
        first, second = project.apps
        self.assertEqual((first.launch_mode, first.reuse_existing, first.enabled), ("instance", False, False))
        self.assertEqual((second.launch_mode, second.reuse_existing, second.enabled), ("window", True, True))

    def test_open_targets_resolve_paths_but_preserve_app_uris(self) -> None:
        targets = ["notes/Planning doc.md", "~/focus notes.md", "obsidian://open?vault=Demo",
                   "https://example.test/project?name=Demo", "file:///tmp/Demo%20notes.md"]
        project = self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\n'
                            f"open = [{', '.join(map(toml_quote, targets))}]")
        self.assertEqual(project.apps[0].open, (
            str((self.project_root / targets[0]).resolve()),
            str(Path(targets[1]).expanduser().resolve()),
            *targets[2:],
        ))

    def test_commands_and_matchers_preserve_literal_content(self) -> None:
        project = self.load('''[[apps]]
name = "editor"
bundle_id = "com.example.Editor"
window_command = ["editor", "--new-window", "a b", "$(not-a-shell)", " leading "]
instance_command = ["editor", "--new-instance"]
match_title = "Demo * — Editor"
match_document = "file:///project/[ab]*.md"
''')
        app, = project.apps
        self.assertEqual(app.window_command, ("editor", "--new-window", "a b", "$(not-a-shell)", " leading "))
        self.assertEqual(app.instance_command, ("editor", "--new-instance"))
        self.assertEqual(app.match_title, "Demo * — Editor")
        self.assertEqual(app.match_document, "file:///project/[ab]*.md")

    def test_open_conflicts_only_with_the_selected_launch_command(self) -> None:
        for mode in ("window", "instance"):
            with self.subTest(mode=mode):
                base = ('[[apps]]\nname = "editor"\nbundle_id = "com.example.Editor"\n'
                        f'launch_mode = "{mode}"\nopen = ["notes.md"]\n')
                with self.assertRaisesRegex(WorkError, f"cannot be combined with {mode}_command"):
                    self.load(base + f'{mode}_command = ["editor"]')
                other = "instance" if mode == "window" else "window"
                self.assertTrue(self.load(base + f'{other}_command = ["editor"]').apps[0].open)

    def test_apps_must_be_an_array_of_tables(self) -> None:
        for settings in ('apps = "notes"', 'apps = ["notes"]', '[apps]\nname = "notes"'):
            with self.subTest(settings=settings), self.assertRaisesRegex(WorkError, "array of tables"):
                self.load(settings)

    def test_identity_is_required_even_for_command_launches(self) -> None:
        for settings in ('bundle_id = "com.example.Notes"', 'name = "notes"',
                         'name = "notes"\nwindow_command = ["notes"]',
                         'name = "notes"\nbundle_id = " "',
                         'name = "notes"\nbundle_id = true',
                         'name = 2\nbundle_id = "com.example.Notes"'):
            with self.subTest(settings=settings), self.assertRaises(WorkError):
                self.load('[[apps]]\n' + settings)

    def test_unsafe_and_duplicate_names_are_rejected(self) -> None:
        for name in ("", "../notes", "notes/editor", "two words", "-notes", ".notes"):
            with self.subTest(name=name), self.assertRaisesRegex(WorkError, "name must start"):
                self.load(f'[[apps]]\nname = {toml_quote(name)}\nbundle_id = "com.example.Notes"')
        with self.assertRaisesRegex(WorkError, "duplicate app name"):
            self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\n'
                      '[[apps]]\nname = "notes"\nbundle_id = "com.example.Other"')

    def test_unknown_app_settings_are_rejected(self) -> None:
        with self.assertRaisesRegex(WorkError, "unknown setting.*launch_mod"):
            self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\nlaunch_mod = "instance"')

    def test_boolean_and_launch_mode_types_are_strict(self) -> None:
        for setting in ('enabled = "false"', 'reuse_existing = 1', 'launch_mode = false',
                        'launch_mode = "tab"', 'launch_mode = " "'):
            with self.subTest(setting=setting), self.assertRaises(WorkError):
                self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\n' + setting)

    def test_open_and_commands_require_string_arrays(self) -> None:
        for key in ("open", "window_command", "instance_command"):
            for value in ('"notes"', '[true]', '["valid", ""]', '[" "]'):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(WorkError, "array of non-empty strings"):
                    self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\n'
                              f"{key} = {value}")
        for key in ("window_command", "instance_command"):
            with self.subTest(key=key), self.assertRaisesRegex(WorkError, "cannot be empty"):
                self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\n' + f"{key} = []")

    def test_matchers_must_identify_a_project(self) -> None:
        for key in ("match_title", "match_document"):
            for value in ('true', '""', '" "', '"*"', '"?*?"', '"[!*?]"'):
                with self.subTest(key=key, value=value), self.assertRaises(WorkError):
                    self.load('[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\n'
                              f"{key} = {value}")


if __name__ == "__main__":
    unittest.main()
