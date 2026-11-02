from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from workspace_manager.config import load_project, toml_quote
from workspace_manager.errors import WorkError
from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import AppConfig, CommandConfig, ContextConfig, SpaceConfig
from workspace_manager.windows import ProjectWindows


class AppManagerRunner:
    dry_run = False

    def __init__(self, project):
        self.project = project
        self.running = False
        self.rows = []
        self.captures = []
        self.mutations = []
        self.desktops = [
            dict(uuid="other", name="Other", active=True, regular=True),
            dict(uuid="project", name=project.name, active=False, regular=True),
        ]

    def window(self, identity=1, *, space="other"):
        return dict(id=identity, pid=42, bundle="com.example.Notes", title="Demo notes",
                    document=(self.project.root / "notes.md").as_uri(), spaces=[space])

    def capture(self, command, *, cwd=None):
        command = tuple(command)
        self.captures.append(command)
        if command == ("/usr/sbin/sysctl", "-n", "kern.boottime"):
            code, output = 0, "{ sec = 100, usec = 0 }"
        elif command[:2] == ("tmux", "has-session"):
            code, output = (0 if self.running else 1), ""
        elif command[:2] == ("tmux", "list-sessions"):
            code, output = 0, ""
        elif command == ("desktop-names", "list"):
            code, output = 0, json.dumps(self.desktops)
        elif command[:2] == ("desktop-names", "windows"):
            rows = [row for row in self.rows if len(command) == 2 or row["bundle"] == command[2]]
            code, output = 0, json.dumps(rows)
        elif command[:2] == ("desktop-names", "move-window"):
            row = next(row for row in self.rows if str(row["id"]) == command[2])
            row["spaces"] = [command[5]]
            code, output = 0, "ok"
        else:
            raise AssertionError(f"Unexpected capture: {command}")
        return subprocess.CompletedProcess(command, code, output, "")

    def mutate(self, command, *, cwd=None, detached=False):
        command = tuple(command)
        self.mutations.append(command)
        if command[:2] == ("tmux", "new-session"):
            self.running = True
        elif command[:2] == ("tmux", "kill-session"):
            self.running = False
        elif command[:2] == ("desktop-names", "switch"):
            for desktop in self.desktops:
                desktop["active"] = desktop["uuid"] == command[2]
        elif command[:2] == ("desktop-names", "rename"):
            next(desktop for desktop in self.desktops if desktop["uuid"] == command[2])["name"] = command[3]
        elif command[0] == "/usr/bin/open":
            self.rows.append(self.window(len(self.rows) + 1))
        else:
            raise AssertionError(f"Unexpected mutation: {command}")
        return 0


class AppsManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        env = patch.dict(os.environ, XDG_STATE_HOME=str(self.root / "state"),
                         XDG_CONFIG_HOME=str(self.root / "config"))
        env.start()
        self.addCleanup(env.stop)
        directory = self.root / "config/focus/projects"
        directory.mkdir(parents=True)
        (directory / "demo.toml").write_text(
            f"version = 1\nroot = {toml_quote(str(self.root))}\n"
            '[vscode]\nenabled = false\n[chrome]\nenabled = false\n'
            '[context]\nopen_in_vscode = false\n'
            '[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"\nopen = ["notes.md"]\n',
            encoding="utf-8",
        )
        self.project = load_project("demo")
        self.commands = CommandConfig("tmux", "code", "chrome", "desktop-names")

    def test_generic_only_reconcile_launches_then_reuses_a_verified_window_without_desktop(self):
        runner = AppManagerRunner(self.project)
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)

        first = manager.reconcile(self.project)
        second = manager.reconcile(self.project)

        self.assertEqual(first.apps, {"notes": True})
        self.assertEqual(second.apps, {"notes": True})
        self.assertIsNone(second.vscode)
        self.assertIsNone(second.chrome)
        launches = [command for command in runner.mutations if command[0] == "/usr/bin/open"]
        self.assertEqual(launches, [("/usr/bin/open", "-b", "com.example.Notes", str(self.root / "notes.md"))])
        self.assertEqual(len(runner.rows), 1)
        self.assertEqual(runner.rows[0]["spaces"], ["other"])
        self.assertFalse(any(command[:2] == ("desktop-names", "move-window") for command in runner.captures))
        binding = ProjectWindows("desktop-names", runner, self.project, None).path
        self.assertEqual(json.loads(binding.read_text())["app:notes"]["id"], 1)

    def test_generic_only_reconcile_moves_a_matching_window_to_the_project_desktop(self):
        app = replace(self.project.apps[0], match_title="Demo *")
        project = replace(self.project, space=SpaceConfig(enabled=True), apps=(app,))
        runner = AppManagerRunner(project)
        runner.rows.append(runner.window())

        status = WorkspaceManager(self.commands, runner, validate_executables=False).reconcile(project)

        self.assertEqual(status.apps, {"notes": True})
        self.assertEqual(runner.rows[0]["spaces"], ["project"])
        self.assertIn(("desktop-names", "move-window", "1", "42", "com.example.Notes", "project"), runner.captures)
        self.assertFalse(any(command[0] == "/usr/bin/open" for command in runner.mutations))

    def test_generic_only_reconcile_does_not_require_disabled_vscode_context(self):
        context = ContextConfig(self.root / "PROJECT.md")
        project = replace(self.project, context=context)
        self.assertTrue(project.context.open_in_vscode)
        self.assertFalse(project.context.path.exists())
        runner = AppManagerRunner(project)

        status = WorkspaceManager(self.commands, runner, validate_executables=False).reconcile(project)

        self.assertEqual(status.apps, {"notes": True})
        self.assertFalse(project.context.path.exists())

    def test_selected_launcher_is_required_before_any_side_effect(self):
        variants = (
            (AppConfig("notes", "com.example.Notes"), "/usr/bin/open"),
            (AppConfig("notes", "com.example.Notes", window_command=("window-launcher",),
                       instance_command=("unused-launcher",)), "window-launcher"),
            (AppConfig("notes", "com.example.Notes", launch_mode="instance",
                       window_command=("unused-launcher",), instance_command=("./bin/instance-launcher",)),
             str(self.root / "bin/instance-launcher")),
        )
        for app, selected in variants:
            with self.subTest(mode=app.launch_mode, selected=selected):
                project = replace(self.project, apps=(app,))
                runner = AppManagerRunner(project)
                with patch("workspace_manager.manager.executable_exists", side_effect=lambda value: value != selected) as exists:
                    with self.assertRaisesRegex(WorkError, "required executable not found.*app notes"):
                        WorkspaceManager(self.commands, runner).reconcile(project)
                self.assertEqual([call.args[0] for call in exists.call_args_list], ["desktop-names", selected])
                self.assertEqual(runner.captures, [])
                self.assertEqual(runner.mutations, [])

    def test_generic_app_requires_desktop_names_even_when_desktop_management_is_disabled(self):
        runner = AppManagerRunner(self.project)
        with patch("workspace_manager.manager.executable_exists", side_effect=lambda command: command != "desktop-names"):
            with self.assertRaisesRegex(WorkError, "Desktop Names: desktop-names"):
                WorkspaceManager(self.commands, runner).reconcile(self.project)
        self.assertEqual(runner.captures, [])
        self.assertEqual(runner.mutations, [])

    def test_disabled_generic_apps_do_not_require_launchers_or_window_inventory(self):
        app = replace(self.project.apps[0], enabled=False, window_command=("missing-launcher",), open=())
        project = replace(self.project, apps=(app,))
        runner = AppManagerRunner(project)
        with patch("workspace_manager.manager.executable_exists", return_value=True) as exists:
            status = WorkspaceManager(self.commands, runner).reconcile(project)
        self.assertEqual([call.args[0] for call in exists.call_args_list], [])
        self.assertEqual(status.apps, {"notes": None})
        self.assertFalse(any(command[0] == "desktop-names" for command in runner.captures + runner.mutations))

    def test_refocus_only_switches_without_restoring_apps_or_checking_launchers(self):
        app = replace(self.project.apps[0], reuse_existing=False, launch_mode="instance")
        project = replace(self.project, space=SpaceConfig(enabled=True), apps=(app,))
        runner = AppManagerRunner(project)
        with patch("workspace_manager.manager.ConfiguredApps.restore") as restore, \
                patch("workspace_manager.manager.executable_exists") as exists:
            result = WorkspaceManager(self.commands, runner).focus(project, no_attach=True)
        self.assertEqual(result, 0)
        restore.assert_not_called()
        exists.assert_not_called()
        self.assertEqual(runner.captures, [("desktop-names", "list")])
        self.assertEqual(runner.mutations, [("desktop-names", "switch", "project")])

    def test_stop_without_desktop_preserves_generic_windows_and_bindings(self):
        runner = AppManagerRunner(self.project)
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        manager.reconcile(self.project)
        binding = ProjectWindows("desktop-names", runner, self.project, None).path
        original_binding = binding.read_bytes()
        original_windows = json.dumps(runner.rows)
        runner.captures.clear()
        runner.mutations.clear()

        manager.stop(self.project)

        self.assertFalse(runner.running)
        self.assertEqual(binding.read_bytes(), original_binding)
        self.assertEqual(json.dumps(runner.rows), original_windows)
        self.assertEqual(runner.mutations, [])
        self.assertEqual(runner.captures, [])


if __name__ == "__main__":
    unittest.main()
