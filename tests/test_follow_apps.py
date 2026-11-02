from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

import test_apps_manager as fixtures
from workspace_manager.config import load_follow_apps
from workspace_manager.desktop import DesktopNamesAdapter
from workspace_manager.errors import WorkError
from workspace_manager.follow_apps import FollowApps
from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import FollowAppConfig, SpaceConfig
from workspace_manager.paths import global_config_path
from workspace_manager.windows import ProjectWindows


class FollowAppsTests(unittest.TestCase):
    setUp = fixtures.AppsManagerTests.setUp

    def follower(self, **changes):
        return replace(FollowAppConfig("notes", "com.example.Notes"), **changes)

    def test_config_defaults_and_validation(self):
        self.assertEqual(load_follow_apps(), ())
        path = global_config_path()
        path.write_text('version = 1\n[[follow_apps]]\nname = "chat"\nbundle_id = "com.example.Chat"\n'
                        'enabled = false\nmatch_title = "*Main*"\n')
        self.assertEqual(load_follow_apps(), (FollowAppConfig("chat", "com.example.Chat", False, "*Main*"),))
        for text in ('follow_apps = "chat"', '[[follow_apps]]\nname = "chat"',
                     '[[follow_apps]]\nname = "chat"\nbundle_id = "*"',
                     '[[follow_apps]]\nname = "chat"\nbundle_id = "com.example.Chat"\nenabled = "yes"',
                     '[[follow_apps]]\nname = "chat"\nbundle_id = "com.example.Chat"\nlaunch = true'):
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(WorkError):
                    load_follow_apps()

    def test_existing_desktop_switch_moves_all_matching_windows_without_launch(self):
        project = replace(self.project, space=SpaceConfig(enabled=True))
        runner = fixtures.AppManagerRunner(project)
        runner.rows = [runner.window(1), runner.window(2)]
        with patch("workspace_manager.manager.ConfiguredApps.restore") as restore:
            WorkspaceManager(self.commands, runner, follow_apps=(self.follower(),)).focus(project)
        restore.assert_not_called()
        self.assertEqual([row["spaces"] for row in runner.rows], [["project"], ["project"]])
        self.assertEqual(runner.mutations, [("desktop-names", "switch", "project")])
        self.assertFalse(ProjectWindows("desktop-names", runner, project, "project").path.exists())

    def test_repair_also_moves_followers(self):
        project = replace(self.project, space=SpaceConfig(enabled=True), apps=())
        runner = fixtures.AppManagerRunner(project)
        runner.rows = [runner.window()]
        WorkspaceManager(self.commands, runner, validate_executables=False,
                         follow_apps=(self.follower(),)).focus(project, force_gui=True, no_attach=True)
        self.assertEqual(runner.rows[0]["spaces"], ["project"])

    def test_filters_disabled_duplicate_and_all_desktop_windows(self):
        runner = fixtures.AppManagerRunner(self.project)
        runner.rows = [runner.window(1), dict(runner.window(2), title="popup"),
                       dict(runner.window(3), spaces=["other", "project"])]
        windows = ProjectWindows("desktop-names", runner, self.project, "project")
        FollowApps((self.follower(match_title="Demo *"), self.follower(match_title="Demo *"),
                    self.follower(enabled=False))).move(windows)
        self.assertEqual([row["spaces"] for row in runner.rows],
                         [["project"], ["other"], ["other", "project"]])
        self.assertEqual(sum(cmd[1] == "move-window" for cmd in runner.captures), 1)

    def test_absent_apps_are_not_launched(self):
        runner = fixtures.AppManagerRunner(self.project)
        FollowApps((self.follower(),)).move(ProjectWindows("desktop-names", runner, self.project, "project"))
        self.assertEqual(runner.mutations, [])

    def test_move_failure_is_reported(self):
        runner = fixtures.AppManagerRunner(self.project)
        runner.rows = [runner.window()]
        windows = ProjectWindows("desktop-names", runner, self.project, "project")
        original = runner.capture
        def no_move(command, **kwargs):
            if command[1] == "move-window":
                import subprocess
                return subprocess.CompletedProcess(command, 0, "", "")
            return original(command, **kwargs)
        runner.capture = no_move
        with self.assertRaisesRegex(WorkError, "not confirmed"):
            FollowApps((self.follower(),)).move(windows)

    def test_stop_preserves_shared_windows_before_cleanup(self):
        project = replace(self.project, space=SpaceConfig(enabled=True), apps=())
        runner = fixtures.AppManagerRunner(project)
        runner.rows = [runner.window(space="project")]
        manager = WorkspaceManager(self.commands, runner, follow_apps=(self.follower(),))
        def close(**kwargs):
            self.assertEqual(runner.rows[0]["spaces"], ["other"])
        with patch.object(ProjectWindows, "close_on_desktop", side_effect=close) as cleanup, \
                patch.object(DesktopNamesAdapter, "remove") as remove:
            manager.stop(project)
        cleanup.assert_called_once()
        remove.assert_called_once()

    def test_no_safe_desktop_prevents_stop_cleanup(self):
        project = replace(self.project, space=SpaceConfig(enabled=True), apps=())
        runner = fixtures.AppManagerRunner(project)
        runner.desktops = [dict(uuid="project", name=project.name, active=True, regular=True)]
        runner.rows = [runner.window(space="project")]
        with patch.object(ProjectWindows, "close_on_desktop") as cleanup:
            with self.assertRaisesRegex(WorkError, "No other regular desktop"):
                WorkspaceManager(self.commands, runner, follow_apps=(self.follower(),)).stop(project)
        cleanup.assert_not_called()

    def test_dry_run_and_disabled_desktop_do_not_move_windows(self):
        runner = fixtures.AppManagerRunner(self.project)
        runner.dry_run = True
        windows = ProjectWindows("desktop-names", runner, self.project, None)
        with redirect_stdout(io.StringIO()) as output:
            FollowApps((self.follower(),)).move(windows)
        self.assertIn("plan move", output.getvalue())
        self.assertEqual(runner.captures, [])
        runner.dry_run = False
        runner.rows = [runner.window()]
        project = replace(self.project, apps=())
        WorkspaceManager(self.commands, runner, validate_executables=False,
                         follow_apps=(self.follower(),)).focus(project, no_attach=True)
        self.assertEqual(runner.rows[0]["spaces"], ["other"])
