from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.cli import command_doctor
from workspace_manager.config import load_project, toml_quote
from workspace_manager.errors import WorkError
from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import CommandConfig


class Runner:
    dry_run = False

    def __init__(self, responses=None):
        self.responses = {('/usr/sbin/sysctl', '-n', 'kern.boottime'): (0, '{ sec = 100, usec = 0 }'),
                          **(responses or {})}
        self.calls = []
        self.mutations = []

    def capture(self, argv, **kwargs):
        argv = tuple(argv)
        self.calls.append(argv)
        code, output = self.responses.get(argv, (1, ''))
        return subprocess.CompletedProcess(argv, code, output, '')

    def shell_capture(self, command, **kwargs):
        return self.capture(('shell', command))

    def mutate(self, argv, **kwargs):
        self.mutations.append(tuple(argv))
        return 0


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {
            'XDG_CONFIG_HOME': str(self.root / 'config'),
            'XDG_STATE_HOME': str(self.root / 'state'),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.commands = CommandConfig('tmux', 'code', 'chrome', 'desktop-names')

    def project(self, content):
        directory = self.root / 'config/focus/projects'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'demo.toml').write_text(f'version = 1\nroot = {toml_quote(str(self.root))}\n{content}\n')
        return load_project('demo')

    def test_empty_and_disabled_sessions_never_check_launch_or_attach_dependencies(self):
        for content in ('apps = []', '[[apps]]\nname = "services"\nextension = "tmux"\nenabled = false'):
            with self.subTest(content=content):
                project = self.project(content)
                runner = Runner()
                with patch('workspace_manager.manager.executable_exists') as exists, \
                        patch('workspace_manager.terminals.TerminalWindows.check_access') as access, \
                        patch('workspace_manager.session_extensions.run_interactive') as attach:
                    manager = WorkspaceManager(self.commands, runner)
                    self.assertEqual(manager.focus(project), 0)
                    status = manager.status(project)
                    manager.stop(project)
                self.assertFalse(status.booted)
                self.assertIsNone(status.tmux)
                self.assertEqual(runner.calls, [])
                self.assertEqual(runner.mutations, [])
                exists.assert_not_called()
                access.assert_not_called()
                attach.assert_not_called()

    def test_editor_only_focus_and_stop_use_no_session_dependency(self):
        project = self.project('[[apps]]\nname = "editor"\nextension = "vscode"')
        runner = Runner()
        manager = WorkspaceManager(self.commands, runner)
        with patch('workspace_manager.manager.executable_exists', return_value=True) as exists, \
                patch('workspace_manager.terminals.TerminalWindows.check_access') as access, \
                patch('workspace_manager.session_extensions.run_interactive') as attach:
            self.assertEqual(manager.focus(project), 0)
            self.assertEqual([argv[0] for argv in runner.mutations], ['code'])
            runner.responses[('code', '--status')] = (0, str(self.root))
            status = manager.status(project)
            manager.stop(project)
        self.assertTrue(status.booted)
        self.assertEqual(status.apps, {'editor': True})
        self.assertIsNone(status.tmux)
        self.assertEqual({call.args[0] for call in exists.call_args_list}, {'code'})
        self.assertEqual({argv[0] for argv in runner.calls}, {'code'})
        self.assertEqual(len(runner.mutations), 1)
        access.assert_not_called()
        attach.assert_not_called()

    def test_explicit_session_reports_failed_startup_readiness(self):
        project = self.project('''[[apps]]
name = "services"
extension = "tmux"
[apps.options]
session = "demo"
presentation = "none"
startup_command = "serve"
startup_check = "healthy"
''')
        runner = Runner({('tmux', 'has-session', '-t', '=demo'): (0, ''), ('shell', 'healthy'): (1, '')})
        status = WorkspaceManager(self.commands, runner).status(project)
        self.assertEqual(status.apps, {'services': False})
        self.assertFalse(status.booted)
        self.assertIsNone(status.tmux)
        self.assertEqual(runner.mutations, [])

    def test_apps_only_first_launch_is_remembered_without_reopening_closed_browser(self):
        project = self.project('''[[apps]]
name = "browser"
extension = "chrome"
[apps.options]
reopen_on_restore = false
''')
        runner = Runner()
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        manager.focus(project)
        self.assertEqual(len(runner.mutations), 1)
        self.assertEqual(runner.mutations[0][0], 'chrome')
        manager.focus(project)
        manager.stop(project)
        manager.focus(project)
        self.assertEqual(len(runner.mutations), 1)
        manager.focus(project, force_gui=True)
        self.assertEqual(len(runner.mutations), 2)

    def test_apps_only_first_launch_marker_tracks_changed_options(self):
        project = self.project('[[apps]]\nname = "browser"\nextension = "chrome"\n[apps.options]\nreopen_on_restore = false')
        runner = Runner()
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        manager.focus(project)
        manager.focus(project)
        self.assertEqual(len(runner.mutations), 1)
        changed = replace(project.apps[0], options={**project.apps[0].options, 'urls': ['https://example.org']})
        manager.focus(replace(project, apps=(changed,)))
        self.assertEqual(len(runner.mutations), 2)
        self.assertIn('https://example.org', runner.mutations[1])

    def test_apps_only_reboot_allows_initial_launch_again(self):
        project = self.project('[[apps]]\nname = "browser"\nextension = "chrome"\n[apps.options]\nreopen_on_restore = false')
        runner = Runner()
        manager = WorkspaceManager(self.commands, runner, validate_executables=False)
        manager.focus(project)
        manager.focus(project)
        self.assertEqual(len(runner.mutations), 1)
        runner.responses[('/usr/sbin/sysctl', '-n', 'kern.boottime')] = (0, '{ sec = 200, usec = 0 }')
        manager.focus(project)
        self.assertEqual(len(runner.mutations), 2)

    def test_apps_only_dry_run_does_not_write_initial_launch_marker(self):
        project = self.project('[[apps]]\nname = "browser"\nextension = "chrome"')
        runner = Runner()
        runner.dry_run = True
        WorkspaceManager(self.commands, runner, validate_executables=False).focus(project)
        self.assertFalse((self.root / 'state').exists())

    def test_restart_checks_missing_extension_before_stopping_services(self):
        project = self.project('[[apps]]\nname = "services"\nextension = "tmux"')
        runner = Runner()
        manager = WorkspaceManager(self.commands, runner)
        with patch('workspace_manager.manager.executable_exists', return_value=False), \
                patch.object(manager, '_stop_services') as stop:
            with self.assertRaisesRegex(WorkError, 'required executable not found'):
                manager.focus(project, restart=True, no_attach=True)
        stop.assert_not_called()
        self.assertEqual(runner.calls, [])
        self.assertEqual(runner.mutations, [])

    def test_doctor_fix_installs_only_enabled_declared_dependencies(self):
        enabled = self.project('[[apps]]\nname = "services"\nextension = "tmux"')
        disabled = self.project('[[apps]]\nname = "services"\nextension = "tmux"\nenabled = false')
        empty = self.project('apps = []')
        for projects, expected in (({'disabled': disabled, 'empty': empty}, 0),
                                   ({'demo': enabled, 'second': replace(enabled, name='second'),
                                     'disabled': disabled}, 1)):
            with self.subTest(projects=list(projects)), \
                    patch('workspace_manager.cli.load_commands', return_value=self.commands), \
                    patch('workspace_manager.cli.list_project_names', return_value=list(projects)), \
                    patch('workspace_manager.cli.load_project', side_effect=projects.__getitem__), \
                    patch('workspace_manager.cli.sys.platform', 'darwin'), \
                    patch('workspace_manager.cli.executable_exists', side_effect=lambda command: command != 'tmux') as exists, \
                    patch('workspace_manager.cli.repair_dependency', return_value=True) as repair, \
                    redirect_stdout(io.StringIO()):
                result = command_doctor(fix=True, assume_yes=False)
            self.assertEqual(repair.call_count, expected)
            self.assertEqual(result, expected)  # A failed post-install lookup must remain an error.
            if expected:
                repair.assert_called_once_with(command='tmux', package='tmux', assume_yes=False)
            else:
                exists.assert_not_called()


if __name__ == '__main__':
    unittest.main()
