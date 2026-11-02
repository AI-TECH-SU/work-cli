from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.apps import ConfiguredApps
from workspace_manager.errors import WorkError
from workspace_manager.models import (
    AppConfig, ChromeConfig, ContextConfig, ProjectConfig, StartupConfig, TmuxConfig, VSCodeConfig,
)
from test_windows import WindowRunner, window


BUNDLE = "com.apple.TextEdit"


class AppRunner(WindowRunner):
    def __init__(self, rows):
        super().__init__(rows)
        self.launches = []
        self.appeared = []
        self.launch_code = 0

    def mutate(self, command, *, cwd=None, detached=False):
        self.launches.append((tuple(command), cwd, detached))
        if not self.dry_run:
            self.rows.extend(self.appeared)
            self.appeared = []
        return self.launch_code


class AppsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, XDG_STATE_HOME=str(self.root / "state"))
        env.start()
        self.addCleanup(env.stop)
        self.app = AppConfig("notes", BUNDLE, open=(str(self.root / "PROJECT.md"),))
        self.project = ProjectConfig("demo", self.root, TmuxConfig("demo", enabled=False), VSCodeConfig(False),
                                     ChromeConfig(False), ContextConfig(), StartupConfig(),
                                     self.root / "demo.toml", apps=(self.app,))
        self.runner = AppRunner([])

    def apps(self, app=None, *, desktop="target"):
        return ConfiguredApps("desktop-names", self.runner,
                              replace(self.project, apps=(app or self.app,)), desktop)

    def row(self, id=1, **kwargs):
        return window(id, bundle=BUNDLE, title=kwargs.pop("title", "PROJECT.md"), **kwargs)

    def test_default_commands_request_window_or_instance_with_document_operands(self):
        self.assertEqual(ConfiguredApps.launch_command(self.project, self.app),
                         ("/usr/bin/open", "-b", BUNDLE, str(self.root / "PROJECT.md")))
        self.assertEqual(ConfiguredApps.launch_command(self.project, replace(self.app, launch_mode="instance")),
                         ("/usr/bin/open", "-n", "-b", BUNDLE, str(self.root / "PROJECT.md")))

    def test_custom_commands_preserve_literal_arguments_and_run_from_project(self):
        app = replace(self.app, open=(), window_command=("./bin/my editor", "$HOME", "$(touch bad)", "a b"),
                      instance_command=("other-cli", "--instance"))
        self.runner.appeared = [self.row()]
        self.apps(app).restore()
        self.assertEqual(self.runner.launches, [((str(self.root / "bin/my editor"), "$HOME", "$(touch bad)", "a b"),
                                                 self.root, True)])
        self.assertEqual(ConfiguredApps.launch_command(self.project, replace(app, launch_mode="instance")),
                         ("other-cli", "--instance"))

    def test_unique_existing_match_moves_without_launching(self):
        self.runner.rows = [self.row(), self.row(2, title="other project")]
        self.apps(replace(self.app, match_title="PROJECT.md")).restore()
        self.assertEqual(self.runner.launches, [])
        self.assertEqual([row["spaces"] for row in self.runner.rows], [["target"], ["elsewhere"]])

    def test_no_matcher_ignores_old_window_then_reuses_recorded_identity(self):
        self.runner.rows = [self.row()]
        self.runner.appeared = [self.row(2)]
        apps = self.apps()
        apps.restore()
        self.runner.rows[1]["title"] = "changed active document"
        apps.restore()
        self.assertEqual(len(self.runner.launches), 1)
        self.assertEqual([row["spaces"] for row in self.runner.rows], [["elsewhere"], ["target"]])

    def test_generic_resource_named_chrome_does_not_adopt_by_desktop(self):
        self.runner.rows = [self.row(space="target")]
        self.runner.appeared = [self.row(2)]
        self.apps(replace(self.app, name="chrome")).restore()
        self.assertEqual(len(self.runner.launches), 1)

    def test_ambiguous_matches_fail_without_mutating(self):
        self.runner.rows = [self.row(), self.row(2)]
        apps = self.apps(replace(self.app, match_title="PROJECT.md"))
        with self.assertRaisesRegex(WorkError, "Several app:notes windows match"):
            apps.restore()
        self.assertEqual(self.runner.launches, [])
        self.assertFalse(apps.windows.path.exists())

    def test_title_and_decoded_document_match_are_both_required(self):
        app = replace(self.app, match_title="PROJECT*", match_document=str(self.root / "my notes/*"))
        row = self.row()
        row["document"] = (self.root / "my notes/PROJECT.md").as_uri()
        self.assertTrue(ConfiguredApps.matches(app, row))
        self.assertFalse(ConfiguredApps.matches(app, {**row, "title": "project.md"}))
        self.assertFalse(ConfiguredApps.matches(app, {**row, "document": "file:///another/PROJECT.md"}))
        url_app = replace(app, match_title=None, match_document="https://example.test/project/*")
        self.assertTrue(ConfiguredApps.matches(url_app, {**row, "document": "https://example.test/project/1"}))

    def test_changed_target_does_not_reuse_stale_saved_identity(self):
        self.runner.appeared = [self.row()]
        self.apps().restore()
        self.runner.rows[0]["spaces"] = ["elsewhere"]
        self.runner.appeared = [self.row(2, title="OTHER.md")]
        self.apps(replace(self.app, open=(str(self.root / "OTHER.md"),))).restore()
        self.assertEqual(len(self.runner.launches), 2)
        self.assertEqual(self.runner.rows[0]["spaces"], ["elsewhere"])

    def test_new_window_must_match_explicit_rules(self):
        self.runner.appeared = [self.row(title="restored unrelated window"), self.row(2)]
        self.apps(replace(self.app, match_title="PROJECT.md")).restore()
        self.assertEqual([row["spaces"] for row in self.runner.rows], [["elsewhere"], ["target"]])

    def test_changed_project_root_does_not_reuse_old_command_window(self):
        app = replace(self.app, open=(), window_command=("./bin/editor", "."))
        self.runner.appeared = [self.row()]
        self.apps(app).restore()
        self.runner.rows[0]["spaces"] = ["elsewhere"]
        new_root = self.root / "other project"
        new_root.mkdir()
        self.runner.appeared = [self.row(2)]
        changed = replace(self.project, root=new_root, apps=(app,))
        ConfiguredApps("desktop-names", self.runner, changed, "target").restore()
        self.assertEqual(len(self.runner.launches), 2)
        self.assertEqual(self.runner.launches[-1][0], (str(new_root / "bin/editor"), "."))
        self.assertEqual(self.runner.rows[0]["spaces"], ["elsewhere"])

    def test_unrelated_new_window_is_not_adopted(self):
        self.runner.appeared = [self.row(title="restored unrelated window")]
        with patch("workspace_manager.windows.time.monotonic", side_effect=[0, 20]):
            with self.assertRaisesRegex(WorkError, "No new app:notes window appeared"):
                self.apps(replace(self.app, match_title="PROJECT.md")).restore()
        self.assertEqual(self.runner.rows[0]["spaces"], ["elsewhere"])

    def test_no_desktop_opens_and_tracks_without_move(self):
        self.runner.appeared = [self.row()]
        apps = self.apps(desktop=None)
        apps.restore()
        apps.restore()
        self.assertEqual(len(self.runner.launches), 1)
        self.assertFalse(any(call[1] == "move-window" for call in self.runner.calls))
        self.assertEqual(self.runner.rows[0]["spaces"], ["elsewhere"])

    def test_disabled_reuse_keeps_old_window_and_retries_failed_new_move(self):
        self.runner.rows = [self.row()]
        self.runner.appeared = [self.row(2)]
        apps = self.apps(replace(self.app, reuse_existing=False, match_title="PROJECT.md"))
        self.runner.fail_move = True
        with self.assertRaisesRegex(WorkError, "move rejected"):
            apps.restore()
        self.runner.fail_move = False
        apps.restore()
        self.assertEqual(len(self.runner.launches), 1)
        self.assertEqual([row["spaces"] for row in self.runner.rows], [["elsewhere"], ["target"]])
        self.assertFalse(json.loads(apps.windows.path.read_text())["app:notes"]["pending_move"])

    def test_failed_launch_does_not_write_a_binding(self):
        self.runner.launch_code = 1
        apps = self.apps()
        with self.assertRaisesRegex(WorkError, "Could not launch configured app"):
            apps.restore()
        self.assertFalse(apps.windows.path.exists())

    def test_status_finds_windows_without_launching_moving_or_remembering(self):
        self.runner.rows = [self.row()]
        apps = self.apps(replace(self.app, reuse_existing=False, match_title="PROJECT.md"))
        self.assertEqual(apps.status(), {"notes": True})
        self.assertFalse(apps.windows.path.exists())
        self.assertEqual(self.runner.launches, [])
        self.assertFalse(any(call[1] == "move-window" for call in self.runner.calls))
        self.assertEqual(self.apps().status(), {"notes": False})

    def test_status_uses_saved_identity_only_for_current_configuration(self):
        self.runner.appeared = [self.row()]
        apps = self.apps()
        apps.restore()
        before = apps.windows.path.read_bytes()
        self.assertEqual(apps.status(), {"notes": True})
        self.assertEqual(apps.windows.path.read_bytes(), before)
        self.assertEqual(self.apps(replace(self.app, launch_mode="instance")).status(), {"notes": False})

    def test_status_reports_ambiguity_and_inventory_failure(self):
        self.runner.rows = [self.row(), self.row(2)]
        with self.assertRaisesRegex(WorkError, "Several app:notes"):
            self.apps(replace(self.app, match_title="PROJECT.md")).status()
        with patch.object(self.runner, "capture", side_effect=WorkError("inventory unavailable")):
            with self.assertRaisesRegex(WorkError, "inventory unavailable"):
                self.apps().status()

    def test_disabled_apps_never_inspect_or_launch(self):
        apps = self.apps(replace(self.app, enabled=False))
        apps.restore()
        self.assertEqual(apps.status(), {"notes": None})
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.runner.launches, [])

    def test_dry_run_does_not_inspect_or_save_window_state(self):
        self.runner.dry_run = True
        apps = self.apps()
        apps.restore()
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(apps.windows.path.exists())
        self.assertEqual(self.runner.launches[0][0], ConfiguredApps.launch_command(self.project, self.app))


if __name__ == "__main__":
    unittest.main()
