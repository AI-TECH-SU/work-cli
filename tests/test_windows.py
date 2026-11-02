import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from workspace_manager.errors import WorkError
from workspace_manager.models import ProjectConfig, TmuxConfig, VSCodeConfig, ChromeConfig, ContextConfig, StartupConfig
from workspace_manager.windows import ProjectWindows, TERMINAL
from workspace_manager.extensions import resolve_extension
from workspace_manager.models import AppConfig, CommandConfig

CODE = "com.microsoft.VSCode"
CHROME = "com.google.Chrome"


def window(id=1, *, bundle=CODE, space='elsewhere', title='AGENT.MD — Foundation (Workspace) — Wellprept', pid=42):
    return dict(id=id, pid=pid, bundle=bundle, title=title, document='', spaces=[space])


class WindowRunner:
    dry_run = False

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.fail_move = False
        self.boot = '100'

    def capture(self, command):
        self.calls.append(command)
        if command[0] == '/usr/sbin/sysctl':
            return subprocess.CompletedProcess(command, 0, '{ sec = ' + self.boot + ', usec = 0 }', '')
        if command[1] == 'windows':
            rows = [row for row in self.rows if len(command) == 2 or row['bundle'] == command[2]]
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), '')
        if command[1] == 'move-window':
            if self.fail_move:
                return subprocess.CompletedProcess(command, 1, '', 'move rejected')
            for row in self.rows:
                if str(row['id']) == command[2]:
                    row['spaces'] = [command[5]]
        if command[1] == 'close-window':
            self.rows = [row for row in self.rows if str(row['id']) != command[2]]
        return subprocess.CompletedProcess(command, 0, 'ok\n', '')


class WindowsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, XDG_STATE_HOME=str(self.root / 'state'))
        env.start()
        self.addCleanup(env.stop)
        self.project = ProjectConfig('demo', self.root, TmuxConfig('demo'),
                                     VSCodeConfig(workspace=self.root / 'Foundation.code-workspace'),
                                     ChromeConfig(), ContextConfig(), StartupConfig(), self.root / 'demo.toml')
        self.matcher = resolve_extension(AppConfig("editor", extension="vscode", options={
            "workspace": str(self.project.vscode.workspace),
        }), self.project, CommandConfig("tmux", "code", "chrome"))
        self.runner = WindowRunner([window()])
        self.windows = ProjectWindows('desktop-names', self.runner, self.project, 'target')

    def test_original_workspace_moves_without_opening_or_copying(self):
        launch = Mock()
        self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        launch.assert_not_called()
        self.assertEqual(self.runner.rows[0]['spaces'], ['target'])
        self.assertFalse((self.root / 'state/focus/vscode-workspaces').exists())
        self.runner.calls.clear()
        self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        self.assertFalse(any(call[1] == 'move-window' for call in self.runner.calls))

    def test_failed_move_is_retried_using_same_window_without_launching(self):
        launch = Mock()
        self.runner.fail_move = True
        with self.assertRaisesRegex(WorkError, 'move rejected'):
            self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        self.assertTrue(self.windows.path.exists())
        self.runner.rows[0]['title'] = 'changed active document title'
        self.runner.fail_move = False
        self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        launch.assert_not_called()

    def test_new_window_is_recorded_and_moved_if_app_opened_on_wrong_desktop(self):
        self.runner.rows = []
        def launch():
            self.runner.rows.append(window())
        self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        self.assertEqual(self.runner.rows[0]['spaces'], ['target'])
        self.assertEqual(json.loads(self.windows.path.read_text())['vscode']['id'], 1)

    def test_disabling_reuse_leaves_saved_and_matching_windows_in_place(self):
        for bundle, slot in ((CODE, 'vscode'), (CHROME, 'chrome')):
            with self.subTest(slot=slot):
                self.runner.rows = [window(bundle=bundle)]
                self.windows.remember(slot, self.runner.rows[0])
                def launch():
                    self.runner.rows.append(window(2, bundle=bundle))
                self.windows.ensure(slot, bundle, launch, lambda row: True, reuse_existing=False)
                self.assertEqual(self.runner.rows[0]['spaces'], ['elsewhere'])
                self.assertEqual(self.runner.rows[1]['spaces'], ['target'])

    def test_application_reusing_old_window_does_not_override_disabled_reuse(self):
        launch = Mock()
        with patch('workspace_manager.windows.time.monotonic', side_effect=[0, 20]):
            with self.assertRaisesRegex(WorkError, 'No new vscode window appeared'):
                self.windows.ensure('vscode', CODE, launch, self.matcher.matches, reuse_existing=False)
        launch.assert_called_once()
        self.assertFalse(any(call[1] == 'move-window' for call in self.runner.calls))
        self.assertFalse(self.windows.path.exists())

    def test_late_accessibility_metadata_does_not_make_an_old_chrome_window_new(self):
        self.runner.rows = [window(46607, bundle=CHROME, title='')]
        def launch():
            self.runner.rows[0]['title'] = 'Existing personal browser window'
        with patch('workspace_manager.windows.time.monotonic', side_effect=[0, 20]):
            with self.assertRaisesRegex(WorkError, 'No new browser window appeared'):
                self.windows.ensure('browser', CHROME, launch)
        self.assertFalse(any(call[1] == 'move-window' for call in self.runner.calls))
        self.assertFalse(self.windows.path.exists())

    def test_existing_project_window_can_be_identified_after_launch_reveals_metadata(self):
        self.runner.rows = [window(title=''), window(2, title='Unrelated workspace')]
        def launch():
            self.runner.rows[0]['title'] = window()['title']
        self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        self.assertEqual(self.runner.rows[0]['spaces'], ['target'])
        self.assertEqual(self.runner.rows[1]['spaces'], ['elsewhere'])
        self.assertEqual(json.loads(self.windows.path.read_text())['vscode']['id'], 1)

    def test_late_matching_metadata_cannot_override_disabled_reuse(self):
        self.runner.rows = [window(title='')]
        def launch():
            self.runner.rows[0]['title'] = window()['title']
        with patch('workspace_manager.windows.time.monotonic', side_effect=[0, 20]):
            with self.assertRaisesRegex(WorkError, 'No new vscode window appeared'):
                self.windows.ensure('vscode', CODE, launch, self.matcher.matches, reuse_existing=False)
        self.assertEqual(self.runner.rows[0]['spaces'], ['elsewhere'])

    def test_late_matching_metadata_must_be_unambiguous(self):
        self.runner.rows = [window(title=''), window(2, title='')]
        def launch():
            for row in self.runner.rows:
                row['title'] = window()['title']
        with self.assertRaisesRegex(WorkError, 'Several vscode windows match'):
            self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        self.assertTrue(all(row['spaces'] == ['elsewhere'] for row in self.runner.rows))

    def test_failed_new_window_move_retries_without_opening_another(self):
        self.runner.rows = []
        def open_window():
            self.runner.rows.append(window())
        launch = Mock(side_effect=open_window)
        self.runner.fail_move = True
        with self.assertRaisesRegex(WorkError, 'move rejected'):
            self.windows.ensure('vscode', CODE, launch, reuse_existing=False)
        self.runner.fail_move = False
        self.windows.ensure('vscode', CODE, launch, reuse_existing=False)
        launch.assert_called_once()
        self.assertFalse(json.loads(self.windows.path.read_text())['vscode']['pending_move'])

    def test_only_new_matching_terminal_is_selected_among_restored_windows(self):
        self.runner.rows = [window(bundle=TERMINAL, title='demo / api')]
        def launch():
            self.runner.rows.extend([window(2, bundle=TERMINAL, title='unrelated restored shell'),
                                     window(3, bundle=TERMINAL, title='demo / api')])
        self.windows.ensure('terminal:api', TERMINAL, launch,
                            lambda row: row['title'] == 'demo / api', reuse_existing=False, require_match=True)
        self.assertEqual([row['spaces'] for row in self.runner.rows], [['elsewhere'], ['elsewhere'], ['target']])

    def test_terminal_does_not_adopt_an_unrelated_restored_window(self):
        self.runner.rows = []
        def launch():
            self.runner.rows.append(window(bundle=TERMINAL, title='unrelated restored shell'))
        with patch('workspace_manager.windows.time.monotonic', side_effect=[0, 20]):
            with self.assertRaisesRegex(WorkError, 'No new terminal:api window appeared'):
                self.windows.ensure('terminal:api', TERMINAL, launch,
                                    lambda row: row['title'] == 'demo / api',
                                    reuse_existing=False, require_match=True)
        self.assertEqual(self.runner.rows[0]['spaces'], ['elsewhere'])

    def test_disabling_reuse_after_failed_adoption_leaves_old_window_alone(self):
        self.runner.fail_move = True
        with self.assertRaisesRegex(WorkError, 'move rejected'):
            self.windows.ensure('vscode', CODE, Mock(), self.matcher.matches)
        self.runner.fail_move = False
        def launch():
            self.runner.rows.append(window(2))
        self.windows.ensure('vscode', CODE, launch, self.matcher.matches, reuse_existing=False)
        self.assertEqual([row['spaces'] for row in self.runner.rows], [['elsewhere'], ['target']])

    def test_ambiguous_workspace_titles_do_not_move_arbitrary_window(self):
        self.runner.rows.append(window(2))
        launch = Mock()
        with self.assertRaisesRegex(WorkError, 'Several vscode'):
            self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        launch.assert_not_called()
        self.assertEqual([row['spaces'] for row in self.runner.rows], [['elsewhere'], ['elsewhere']])

    def test_same_id_different_process_does_not_reuse_unrelated_chrome(self):
        self.windows.remember('chrome', window(bundle=CHROME, pid=41))
        self.runner.rows = [window(bundle=CHROME, pid=42)]
        def launch():
            self.runner.rows.append(window(2, bundle=CHROME))
        self.windows.ensure('chrome', CHROME, launch)
        self.assertEqual(self.runner.rows[0]['spaces'], ['elsewhere'])
        self.assertEqual(self.runner.rows[1]['spaces'], ['target'])

    def test_repair_adopts_existing_chrome_on_dedicated_project_desktop(self):
        self.runner.rows = [window(bundle=CHROME, space='target')]
        launch = Mock()
        self.windows.ensure('chrome', CHROME, launch, adopt_single_on_desktop=True)
        launch.assert_not_called()
        self.assertEqual(json.loads(self.windows.path.read_text())['chrome']['id'], 1)

    def test_reboot_invalidates_saved_window_ids_even_when_pid_is_reused(self):
        self.windows.remember('chrome', window(bundle=CHROME))
        self.runner.boot = '200'
        self.runner.rows = [window(bundle=CHROME)]
        windows = ProjectWindows('desktop-names', self.runner, self.project, 'target')
        def launch():
            self.runner.rows.append(window(2, bundle=CHROME))
        windows.ensure('chrome', CHROME, launch)
        self.assertEqual(self.runner.rows[0]['spaces'], ['elsewhere'])
        self.assertEqual(self.runner.rows[1]['spaces'], ['target'])

    def test_stop_closes_only_target_desktop_windows_not_shared_app_elsewhere(self):
        self.runner.rows = [window(1, space='target'), window(2), window(3, bundle=TERMINAL, space='target')]
        sticky = window(4, space='target')
        sticky['spaces'].append('elsewhere')
        self.runner.rows.append(sticky)
        self.windows.close_on_desktop(exclude_bundles=(TERMINAL,))
        self.assertEqual([row['id'] for row in self.runner.rows], [2, 3, 4])
        with self.assertRaisesRegex(WorkError, 'still contains'):
            self.windows.require_empty()
        self.windows.close_on_desktop(include_bundles=(TERMINAL,))
        self.windows.require_empty()
        self.assertEqual([row['id'] for row in self.runner.rows], [2, 4])

    def test_document_from_different_project_rejects_same_workspace_name(self):
        row = window()
        row['document'] = 'file:///other/repository/AGENT.MD'
        self.assertFalse(self.matcher.matches(row))

    def test_document_in_a_linked_workspace_folder_is_recognized(self):
        source = self.root / 'backend'
        source.mkdir()
        (self.root / 'linked').symlink_to(source)
        self.project.vscode.workspace.write_text('''{
            // VS Code accepts comments and trailing commas.
            "folders": [{"path": "linked",},],
            "settings": {"url": "https://example.test/a,}/*literal*/"},
        }''')
        row = window()
        row['document'] = (source / 'file.php').as_uri()
        self.assertTrue(self.matcher.matches(row))

    def test_dry_run_does_not_read_or_write_window_state(self):
        self.runner.dry_run = True
        launch = Mock()
        self.windows.ensure('vscode', CODE, launch, self.matcher.matches)
        self.windows.close_on_desktop(exclude_bundles=(TERMINAL,))
        self.windows.require_empty()
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(self.windows.path.exists())


if __name__ == '__main__':
    unittest.main()
