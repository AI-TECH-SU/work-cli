import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.paths import config_home, state_home, project_lock_path
from workspace_manager.config import load_project


class RenameCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.root / 'config'),
                                 XDG_STATE_HOME=str(self.root / 'state'))
        environment.start()
        self.addCleanup(environment.stop)

    def test_new_installations_use_focus(self):
        self.assertEqual(config_home(), self.root / 'config/focus')
        self.assertEqual(state_home(), self.root / 'state/focus')

    def test_existing_config_and_state_are_reused(self):
        legacy = self.root / 'config/work/projects'
        legacy.mkdir(parents=True)
        state = self.root / 'state/work'
        (state / 'desktops').mkdir(parents=True)
        binding = state / 'desktops/demo.json'
        binding.write_text('{"uuid":"existing"}')
        (legacy / 'demo.toml').write_text(f'version = 1\nroot = "{self.root}"\n')
        self.assertEqual(config_home(), legacy.parent)
        self.assertEqual(state_home(), state)
        self.assertEqual(project_lock_path('demo'), state / 'locks/demo.lock')
        self.assertEqual(load_project('demo').tmux.session, 'work-demo')
        self.assertEqual(binding.read_text(), '{"uuid":"existing"}')

    def test_focus_takes_precedence_without_deleting_legacy_data(self):
        for base in ['config', 'state']:
            for product in ['work', 'focus']:
                (self.root / base / product).mkdir(parents=True)
        legacy = self.root / 'config/work/keep.toml'
        legacy.write_text('keep')
        self.assertEqual(config_home(), self.root / 'config/focus')
        self.assertEqual(state_home(), self.root / 'state/focus')
        self.assertEqual(legacy.read_text(), 'keep')
