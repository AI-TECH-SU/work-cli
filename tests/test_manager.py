from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import (
    ChromeConfig,
    CommandConfig,
    ContextConfig,
    ProjectConfig,
    SpaceConfig,
    StartupConfig,
    TmuxConfig,
    TmuxWindowConfig,
    VSCodeConfig,
)


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str]] | None = None, dry_run: bool = False):
        self.responses = responses or {}
        self.dry_run = dry_run
        self.mutations: list[tuple[str, ...]] = []

    def capture(self, command, *, cwd=None):
        code, stdout = self.responses.get(tuple(command), (1, ""))
        return subprocess.CompletedProcess(command, code, stdout, "")

    def shell_capture(self, command, *, cwd):
        code, stdout = self.responses.get(("shell", command), (1, ""))
        return subprocess.CompletedProcess(command, code, stdout, "")

    def mutate(self, command, *, cwd=None, detached=False):
        self.mutations.append(tuple(command))
        return 0


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        workspace = root / "demo.code-workspace"
        context = root / "PROJECT.md"
        workspace.write_text("{}\n", encoding="utf-8")
        context.write_text("# demo\n", encoding="utf-8")
        self.project = ProjectConfig(
            name="demo",
            root=root,
            tmux=TmuxConfig(session="work-demo"),
            vscode=VSCodeConfig(True, workspace, "Demo"),
            chrome=ChromeConfig(True, "Profile 2", ("http://localhost:3000",)),
            context=ContextConfig(context, True),
            startup=StartupConfig("npm run dev", "dev", None),
            source=root / "demo.toml",
        )
        self.commands = CommandConfig("tmux", "code", "/Applications/Google Chrome")
        self.env = patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "state")})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_fresh_boot_creates_every_resource(self) -> None:
        runner = FakeRunner()
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(self.project)

        flattened = [" ".join(command) for command in runner.mutations]
        self.assertTrue(any("new-session" in command for command in flattened))
        self.assertTrue(any("respawn-pane" in command and "npm run dev" in command for command in flattened))
        self.assertTrue(any(command.startswith("code --new-window") for command in flattened))
        self.assertTrue(any("--profile-directory=Profile 2" in command for command in flattened))
        for executable in (self.commands.code, self.commands.chrome):
            launch = next(command for command in runner.mutations if command[0] == executable)
            self.assertIn("--new-window", launch)
            self.assertNotIn("--user-data-dir", launch)

    def test_healthy_workspace_does_not_duplicate_resources(self) -> None:
        responses = {
            ("tmux", "has-session", "-t", "=work-demo"): (0, ""),
            ("tmux", "list-windows", "-t", "=work-demo", "-F", "#{window_name}:#{window_dead}"): (0, "shell:0\ndev:0\n"),
            ("code", "--status"): (0, str(self.project.vscode.workspace)),
            ("/bin/ps", "-axo", "command="): (0, "/Applications/Google Chrome --profile-directory=Profile 2\n"),
        }
        runner = FakeRunner(responses)
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(self.project)

        self.assertEqual(runner.mutations, [])

    def test_partial_workspace_reopens_missing_vscode_only(self) -> None:
        responses = {
            ("tmux", "has-session", "-t", "=work-demo"): (0, ""),
            ("tmux", "list-windows", "-t", "=work-demo", "-F", "#{window_name}:#{window_dead}"): (0, "shell:0\ndev:0\n"),
            ("/bin/ps", "-axo", "command="): (0, "/Applications/Google Chrome --profile-directory=Profile 2\n"),
        }
        runner = FakeRunner(responses)
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(self.project)

        self.assertEqual(len(runner.mutations), 1)
        self.assertEqual(runner.mutations[0][0:2], ("code", "--new-window"))

    def test_dry_run_does_not_create_a_lock_file(self) -> None:
        runner = FakeRunner(dry_run=True)
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(self.project)

        self.assertFalse((Path(os.environ["XDG_STATE_HOME"]) / "focus").exists())

    @patch("sys.stdout", new_callable=StringIO)
    def test_space_dry_run_describes_desktop_without_mutating(self, stdout) -> None:
        project = replace(self.project, space=SpaceConfig(enabled=True))
        runner = FakeRunner(dry_run=True)
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(project)

        self.assertIn("plan Desktop Names: project desktop", stdout.getvalue())
        self.assertFalse(any(command[0] == "desktop-names" for command in runner.mutations))

    @patch("workspace_manager.manager.DesktopNamesAdapter.prepare")
    def test_desktop_failure_stops_before_project_mutations(self, prepare) -> None:
        from workspace_manager.errors import WorkError
        prepare.side_effect = WorkError("switch failed")
        runner = FakeRunner()
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        with self.assertRaisesRegex(WorkError, "switch failed"):
            manager.reconcile(replace(self.project, space=SpaceConfig(enabled=True)))
        self.assertEqual(runner.mutations, [])

    def test_fresh_boot_creates_configured_tmux_windows_in_their_roots(self) -> None:
        api = self.project.root / "api"
        web = self.project.root / "web"
        api.mkdir()
        web.mkdir()
        project = ProjectConfig(
            name=self.project.name,
            root=self.project.root,
            tmux=TmuxConfig(
                session="work-demo",
                windows=(
                    TmuxWindowConfig("api", api),
                    TmuxWindowConfig("web", web, "npm run dev"),
                ),
            ),
            vscode=self.project.vscode,
            chrome=self.project.chrome,
            context=self.project.context,
            startup=StartupConfig(),
            source=self.project.source,
        )
        runner = FakeRunner()
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(project)

        self.assertEqual(
            runner.mutations[0][0:9],
            ("tmux", "new-session", "-d", "-s", "work-demo", "-n", "api", "-c", str(api)),
        )
        retention = ("tmux", "set-window-option", "-t", "=work-demo:web", "remain-on-exit", "on")
        start = ("tmux", "respawn-pane", "-k", "-t", "=work-demo:web", "-c", str(web),
                 os.environ.get("SHELL", "/bin/zsh"), "-lc", "exec npm run dev")
        self.assertIn(retention, runner.mutations)
        self.assertIn(start, runner.mutations)
        self.assertLess(runner.mutations.index(retention), runner.mutations.index(start))

    def test_existing_session_repairs_only_missing_tmux_windows(self) -> None:
        api = self.project.root / "api"
        web = self.project.root / "web"
        api.mkdir()
        web.mkdir()
        project = ProjectConfig(
            name=self.project.name,
            root=self.project.root,
            tmux=TmuxConfig(
                session="work-demo",
                windows=(TmuxWindowConfig("api", api), TmuxWindowConfig("web", web)),
            ),
            vscode=VSCodeConfig(False),
            chrome=ChromeConfig(False),
            context=ContextConfig(None, False),
            startup=StartupConfig(),
            source=self.project.source,
        )
        responses = {
            ("tmux", "has-session", "-t", "=work-demo"): (0, ""),
            ("tmux", "list-windows", "-t", "=work-demo", "-F", "#{window_name}:#{window_dead}"): (0, "api:0\n"),
        }
        runner = FakeRunner(responses)
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(project)

        self.assertEqual(len(runner.mutations), 1)
        self.assertEqual(runner.mutations[0][6], "web")

    def test_dead_service_is_reported_without_claiming_it_is_running(self):
        project = replace(self.project, tmux=TmuxConfig('work-demo', (TmuxWindowConfig('api', self.project.root),)),
                          vscode=VSCodeConfig(False), chrome=ChromeConfig(False), startup=StartupConfig())
        runner = FakeRunner({
            ('tmux', 'has-session', '-t', '=work-demo'): (0, ''),
            ('tmux', 'list-windows', '-t', '=work-demo', '-F', '#{window_name}:#{window_dead}'): (0, 'api:1\n'),
        })
        with patch('sys.stdout', new_callable=StringIO) as output:
            WorkspaceManager(self.commands, runner, validate_executables=False).reconcile(project)
        self.assertIn("command in 'api' exited", output.getvalue())
        self.assertNotIn("window 'api' is running", output.getvalue())
        self.assertEqual(runner.mutations, [])

    def test_separate_vscode_instance_uses_project_data_directory(self):
        project = replace(self.project, vscode=replace(self.project.vscode, separate_instance=True))
        runner = FakeRunner(dry_run=True)
        WorkspaceManager(self.commands, runner, validate_executables=False).reconcile(project)
        launch = next(command for command in runner.mutations if command[0] == 'code')
        self.assertEqual(launch[1], '--user-data-dir')
        self.assertTrue(launch[2].endswith('/focus/vscode/demo'))
        self.assertIn('--new-window', launch)

    def test_instance_launch_policy_uses_separate_project_data_for_both_apps(self):
        project = replace(self.project, vscode=replace(self.project.vscode, launch_mode='instance'),
                          chrome=replace(self.project.chrome, launch_mode='instance'))
        runner = FakeRunner(dry_run=True)
        WorkspaceManager(self.commands, runner, validate_executables=False).reconcile(project)
        code = next(command for command in runner.mutations if command[0] == 'code')
        chrome = next(command for command in runner.mutations if command[0] == self.commands.chrome)
        self.assertEqual(code[1], '--user-data-dir')
        self.assertTrue(code[2].endswith('/focus/vscode/demo'))
        self.assertTrue(any(arg.startswith('--user-data-dir=') and arg.endswith('/focus/chrome/demo') for arg in chrome))
        self.assertIn('--profile-directory=Profile 2', chrome)

    @patch('workspace_manager.manager.ProjectWindows')
    @patch('workspace_manager.manager.DesktopNamesAdapter.prepare', return_value='desktop-a')
    def test_desktop_restore_passes_each_apps_reuse_policy(self, prepare, windows):
        project = replace(self.project, space=SpaceConfig(enabled=True),
                          vscode=replace(self.project.vscode, reuse_existing=False),
                          chrome=replace(self.project.chrome, reuse_existing=True))
        WorkspaceManager(self.commands, FakeRunner(), validate_executables=False).reconcile(project)
        calls = windows.return_value.ensure.call_args_list
        self.assertEqual([(call.args[0], call.kwargs['reuse_existing']) for call in calls],
                         [('vscode', False), ('chrome', True)])

    def test_no_desktop_reuse_false_opens_new_windows_even_when_apps_detected(self):
        project = replace(self.project, vscode=replace(self.project.vscode, reuse_existing=False),
                          chrome=replace(self.project.chrome, reuse_existing=False, reopen_on_restore=False))
        runner = FakeRunner({
            ('tmux', 'has-session', '-t', '=work-demo'): (0, ''),
            ('tmux', 'list-windows', '-t', '=work-demo', '-F', '#{window_name}:#{window_dead}'): (0, 'shell:0\ndev:0\n'),
        })
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        with patch('workspace_manager.extensions.ResolvedExtension.running', return_value=True):
            manager.reconcile(project)
        for command in (self.commands.code, self.commands.chrome):
            self.assertTrue(any(call[0] == command and '--new-window' in call for call in runner.mutations))

    @patch('workspace_manager.manager.DesktopNamesAdapter.switch_existing', return_value=True)
    @patch('workspace_manager.terminals.TerminalWindows.check_access')
    def test_focusing_existing_desktop_only_switches_even_without_terminal_access(self, access, switch):
        project = replace(self.project, space=SpaceConfig(enabled=True),
                          tmux=replace(self.project.tmux, presentation='windows', reuse_existing=False),
                          vscode=replace(self.project.vscode, launch_mode='instance', reuse_existing=False),
                          chrome=replace(self.project.chrome, launch_mode='instance', reuse_existing=False))
        runner = FakeRunner()
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        with patch.object(manager, 'reconcile') as reconcile:
            self.assertEqual(manager.focus(project), 0)
        access.assert_not_called()
        reconcile.assert_not_called()
        self.assertEqual(runner.mutations, [])

    @patch('workspace_manager.manager.DesktopNamesAdapter.switch_existing', return_value=False)
    @patch('workspace_manager.terminals.TerminalWindows.check_access')
    def test_denied_terminal_access_precedes_setup_and_restart(self, access, switch):
        from workspace_manager.errors import WorkError
        access.side_effect = WorkError('Automation denied')
        project = replace(self.project, space=SpaceConfig(enabled=True),
                          tmux=replace(self.project.tmux, presentation='windows'))
        runner = FakeRunner()
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        for restart in (False, True):
            with patch.object(manager, 'reconcile') as reconcile, patch.object(manager, '_stop_services') as stop:
                with self.assertRaisesRegex(WorkError, 'Automation denied'):
                    manager.focus(project, restart=restart)
                reconcile.assert_not_called()
                stop.assert_not_called()
        self.assertEqual(runner.mutations, [])

    @patch('workspace_manager.manager.DesktopNamesAdapter')
    @patch('workspace_manager.manager.ProjectWindows')
    def test_stop_save_prompt_preserves_services_and_desktop(self, windows, adapter):
        from workspace_manager.errors import WorkError
        adapter.return_value.find.return_value = 'desktop-a'
        windows.return_value.close_on_desktop.side_effect = WorkError('Save prompt')
        manager = WorkspaceManager(self.commands, FakeRunner(), validate_executables=False)
        with patch.object(manager, '_stop_services') as stop:
            with self.assertRaisesRegex(WorkError, 'Save prompt'):
                manager.stop(replace(self.project, space=SpaceConfig(enabled=True)))
        stop.assert_not_called()
        adapter.return_value.remove.assert_not_called()
        windows.return_value.path.unlink.assert_not_called()

    @patch('workspace_manager.manager.DesktopNamesAdapter')
    @patch('workspace_manager.manager.ProjectWindows')
    def test_stop_removes_desktop_only_after_windows_close_and_services_stop(self, windows, adapter):
        events = []
        adapter.return_value.find.return_value = 'desktop-a'
        windows.return_value.close_on_desktop.side_effect = lambda **kw: events.append('terminals' if 'include_bundles' in kw else 'apps')
        windows.return_value.require_empty.side_effect = lambda: events.append('empty')
        adapter.return_value.remove.side_effect = lambda *args: events.append('remove')
        manager = WorkspaceManager(self.commands, FakeRunner(), validate_executables=False)
        with patch.object(manager, '_stop_services', side_effect=lambda *a, **kw: events.append('services')):
            manager.stop(replace(self.project, space=SpaceConfig(enabled=True)))
        self.assertEqual(events, ['apps', 'services', 'terminals', 'empty', 'remove'])

    @patch('workspace_manager.manager.DesktopNamesAdapter')
    @patch('workspace_manager.manager.ProjectWindows')
    def test_stop_without_dedicated_desktop_only_stops_project_services(self, windows, adapter):
        for space in (SpaceConfig(), SpaceConfig(enabled=True, target='current')):
            with self.subTest(space=space):
                runner = FakeRunner({('tmux', 'has-session', '-t', '=work-demo'): (0, '')})
                manager = WorkspaceManager(self.commands, runner, validate_executables=False)
                manager.stop(replace(self.project, space=space))
                self.assertEqual(runner.mutations, [('tmux', 'kill-session', '-t', '=work-demo')])
        windows.assert_not_called()
        adapter.assert_not_called()

    @patch('workspace_manager.manager.DesktopNamesAdapter')
    @patch('workspace_manager.manager.ProjectWindows')
    def test_restart_stops_services_without_closing_windows_or_removing_desktop(self, windows, adapter):
        runner = FakeRunner({('tmux', 'has-session', '-t', '=work-demo'): (0, '')})
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        project = replace(self.project, space=SpaceConfig(enabled=True))
        with patch.object(manager, 'reconcile') as reconcile:
            self.assertEqual(manager.focus(project, restart=True, no_attach=True), 0)
        self.assertEqual(runner.mutations, [('tmux', 'kill-session', '-t', '=work-demo')])
        reconcile.assert_called_once_with(project, force_gui=False, assume_stopped=False, lock_held=True)
        windows.assert_not_called()
        adapter.return_value.find.assert_not_called()
        adapter.return_value.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
