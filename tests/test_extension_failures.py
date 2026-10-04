from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.apps import ConfiguredApps
from workspace_manager.cli import command_doctor
from workspace_manager.config import load_project, toml_quote
from workspace_manager.errors import WorkError
from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import CommandConfig


class InventoryRunner:
    dry_run = False

    def __init__(self):
        self.rows = []
        self.captures = []
        self.mutations = []

    def capture(self, command, *, cwd=None):
        command = tuple(command)
        self.captures.append(command)
        if command == ("/bin/ps", "-axo", "command="):
            output = "/Applications/Google Chrome --profile-directory=Profile 2\n"
        elif command == ("desktop-names", "windows", "com.google.Chrome"):
            output = json.dumps(self.rows)
        elif command == ("/usr/sbin/sysctl", "-n", "kern.boottime"):
            output = "{ sec = 100, usec = 0 }"
        else:
            raise AssertionError(f"Unexpected capture: {command}")
        return subprocess.CompletedProcess(command, 0, output, "")

    def mutate(self, command, *, cwd=None, detached=False):
        command = tuple(command)
        self.mutations.append(command)
        if command[0] == "/Applications/Google Chrome":
            self.rows.append(dict(id=1, pid=42, bundle="com.google.Chrome", title="Project Demo",
                                  document="https://example.test/demo", spaces=["current"]))
        else:
            raise AssertionError(f"Unexpected mutation: {command}")
        return 0


class ExtensionFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config_home = self.root / "config" / "focus"
        self.projects = self.config_home / "projects"
        self.projects.mkdir(parents=True)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.root / "config"),
                         XDG_STATE_HOME=str(self.root / "state"))
        env.start()
        self.addCleanup(env.stop)
        self.commands = CommandConfig("tmux", "code", "/Applications/Google Chrome", "desktop-names")

    def load(self, name, settings):
        (self.projects / f"{name}.toml").write_text(
            f"version = 1\nroot = {toml_quote(str(self.root))}\n{settings}\n",
            encoding="utf-8",
        )
        return load_project(name)

    def test_restart_rejects_missing_extension_path_before_stopping_services(self):
        project = self.load("demo", '''[[apps]]
name = "editor"
extension = "vscode"
[apps.options]
workspace = "missing.code-workspace"
''')
        runner = InventoryRunner()
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        with patch.object(manager, "_stop_services", wraps=manager._stop_services) as stop:
            with self.assertRaisesRegex(WorkError, "workspace path does not exist"):
                manager.focus(project, restart=True, no_attach=True)
        stop.assert_not_called()
        self.assertEqual(runner.captures, [])
        self.assertEqual(runner.mutations, [])

    def test_doctor_continues_after_selected_extension_template_cannot_resolve(self):
        extensions = self.config_home / "extensions"
        extensions.mkdir()
        (extensions / "broken.toml").write_text('''version = 1
id = "broken"
bundle_id = "com.example.Editor"
[command]
name = "editor"
[inputs.document]
type = "string"
[launch.window]
argv = ["{command}", "{document}"]
[launch.instance]
argv = ["{command}", "--new-instance"]
''', encoding="utf-8")
        # The definition and options are valid; the selected window command
        # requires an absent optional scalar without guarding it in an if group.
        self.load("a-broken", '[[apps]]\nname = "editor"\nextension = "broken"')
        self.load("b-healthy", '[[apps]]\nname = "notes"\nbundle_id = "com.example.Notes"')
        output = io.StringIO()
        with patch("workspace_manager.cli.load_commands", return_value=self.commands), \
                patch("workspace_manager.cli.sys.platform", "darwin"), \
                patch("workspace_manager.cli.executable_exists", return_value=True), \
                redirect_stdout(output):
            result = command_doctor()
        self.assertEqual(result, 1)
        self.assertIn("ERR project configuration a-broken:", output.getvalue())
        self.assertIn("requires a scalar", output.getvalue())
        self.assertNotIn("ok  project configuration: a-broken", output.getvalue())
        self.assertIn("ok  project configuration: b-healthy", output.getvalue())
        self.assertIn("ok  app notes executable for b-healthy: /usr/bin/open", output.getvalue())

    def test_custom_matchers_use_inventory_even_when_extension_process_is_running(self):
        for matcher in ('match_title = "Project Demo"',
                        'match_document = "https://example.test/demo"'):
            with self.subTest(matcher=matcher):
                project = self.load("demo", f'''[[apps]]
name = "browser"
extension = "chrome"
{matcher}
[apps.options]
profile = "Profile 2"
urls = ["https://example.test/demo"]
''')
                runner = InventoryRunner()
                apps = ConfiguredApps("desktop-names", runner, project, commands=self.commands)
                self.assertFalse(project.space.enabled)
                self.assertTrue(apps.resources[0].extension.running(runner, "window"))
                runner.captures.clear()

                self.assertIn(("Desktop Names", "desktop-names"), apps.required_commands())
                self.assertEqual(apps.status(), {"browser": False})
                self.assertEqual(runner.captures, [("desktop-names", "windows", "com.google.Chrome")])
                self.assertEqual(runner.mutations, [])

                apps.restore()

                self.assertEqual(len(runner.mutations), 1)
                self.assertEqual(runner.mutations[0][0], self.commands.chrome)
                self.assertEqual(apps.status(), {"browser": True})
                self.assertNotIn(("/bin/ps", "-axo", "command="), runner.captures)
                self.assertFalse(any(command[1:2] == ("move-window",) for command in runner.captures))
                # The next subtest starts with no live window or persisted binding.
                apps.windows.path.unlink()


if __name__ == "__main__":
    unittest.main()
