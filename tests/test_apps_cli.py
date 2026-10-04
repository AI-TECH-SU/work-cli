from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from workspace_manager.cli import command_doctor, command_status
from workspace_manager.errors import WorkError
from workspace_manager.models import (
    AppConfig,
    ChromeConfig,
    CommandConfig,
    ContextConfig,
    ProjectConfig,
    StartupConfig,
    TmuxConfig,
    VSCodeConfig,
    WorkspaceStatus,
)


class AppsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.commands = CommandConfig("tmux", "code", "chrome", "desktop-names")
        self.project = ProjectConfig(
            name="demo", root=Path("/projects/demo"), tmux=TmuxConfig("demo"),
            vscode=VSCodeConfig(enabled=False), chrome=ChromeConfig(enabled=False),
            context=ContextConfig(), startup=StartupConfig(), source=Path("/config/demo.toml"),
            apps=(AppConfig("notes", "com.example.Notes"),),
        )

    def doctor(self, projects, *, unavailable=()):
        output = io.StringIO()

        def load(name):
            value = projects[name]
            if isinstance(value, WorkError):
                raise value
            return value

        with patch("workspace_manager.cli.load_commands", return_value=self.commands), \
                patch("workspace_manager.cli.list_project_names", return_value=list(projects)), \
                patch("workspace_manager.cli.load_project", side_effect=load), \
                patch("workspace_manager.cli.sys.platform", "darwin"), \
                patch("workspace_manager.cli.executable_exists",
                      side_effect=lambda command: command not in unavailable) as exists, \
                redirect_stdout(output):
            result = command_doctor()
        return result, output.getvalue(), [call.args[0] for call in exists.call_args_list]

    def test_generic_only_project_does_not_require_disabled_builtins(self):
        result, output, checked = self.doctor({"demo": self.project}, unavailable=("code", "chrome"))
        self.assertEqual(result, 0)
        self.assertNotIn("code", checked)
        self.assertNotIn("chrome", checked)
        self.assertIn("desktop-names", checked)
        self.assertIn("/usr/bin/open", checked)
        self.assertIn("ok  app notes executable for demo: /usr/bin/open", output)

    def test_doctor_requires_helper_for_generic_apps_without_desktop(self):
        result, output, _ = self.doctor({"demo": self.project}, unavailable=("desktop-names",))
        self.assertFalse(self.project.space.enabled)
        self.assertEqual(result, 1)
        self.assertIn("ERR Desktop Names executable for demo", output)

    def test_doctor_checks_only_the_selected_mode_command_relative_to_project(self):
        app = replace(self.project.apps[0], launch_mode="instance",
                      window_command=("unused-window-command",),
                      instance_command=("./bin/notes", "--profile", "literal value"))
        project = replace(self.project, apps=(app,))
        result, output, checked = self.doctor({"demo": project}, unavailable=("/projects/demo/bin/notes",))
        self.assertEqual(result, 1)
        self.assertIn("/projects/demo/bin/notes", checked)
        self.assertNotIn("unused-window-command", checked)
        self.assertNotIn("/usr/bin/open", checked)
        self.assertIn("ERR app notes executable for demo", output)

    def test_disabled_generic_apps_require_neither_helper_nor_launch_command(self):
        app = replace(self.project.apps[0], enabled=False, window_command=("missing-app",))
        result, _, checked = self.doctor({"demo": replace(self.project, apps=(app,))},
                                         unavailable=("desktop-names", "missing-app"))
        self.assertEqual(result, 0)
        self.assertNotIn("desktop-names", checked)
        self.assertNotIn("missing-app", checked)

    def test_doctor_derives_builtin_requirements_across_projects(self):
        editor = replace(self.project, name="editor", apps=(), vscode=VSCodeConfig(enabled=True))
        browser = replace(self.project, name="browser", apps=(), chrome=ChromeConfig(enabled=True))
        result, _, checked = self.doctor({"demo": self.project, "editor": editor, "browser": browser},
                                         unavailable=("code", "chrome"))
        self.assertEqual(result, 1)
        self.assertEqual(checked.count("code"), 1)
        self.assertEqual(checked.count("chrome"), 1)

    def test_doctor_without_projects_preserves_builtin_checks(self):
        result, output, checked = self.doctor({}, unavailable=("code",))
        self.assertEqual(result, 1)
        self.assertIn("code", checked)
        self.assertIn("chrome", checked)
        self.assertIn("no projects configured", output)

    def test_invalid_project_does_not_hide_other_project_checks(self):
        result, output, checked = self.doctor({"broken": WorkError("invalid apps entry"), "demo": self.project})
        self.assertEqual(result, 1)
        self.assertIn("ERR project configuration broken: invalid apps entry", output)
        self.assertIn("ok  project configuration: demo", output)
        self.assertIn("desktop-names", checked)

    def status_output(self, status, *, as_json):
        output = io.StringIO()
        with patch("workspace_manager.cli.load_commands", return_value=self.commands), \
                patch("workspace_manager.cli.load_project", return_value=self.project), \
                patch("workspace_manager.cli.WorkspaceManager") as manager, redirect_stdout(output):
            manager.return_value.status.return_value = status
            self.assertEqual(command_status("demo", as_json=as_json), 0)
            manager.return_value.status.assert_called_once_with(self.project)
        return output.getvalue()

    def test_json_status_includes_generic_window_states(self):
        status = WorkspaceStatus("demo", True, None, None, None,
                                 apps={"notes": True, "design": False, "chat": None})
        payload = json.loads(self.status_output(status, as_json=True))
        self.assertEqual(payload["apps"], {"notes": True, "design": False, "chat": None})
        self.assertTrue(payload["booted"])

    def test_text_status_lists_resource_states(self):
        status = WorkspaceStatus("demo", True, None, None, None,
                                 apps={"notes": True, "design": False, "chat": None})
        output = self.status_output(status, as_json=False)
        self.assertIn("PROJECT\tTMUX\tSTARTUP\tVSCODE\tCHROME\n", output)
        self.assertIn("PROJECT\tAPP\tSTATE\n", output)
        self.assertIn("demo\tnotes\trunning\n", output)
        self.assertIn("demo\tdesign\tstopped\n", output)
        self.assertIn("demo\tchat\tdisabled\n", output)

    def test_existing_text_status_unchanged_without_generic_apps(self):
        output = self.status_output(WorkspaceStatus("demo", True, None, None, None), as_json=False)
        self.assertEqual(output, "PROJECT\tTMUX\tSTARTUP\tVSCODE\tCHROME\n"
                         "demo\trunning\tdisabled\tdisabled\tdisabled\n")


if __name__ == "__main__":
    unittest.main()
