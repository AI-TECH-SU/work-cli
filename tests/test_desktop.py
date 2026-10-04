import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from workspace_manager.desktop import DesktopNamesAdapter
from workspace_manager.errors import WorkError
from workspace_manager.models import (ProjectConfig, SpaceConfig, TmuxConfig, VSCodeConfig,
                                      ChromeConfig, ContextConfig, StartupConfig)


class Runner:
    dry_run = False

    def __init__(self):
        self.rows = [dict(uuid="a", active=True, regular=True),
                     dict(uuid="b", active=False, regular=True)]
        self.calls = []
        self.failure = None
        self.created = 0

    def capture(self, command):
        self.calls.append(command)
        if command[1] == "create":
            if self.failure == "create":
                return subprocess.CompletedProcess(command, 1, "", "creation rejected")
            self.created += 1
            key = "new" if self.created == 1 else f"new-{self.created}"
            self.rows.append(dict(uuid=key, active=False, regular=True))
            return subprocess.CompletedProcess(command, 0, key + "\n", "")
        return subprocess.CompletedProcess(command, 0, json.dumps(self.rows), "")

    def mutate(self, command):
        self.calls.append(command)
        if command[1] == self.failure:
            return 1
        for row in self.rows:
            if command[1] == "switch":
                row["active"] = row["uuid"] == command[2]
            elif command[1] == "rename" and row["uuid"] == command[2]:
                row["name"] = command[3]
        return 0


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, XDG_STATE_HOME=self.temp.name)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        root = Path(self.temp.name)
        self.project = ProjectConfig("demo", root, TmuxConfig("demo"), VSCodeConfig(),
                                     ChromeConfig(), ContextConfig(), StartupConfig(), root / "demo.toml",
                                     SpaceConfig(enabled=True))
        self.binding = root / "focus/desktops/demo.json"
        self.runner = Runner()
        self.adapter = DesktopNamesAdapter("desktop-names", self.runner)

    def test_first_run_creates_then_returns_to_uuid_after_reordering(self):
        self.adapter.prepare(self.project)
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "new"})
        self.runner.rows.reverse()
        for row in self.runner.rows:
            row["active"] = row["uuid"] == "b"
        self.runner.calls.clear()
        self.adapter.prepare(self.project)
        self.assertEqual(self.runner.calls[1:], [
            ("desktop-names", "switch", "new"), ("desktop-names", "rename", "new", "demo")])

    def test_refocus_existing_desktop_switches_without_rename_or_creation(self):
        self.runner.rows[1]['name'] = 'demo'
        self.assertTrue(self.adapter.switch_existing(self.project))
        self.assertEqual(self.runner.calls, [('desktop-names', 'list'), ('desktop-names', 'switch', 'b')])
        self.assertEqual(json.loads(self.binding.read_text()), {'uuid': 'b'})

    def test_missing_desktop_lookup_does_not_create_one_during_stop(self):
        self.assertIsNone(self.adapter.find(self.project))
        self.assertEqual(self.runner.calls, [('desktop-names', 'list')])
        self.assertFalse(self.binding.exists())

    def test_unverified_desktop_removal_retains_binding(self):
        self.adapter.prepare(self.project)
        with self.assertRaisesRegex(WorkError, 'not confirmed'):
            self.adapter.remove(self.project, 'new')
        self.assertEqual(json.loads(self.binding.read_text()), {'uuid': 'new'})

    def test_missing_bound_desktop_creates_names_and_reuses_a_replacement(self):
        self.adapter.prepare(self.project)
        self.runner.rows = [dict(uuid="b", active=True, regular=True)]
        self.runner.calls.clear()
        project = replace(self.project, space=SpaceConfig(enabled=True, name="A Project"))
        self.assertEqual(self.adapter.prepare(project), "new-2")
        self.assertEqual(self.runner.calls, [
            ("desktop-names", "list"), ("desktop-names", "create"),
            ("desktop-names", "switch", "new-2"), ("desktop-names", "rename", "new-2", "A Project")])
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "new-2"})
        self.runner.calls.clear()
        self.adapter.prepare(project)
        self.assertFalse(any(call[1] == "create" for call in self.runner.calls))

    def test_existing_named_desktop_is_reused_without_a_binding(self):
        self.runner.rows[1]["name"] = "demo"
        self.assertEqual(self.adapter.prepare(self.project), "b")
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "b"})
        self.assertFalse(any(call[1] == "create" for call in self.runner.calls))

    def test_missing_binding_reuses_an_existing_desktop_with_the_same_name(self):
        self.adapter.prepare(self.project)
        self.runner.rows = [dict(uuid="b", active=True, regular=True, name="demo")]
        self.runner.calls.clear()
        self.assertEqual(self.adapter.prepare(self.project), "b")
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "b"})
        self.assertFalse(any(call[1] == "create" for call in self.runner.calls))

    def test_ambiguous_names_stop_without_creating_or_renaming(self):
        for row in self.runner.rows:
            row["name"] = "demo"
        with self.assertRaisesRegex(WorkError, "Several desktops"):
            self.adapter.prepare(self.project)
        self.assertEqual(self.runner.calls, [("desktop-names", "list")])
        self.assertFalse(self.binding.exists())

    def test_failed_replacement_creation_preserves_the_previous_binding(self):
        self.adapter.prepare(self.project)
        self.runner.rows = [dict(uuid="b", active=True, regular=True)]
        self.runner.calls.clear()
        self.runner.failure = "create"
        with self.assertRaisesRegex(WorkError, "create failed"):
            self.adapter.prepare(self.project)
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "new"})
        self.assertEqual(self.runner.calls, [("desktop-names", "list"), ("desktop-names", "create")])

    def test_missing_explicit_uuid_reuses_created_replacement_after_rename_failure(self):
        project = replace(self.project, space=SpaceConfig(enabled=True, uuid="gone"))
        self.runner.failure = "rename"
        with self.assertRaisesRegex(WorkError, "rename failed"):
            self.adapter.prepare(project)
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "new"})
        self.runner.failure = None
        self.runner.calls.clear()
        self.assertEqual(self.adapter.prepare(project), "new")
        self.assertFalse(any(call[1] == "create" for call in self.runner.calls))

    def test_explicit_uuid_takes_precedence_over_an_existing_name(self):
        self.runner.rows[0]["name"] = "demo"
        project = replace(self.project, space=SpaceConfig(enabled=True, uuid="b"))
        self.assertEqual(self.adapter.prepare(project), "b")

    def test_failed_switch_retains_created_uuid_without_rename(self):
        self.runner.failure = "switch"
        with self.assertRaisesRegex(WorkError, "switch failed"):
            self.adapter.prepare(self.project)
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "new"})
        self.assertFalse(any(call[1] == "rename" for call in self.runner.calls))

    def test_failed_rename_retains_created_uuid(self):
        self.runner.failure = "rename"
        with self.assertRaisesRegex(WorkError, "rename failed"):
            self.adapter.prepare(self.project)
        self.assertEqual(json.loads(self.binding.read_text()), {"uuid": "new"})

    def test_current_mode_uses_current_uuid_and_custom_name_without_binding(self):
        self.adapter.prepare(replace(self.project, space=SpaceConfig(True, target="current", name="A Project")))
        self.assertEqual(self.runner.calls[-1], ("desktop-names", "rename", "a", "A Project"))
        self.assertFalse(self.binding.exists())

    def test_dry_run_never_queries_or_creates_state(self):
        self.runner.dry_run = True
        self.adapter.prepare(self.project)
        self.assertEqual(self.runner.calls, [])
        self.assertFalse((Path(self.temp.name) / "focus").exists())

    def test_corrupt_binding_is_preserved(self):
        self.binding.parent.mkdir(parents=True)
        self.binding.write_text("broken")
        with self.assertRaisesRegex(WorkError, "Cannot read"):
            self.adapter.prepare(self.project)
        self.assertEqual(self.binding.read_text(), "broken")

    def test_multiple_active_displays_are_rejected(self):
        self.runner.rows[1]["active"] = True
        with self.assertRaisesRegex(WorkError, "one active display"):
            self.adapter.prepare(replace(self.project, space=SpaceConfig(True, target="current")))
        self.assertFalse(self.binding.exists())

    def test_fullscreen_is_not_used_in_current_mode(self):
        self.runner.rows[0]["regular"] = False
        with self.assertRaisesRegex(WorkError, "regular desktop"):
            self.adapter.prepare(replace(self.project, space=SpaceConfig(True, target="current")))
        self.assertFalse(self.binding.exists())
