from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.apps import ConfiguredApps
from workspace_manager.cli import command_doctor
from workspace_manager.config import load_project, toml_quote
from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import AppConfig, CommandConfig, SpaceConfig
from test_apps_manager import AppManagerRunner


SPEC = '''version = 1
id = "project-notes"
bundle_id = "com.example.Notes"
require_match = true
[command]
name = "/usr/bin/open"
[inputs.document]
type = "path"
required = true
[inputs.title]
type = "string"
default = "Demo notes"
[launch.window]
argv = ["{command}", "-b", "com.example.Notes", "{document}"]
[launch.instance]
argv = ["{command}", "-n", "-b", "com.example.Notes", "{document}"]
[match]
kind = "glob"
title = "{title}"
document = "{document}"
'''


class ExtensionRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.root / "config"),
                         XDG_STATE_HOME=str(self.root / "state"))
        env.start()
        self.addCleanup(env.stop)
        self.extensions = self.root / "config/focus/extensions"
        self.extensions.mkdir(parents=True)
        (self.extensions / "project-notes.toml").write_text(SPEC)
        projects = self.extensions.parent / "projects"
        projects.mkdir()
        (projects / "demo.toml").write_text(
            f'version = 1\nroot = {toml_quote(str(self.root))}\n'
            '[[apps]]\nname = "notes"\nextension = "project-notes"\n'
            '[apps.options]\ndocument = "notes.md"\n')
        self.project = load_project("demo")
        self.commands = CommandConfig("tmux", "code", "chrome", "desktop-names")

    def test_third_app_spec_runs_through_manager_without_a_core_handler(self):
        runner = AppManagerRunner(self.project)
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        self.assertEqual(manager.reconcile(self.project).apps, {"notes": True})
        self.assertEqual(manager.reconcile(self.project).apps, {"notes": True})
        launches = [command for command in runner.mutations if command[0] == "/usr/bin/open"]
        self.assertEqual(launches, [("/usr/bin/open", "-b", "com.example.Notes", str(self.root / "notes.md"))])
        self.assertFalse(any(command[0] in {"code", "chrome"} for command in runner.mutations + runner.captures))

    def test_third_app_existing_window_moves_without_launching(self):
        project = replace(self.project, space=SpaceConfig(enabled=True))
        runner = AppManagerRunner(project)
        runner.rows = [runner.window()]
        status = WorkspaceManager(self.commands, runner, validate_executables=False).reconcile(project)
        self.assertEqual(status.apps, {"notes": True})
        self.assertEqual(runner.rows[0]["spaces"], ["project"])
        self.assertFalse(any(command[0] == "/usr/bin/open" for command in runner.mutations))

    def test_third_app_instance_launch_uses_its_specification(self):
        app = replace(self.project.apps[0], launch_mode="instance", reuse_existing=False)
        project = replace(self.project, apps=(app,))
        runner = AppManagerRunner(project)
        WorkspaceManager(self.commands, runner, validate_executables=False).reconcile(project)
        self.assertIn(("/usr/bin/open", "-n", "-b", "com.example.Notes", str(self.root / "notes.md")), runner.mutations)

    def test_two_resources_do_not_claim_each_others_matching_windows(self):
        first = self.project.apps[0]
        second = replace(first, name="other-notes")
        project = replace(self.project, apps=(first, second), space=SpaceConfig(enabled=True))
        runner = AppManagerRunner(project)
        runner.rows = [runner.window()]
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        manager.reconcile(project)
        bindings = ConfiguredApps("desktop-names", runner, project, "project", commands=self.commands).windows.path
        saved = json.loads(bindings.read_text())
        self.assertNotEqual(saved["app:notes"]["id"], saved["app:other-notes"]["id"])
        self.assertEqual(len(runner.rows), 2)
        manager.reconcile(project)
        self.assertEqual(len(runner.rows), 2)

    def test_multiple_browser_resources_disable_sole_window_adoption(self):
        apps = (AppConfig("one", extension="chrome"), AppConfig("two", extension="chrome"))
        project = replace(self.project, apps=apps, space=SpaceConfig(enabled=True))
        runner = AppManagerRunner(project)
        resources = ConfiguredApps("desktop-names", runner, project, "project", commands=self.commands)
        with patch.object(resources.windows, "ensure") as ensure:
            resources.restore()
        self.assertEqual(len(ensure.call_args_list), 2)
        self.assertTrue(all(not call.kwargs["adopt_single_on_desktop"] for call in ensure.call_args_list))

    def test_plain_app_saved_fingerprint_survives_extension_upgrade(self):
        import hashlib
        app = AppConfig("notes", "com.example.Notes", open=(str(self.root / "notes.md"),))
        project = replace(self.project, apps=(app,))
        runner = AppManagerRunner(project)
        runner.rows = [runner.window()]
        resources = ConfiguredApps("desktop-names", runner, project, commands=self.commands)
        old_data = {key: getattr(app, key) for key in (
            "bundle_id", "launch_mode", "open", "window_command", "instance_command", "match_title", "match_document")}
        old_data["root"] = str(project.root.resolve())
        old_hash = hashlib.sha256(json.dumps(old_data, sort_keys=True).encode()).hexdigest()
        resources.windows.remember("app:notes", runner.rows[0], binding_context=old_hash)
        resources.restore()
        self.assertEqual(runner.mutations, [])

    def test_doctor_validates_custom_extension_selected_launcher_and_inventory(self):
        with patch("workspace_manager.cli.load_commands", return_value=self.commands), \
                patch("workspace_manager.cli.executable_exists", return_value=True) as exists, \
                patch("workspace_manager.cli.sys.platform", "darwin"), patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(command_doctor(), 0)
        self.assertEqual([call.args[0] for call in exists.call_args_list], ["desktop-names", "/usr/bin/open"])

    def test_explicit_matcher_prevents_loose_desktop_adoption_from_spec(self):
        # A project-level matcher narrows the extension's own adoption policy.
        app = AppConfig("browser", extension="chrome", options={"urls": []}, match_title="project page")
        project = replace(self.project, space=SpaceConfig(enabled=True), apps=(app,))
        runner = AppManagerRunner(project)
        row = runner.window(space="project")
        row.update(bundle="com.google.Chrome", title="unrelated page")
        runner.rows = [row]
        apps = ConfiguredApps("desktop-names", runner, project, "project", commands=self.commands)
        with patch.object(apps.windows, "ensure") as ensure:
            apps.restore()
        self.assertFalse(ensure.call_args.kwargs["adopt_single_on_desktop"])
        self.assertTrue(ensure.call_args.kwargs["require_match"])

    def test_legacy_saved_identity_is_upgraded_without_reopening(self):
        from workspace_manager.models import ChromeConfig, ContextConfig, VSCodeConfig
        project = replace(self.project, apps=(), chrome=ChromeConfig(False),
                          vscode=VSCodeConfig(workspace=self.root), context=ContextConfig(),
                          space=SpaceConfig(enabled=True))
        runner = AppManagerRunner(project)
        row = runner.window()
        row.update(bundle="com.microsoft.VSCode", title="Changed document")
        runner.rows = [row]
        apps = ConfiguredApps("desktop-names", runner, project, "project", commands=self.commands)
        apps.windows.remember("vscode", row)
        apps.restore()
        self.assertEqual(runner.mutations, [])
        self.assertEqual(row["spaces"], ["project"])
        self.assertIn("context", json.loads(apps.windows.path.read_text())["vscode"])


if __name__ == "__main__":
    unittest.main()
