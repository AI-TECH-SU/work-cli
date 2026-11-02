import hashlib
import os
from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from workspace_manager.models import (ProjectConfig, TmuxConfig, TmuxWindowConfig, VSCodeConfig,
                                      ChromeConfig, ContextConfig, StartupConfig)
from workspace_manager.errors import WorkError
from workspace_manager.terminals import OPEN_WINDOW_SCRIPT, TerminalWindows


class Runner:
    dry_run = False

    def __init__(self):
        self.groups = {}
        self.opened = []
        self.selected = []
        self.mutations = []
        self.automation_allowed = True

    def capture(self, command, *, cwd=None):
        if command[0] == '/usr/bin/osascript':
            if not self.automation_allowed:
                return subprocess.CompletedProcess(command, 1, '', 'Not authorized (-1743)')
            if command[2] == OPEN_WINDOW_SCRIPT:
                self.opened.append(command)
                session = command[-2].split()[-1]
                self.groups[session]['attached'] = 1
                return subprocess.CompletedProcess(command, 0, 'opened\n', '')
            return subprocess.CompletedProcess(command, 0, '2.14', '')
        _, action, *args = command
        target = args[args.index('-t') + 1].lstrip('=') if '-t' in args else None
        code, value = 0, ''
        if action == 'has-session': code = 0 if target in self.groups else 1
        elif action == 'show-options':
            key = 'owner' if args[-1] == '@focus-parent' else 'mode'
            value = self.groups[target].get(key, '')
        elif action == 'display-message': value = str(self.groups[target]['attached'])
        elif action == 'list-sessions': value = '\n'.join(name+'\t'+row['owner'] for name, row in self.groups.items())
        return subprocess.CompletedProcess(command, code, value, '')

    def mutate(self, command, *, cwd=None):
        self.mutations.append(command)
        if command[0] == '/usr/bin/open':
            self.opened.append(command)
            session = shlex.split(Path(command[-1]).read_text().splitlines()[-1])[-1]
            self.groups[session]['attached'] = 1
            return 0
        _, action, *args = command
        target = args[args.index('-t') + 1].lstrip('=') if '-t' in args else None
        if action == 'new-session': self.groups[args[args.index('-s') + 1]] = {'owner': '', 'attached': 0}
        elif action == 'set-option':
            key = 'owner' if args[-2] == '@focus-parent' else 'mode'
            self.groups[target][key] = args[-1]
        elif action == 'detach-client':
            self.groups[args[args.index('-s') + 1]]['attached'] = 0
        elif action == 'select-window': self.selected.append(target)
        elif action == 'kill-session': del self.groups[target]
        return 0


