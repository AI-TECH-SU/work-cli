from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.errors import WorkError
from workspace_manager.extensions import _validate_spec, load_extension, validate_options
from workspace_manager.models import (AppConfig, ChromeConfig, CommandConfig, ContextConfig, ProjectConfig,
                                       StartupConfig, TmuxConfig, VSCodeConfig)
from workspace_manager.session_extensions import ResolvedSessionExtension


class Runner:
    def __init__(self, responses=None, *, dry_run=False):
        self.responses = responses or {}
        self.dry_run = dry_run
        self.calls = []
        self.mutations = []

    def capture(self, argv, **kwargs):
        argv = tuple(argv)
        self.calls.append(argv)
        code, text = self.responses.get(argv, (1, ''))
        return subprocess.CompletedProcess(argv, code, text, '')

    def mutate(self, argv, **kwargs):
        self.mutations.append(tuple(argv))
        return 0

    def shell_capture(self, command, **kwargs):
        return self.capture(('shell', command))


class SessionExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = ProjectConfig('demo', self.root, TmuxConfig('unused', enabled=False),
            VSCodeConfig(False), ChromeConfig(False), ContextConfig(None, False), StartupConfig(), self.root/'demo.toml')
        self.commands = CommandConfig('tmux', 'code', 'chrome')

    def resource(self, options=None, *, spec=None, **kwargs):
        spec = spec or load_extension('tmux')
        app = AppConfig('services', extension=spec.id, options=options or {}, **kwargs)
        return ResolvedSessionExtension(spec, app, self.project, self.commands)

    def test_fresh_lifecycle_uses_spec_commands_and_retains_output_before_starting(self):
        resource = self.resource({'session': 'demo', 'windows': [
            {'name': 'api', 'root': '.', 'command': 'npm run api'}, {'name': 'web', 'root': '.'}]})
        runner = Runner()
        self.assertTrue(resource.restore(runner))
        self.assertEqual(runner.mutations[0], ('tmux', 'new-session', '-d', '-s', 'demo', '-n', 'api', '-c', str(self.root)))
        self.assertEqual(runner.mutations[1], ('tmux', 'set-window-option', '-t', '=demo:api', 'remain-on-exit', 'on'))
        self.assertEqual(runner.mutations[2][-3:], (os.environ.get('SHELL', '/bin/zsh'), '-lc', 'exec npm run api'))
        self.assertEqual(runner.mutations[3], ('tmux', 'new-window', '-d', '-t', '=demo', '-n', 'web', '-c', str(self.root)))

    def test_existing_session_repairs_missing_units_but_retains_dead_units(self):
        resource = self.resource({'session': 'demo', 'windows': [
            {'name': 'api', 'command': 'false'}, {'name': 'web'}]})
        runner = Runner({resource.operation('exists'): (0, ''),
                         resource.operation('list_units'): (0, 'api:1\n')})
        self.assertFalse(resource.restore(runner))
        self.assertEqual(len(runner.mutations), 1)
        self.assertIn('web', runner.mutations[0])
        self.assertNotIn('respawn-pane', runner.mutations[0])

    def test_dead_startup_unit_is_preserved_until_restart(self):
        resource = self.resource({'session': 'demo', 'startup_command': 'npm run dev'})
        runner = Runner({resource.operation('exists'): (0, ''),
                         resource.operation('list_units'): (0, 'shell:0\ndev:1\n')})
        self.assertFalse(resource.startup_status(runner))
        resource.restore(runner)
        self.assertEqual(runner.mutations, [])

    def test_startup_in_default_shell_starts_once_without_duplicate_unit(self):
        resource = self.resource({'startup_command': 'start', 'startup_window': 'shell'})
        runner = Runner()
        resource.restore(runner)
        self.assertEqual([call[1] for call in runner.mutations], ['new-session', 'set-window-option', 'respawn-pane'])

    def test_healthy_external_startup_is_not_duplicated_on_fresh_session(self):
        resource = self.resource({'startup_command': 'start', 'startup_check': 'healthy'})
        runner = Runner({('shell', 'healthy'): (0, '')})
        resource.restore(runner)
        self.assertEqual([call[1] for call in runner.mutations], ['new-session'])

    def test_startup_check_is_an_explicit_read_only_shell_probe(self):
        resource = self.resource({'startup_command': 'start', 'startup_check': 'healthy'})
        runner = Runner({('shell', 'healthy'): (0, '')})
        self.assertTrue(resource.startup_status(runner))
        self.assertEqual(runner.mutations, [])

    def test_stop_removes_only_owned_views_and_exact_base_session(self):
        resource = self.resource({'session': 'demo'})
        runner = Runner({resource.operation('exists'): (0, ''),
                         resource.operation('view_list'): (0, 'owned\tdemo\nforeign\tother\n')})
        self.assertTrue(resource.stop(runner))
        self.assertEqual(runner.mutations, [('tmux', 'kill-session', '-t', '=owned'),
                                           ('tmux', 'kill-session', '-t', '=demo')])

    def test_missing_exact_base_never_stops_prefix_neighbor(self):
        resource = self.resource({'session': 'demo'})
        runner = Runner({resource.operation('view_list'): (0, 'demo-longer\t\n')})
        self.assertFalse(resource.stop(runner))
        self.assertIn(('tmux', 'has-session', '-t', '=demo'), runner.calls)
        self.assertEqual(runner.mutations, [])

    def test_foreign_view_ownership_is_refused_before_mutation(self):
        resource = self.resource({'session': 'demo', 'presentation': 'windows'})
        view = resource.view_identity('api')
        runner = Runner({resource.operation('view_exists', view=view): (0, ''),
                         resource.operation('view_owner', view=view): (0, 'other')})
        with self.assertRaisesRegex(WorkError, 'unrelated'):
            resource.view_plan(runner, 'api')
        self.assertEqual(runner.mutations, [])

    def test_explicit_resources_use_distinct_view_slots(self):
        resource = self.resource()
        self.assertEqual(resource.session_name, 'focus-demo-services')
        self.assertEqual(resource.view_slot('api'), 'terminal:services:api')
        self.assertEqual(resource.view_title('api'), 'demo / services / api')
        legacy = self.resource(legacy_slot='tmux')
        self.assertEqual(legacy.view_slot('api'), 'terminal:api')
        self.assertEqual(legacy.view_title('api'), 'demo / api')

    @patch('workspace_manager.session_extensions.run_interactive', return_value=7)
    def test_interactive_attach_and_switch_are_declared_operations(self, run):
        resource = self.resource({'session': 'demo'})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resource.attach(Runner()), 7)
        run.assert_called_with(('tmux', 'attach-session', '-t', '=demo'))
        with patch.dict(os.environ, {'TMUX': 'client'}):
            resource.attach(Runner())
        run.assert_called_with(('tmux', 'switch-client', '-t', '=demo'))
        self.assertEqual(self.resource({'presentation': 'none'}).attach(Runner()), 0)

    def test_second_provider_uses_same_engine_without_vendor_dispatch(self):
        data = deepcopy(load_extension('tmux').data)
        data['id'] = 'sessionshell'
        data['command'] = {'name': 'sessionctl'}
        data['session'].pop('views')
        data['session']['attach'] = {}
        data['session']['inventory'] = {'separator': '|', 'columns': ['dead', 'name'], 'dead_value': 'failed'}
        core = {'exists', 'create', 'list_units', 'create_unit', 'retain_unit', 'start_unit', 'stop', 'attach'}
        data['operations'] = {name: {'argv': ['{command}', f'provider:{name}', '{session}'] +
                                (['{unit}'] if name in {'create', 'create_unit', 'retain_unit', 'start_unit'} else [])}
                              for name in core}
        spec = _validate_spec(data, 'sessionshell')
        resource = self.resource({'session': 'other', 'windows': [{'name': 'api'}, {'name': 'web'}]}, spec=spec)
        runner = Runner({('sessionctl', 'provider:exists', 'other'): (0, ''),
                         ('sessionctl', 'provider:list_units', 'other'): (0, 'running|api\n')})
        self.assertFalse(resource.restore(runner))
        self.assertEqual(runner.mutations, [('sessionctl', 'provider:create_unit', 'other', 'web')])
        self.assertTrue(resource.status(runner))
        self.assertTrue(resource.stop(runner))
        self.assertEqual(runner.mutations[-1], ('sessionctl', 'provider:stop', 'other'))
        self.assertEqual(resource.required_commands(), [('extension services', 'sessionctl')])

    def test_programmatic_session_matchers_are_rejected_before_runtime(self):
        with self.assertRaisesRegex(WorkError, 'window matchers'):
            self.resource(match_title='demo')

    def test_ready_reports_failed_startup_even_when_session_exists(self):
        resource = self.resource({'startup_command': 'start', 'startup_check': 'healthy'})
        runner = Runner({resource.operation('exists'): (0, '')})
        self.assertTrue(resource.status(runner))
        self.assertFalse(resource.ready(runner))

    def test_invalid_schema_shapes_fail_as_configuration_errors(self):
        original = load_extension('tmux').data
        changes = [
            lambda d: d.update(kind='unknown'),
            lambda d: d['session'].update(identity_input=[]),
            lambda d: d['session']['inventory'].update(columns=[{}, 'dead']),
            lambda d: d['session']['attach'].update(clear_env=['BAD NAME']),
            lambda d: d['operations']['exists'].update(argv=['{unknown}']),
            lambda d: d['operations'].pop('stop'),
            lambda d: d['install'].update(package='tmux; echo bad'),
            lambda d: d['inputs']['windows']['fields']['root'].update(type='records'),
            lambda d: d.update(bundle_id='other.Terminal'),
        ]
        for change in changes:
            with self.subTest(change=change):
                data = deepcopy(original)
                change(data)
                with self.assertRaises(WorkError):
                    _validate_spec(data, 'tmux')

    def test_invalid_duplicate_units_fail_during_option_parsing(self):
        with self.assertRaisesRegex(WorkError, 'duplicate'):
            validate_options(load_extension('tmux'), {'windows': [{'name': 'api'}, {'name': 'api'}]}, self.root)

    def test_dry_run_does_not_prepare_terminal_or_attach(self):
        resource = self.resource({'presentation': 'windows'}, launch_mode='instance')
        runner = Runner(dry_run=True)
        with patch('workspace_manager.terminals.terminal_automation_permission') as access:
            resource.preflight(runner)
            resource.present(runner)
        self.assertEqual(runner.calls, [])
        self.assertEqual(runner.mutations, [])
        access.assert_not_called()


if __name__ == '__main__':
    unittest.main()