class TerminalWindowTests(unittest.TestCase):
    def setUp(self):
        permission = patch('workspace_manager.terminals.terminal_automation_permission', return_value=0)
        self.permission = permission.start()
        self.addCleanup(permission.stop)

    def test_independent_windows_are_reused_and_stop_only_kills_owned_views(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_STATE_HOME=temporary):
            root = Path(temporary)
            project = ProjectConfig('demo', root, TmuxConfig('demo', (TmuxWindowConfig('api', root), TmuxWindowConfig('web', root)), 'windows'),
                                    VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), root/'demo.toml')
            runner = Runner()
            windows = TerminalWindows('tmux', runner)
            windows.open(project)
            self.assertEqual(len(runner.opened), 2)
            self.assertEqual({item.split(':')[-1] for item in runner.selected}, {'api', 'web'})
            self.assertEqual(len({item.split(':')[0] for item in runner.selected}), 2)
            for command in runner.opened:
                self.assertIn('attach-session', command[-2])
                self.assertEqual(command[0], '/usr/bin/osascript')
                self.assertIn('do script (item 1 of arguments)', command[2])
            self.assertFalse(any(command[0] == '/usr/bin/open' for command in runner.mutations))
            windows.open(project)
            self.assertEqual(len(runner.opened), 2)
            runner.groups['unrelated'] = {'owner': 'another-project', 'attached': 1}
            windows.stop(project)
            self.assertEqual(set(runner.groups), {'unrelated'})

    def test_old_background_clients_reconnect_without_restarting_services(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_STATE_HOME=temporary):
            root = Path(temporary)
            project = ProjectConfig('demo', root, TmuxConfig('demo', (TmuxWindowConfig('api', root),), 'windows'),
                                    VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), root/'demo.toml')
            session = 'demo-view-' + hashlib.sha256(b'api').hexdigest()[:12]
            runner = Runner()
            runner.groups[session] = {'owner': 'demo', 'attached': 1}
            windows = TerminalWindows('tmux', runner)
            windows.open(project)
            self.assertIn(('tmux', 'detach-client', '-s', session), runner.mutations)
            self.assertEqual(runner.groups[session]['mode'], 'window')
            self.assertFalse(any(command[1] in {'kill-session', 'respawn-pane', 'new-session'} for command in runner.mutations))
            windows.open(project)
            self.assertEqual(len(runner.opened), 1)

    def test_instance_mode_launches_command_in_new_terminal_without_automation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_STATE_HOME=temporary):
            root = Path(temporary)
            project = ProjectConfig('demo', root,
                                    TmuxConfig('demo', (TmuxWindowConfig('api', root),), 'windows', 'instance'),
                                    VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), root/'demo.toml')
            runner = Runner()
            TerminalWindows('tmux', runner).open(project)
            self.permission.assert_not_called()
            self.assertEqual(len(runner.opened), 1)
            command = runner.opened[0]
            self.assertEqual(command[:4], ('/usr/bin/open', '-n', '-b', 'com.apple.Terminal'))
            self.assertNotIn('-F', command)
            launcher = Path(command[-1])
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o700)
            self.assertIn('unset TMUX; exec tmux attach-session', launcher.read_text())
            self.assertIn('demo / api', launcher.read_text())
            self.assertTrue(all(row['mode'] == 'instance' for row in runner.groups.values()))

    def test_disabled_reuse_creates_independent_view_without_detaching_old_client(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_STATE_HOME=temporary):
            root = Path(temporary)
            project = ProjectConfig('demo', root,
                                    TmuxConfig('demo', (TmuxWindowConfig('api', root),), 'windows', reuse_existing=False),
                                    VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), root/'demo.toml')
            runner = Runner()
            terminal = TerminalWindows('tmux', runner)
            terminal.open(project)
            terminal.open(project)
            self.assertEqual(len(runner.opened), 2)
            self.assertEqual(len(runner.groups), 2)
            self.assertTrue(all(row['attached'] == 1 for row in runner.groups.values()))
            self.assertFalse(any(call[1] in {'detach-client', 'respawn-pane', 'kill-session'} for call in runner.mutations))
            terminal.stop(project)
            self.assertEqual(runner.groups, {})

    def test_reuse_takes_priority_over_instance_launch_mode(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_STATE_HOME=temporary):
            root = Path(temporary)
            project = ProjectConfig('demo', root, TmuxConfig('demo', (), 'windows'),
                                    VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), root/'demo.toml')
            runner = Runner()
            terminal = TerminalWindows('tmux', runner)
            terminal.open(project)
            terminal.open(replace(project, tmux=replace(project.tmux, launch_mode='instance')))
            self.assertEqual(len(runner.opened), 1)
            self.assertEqual(runner.opened[0][0], '/usr/bin/osascript')

    def test_instance_dry_run_creates_no_launcher_or_process(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_STATE_HOME=temporary):
            root = Path(temporary)
            project = ProjectConfig('demo', root, TmuxConfig('demo', (), 'windows', 'instance'),
                                    VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), root/'demo.toml')
            runner = Runner()
            runner.dry_run = True
            TerminalWindows('tmux', runner).open(project)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(runner.mutations, [])
            self.permission.assert_not_called()

    def test_automation_denied_leaves_sessions_and_clients_untouched(self):
        root = Path('/tmp')
        project = ProjectConfig('demo', root, TmuxConfig('demo', (), 'windows'),
                                VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), root/'demo.toml')
        runner = Runner()
        self.permission.return_value = -1743
        with self.assertRaisesRegex(WorkError, 'Terminal Automation access is unavailable'):
            TerminalWindows('tmux', runner).open(project)
        self.assertEqual(runner.mutations, [])
        self.assertEqual(runner.opened, [])

    def test_stopped_terminal_does_not_launch_an_instance(self):
        runner = Runner()
        self.permission.return_value = -600
        with self.assertRaisesRegex(WorkError, 'Open Terminal before'):
            TerminalWindows('tmux', runner).check_access()
        self.assertEqual(runner.mutations, [])
        self.assertEqual(runner.opened, [])


class TerminalTitleRegressionTests(unittest.TestCase):
    def test_custom_title_matches_with_terminal_prefix_and_suffix(self):
        expected = 'wellprept-foundation / services / api'
        for actual in (expected, expected + ' — tmux — 230×40',
                       'aialec — ' + expected + ' — tmux attach-session -t session — 230×40'):
            self.assertTrue(TerminalWindows.matches_title(actual, expected))
        for actual in ('other — api — tmux', expected + '-other — tmux',
                       'not-' + expected, 'shell command mentioning ' + expected):
            self.assertFalse(TerminalWindows.matches_title(actual, expected))

    @patch('workspace_manager.terminals.terminal_automation_permission', return_value=0)
    def test_live_title_shape_is_found_on_creation_and_reused_on_retry(self, permission):
        from workspace_manager.windows import ProjectWindows
        from tests.test_windows import WindowRunner, window, TERMINAL
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_STATE_HOME=temporary):
            root = Path(temporary)
            project = ProjectConfig('demo', root,
                                    TmuxConfig('demo', (TmuxWindowConfig('api', root),), 'windows'),
                                    VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False),
                                    StartupConfig(), root / 'demo.toml')
            runner = Runner()
            inventory = WindowRunner([])
            windows = ProjectWindows('desktop-names', inventory, project, 'target')
            capture = runner.capture
            def open_and_report(command, **kwargs):
                result = capture(command, **kwargs)
                if command[0] == '/usr/bin/osascript':
                    inventory.rows.append(window(55653, bundle=TERMINAL,
                        title='aialec — ' + command[-1] + ' — tmux attach-session -t demo — 230×40'))
                return result
            runner.capture = open_and_report
            terminal = TerminalWindows('tmux', runner)
            terminal.open(project, windows=windows)
            self.assertEqual(inventory.rows[0]['spaces'], ['target'])
            # Reproduce the user's failed first attempt: attached client, but
            # no binding was saved because the old matcher rejected its title.
            windows.path.unlink()
            terminal.open(project, windows=windows)
            self.assertEqual(len(runner.opened), 1)
            self.assertTrue(windows.path.exists())
