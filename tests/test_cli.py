from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workspace_manager.cli import normalize_argv, repair_tmux, run
from workspace_manager.errors import WorkError


class CliTests(unittest.TestCase):
    def test_project_shorthand_becomes_boot_command(self) -> None:
        self.assertEqual(
            normalize_argv(["projectA", "--dry-run"]),
            ["boot", "projectA", "--dry-run"],
        )

    def test_named_commands_are_not_rewritten(self) -> None:
        self.assertEqual(normalize_argv(["status", "projectA"]), ["status", "projectA"])

    def test_noninteractive_init_writes_a_loadable_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            (root / "PROJECT.md").write_text("# context\n", encoding="utf-8")
            config_root = Path(temporary) / "config"
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_root)}):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = run(
                        [
                            "init",
                            "demo",
                            "--root",
                            str(root),
                            "--chrome-profile",
                            "Profile 1",
                            "--url",
                            "http://localhost:3000",
                            "--non-interactive",
                            "--desktop",
                        ]
                    )
                self.assertEqual(code, 0)
                project_file = config_root / "focus" / "projects" / "demo.toml"
                self.assertTrue(project_file.is_file())
                self.assertIn('manager = "desktop-names"', project_file.read_text())
                self.assertIn("next: run 'focus demo'", output.getvalue())

    def test_no_arguments_prints_help(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = run([])
        self.assertEqual(code, 0)
        self.assertIn("Restore a local project workspace", output.getvalue())

    @patch("workspace_manager.cli.executable_exists")
    @patch("workspace_manager.cli.find_homebrew", return_value="/opt/homebrew/bin/brew")
    @patch("workspace_manager.cli.CommandRunner.mutate", return_value=0)
    def test_repair_tmux_installs_with_homebrew_after_approval(
        self,
        mutate,
        _find_homebrew,
        executable_exists,
    ) -> None:
        executable_exists.side_effect = [False, True]

        repaired = repair_tmux(tmux_command="tmux", assume_yes=True)

        self.assertEqual(repaired, "tmux")
        mutate.assert_called_once_with(("/opt/homebrew/bin/brew", "install", "tmux"))

    @patch("workspace_manager.cli.executable_exists", return_value=False)
    @patch("workspace_manager.cli.find_homebrew", return_value=None)
    def test_repair_tmux_requires_an_existing_homebrew(self, _find_homebrew, _exists) -> None:
        with self.assertRaisesRegex(WorkError, "Homebrew was not found"):
            repair_tmux(tmux_command="tmux", assume_yes=True)

    def test_doctor_yes_requires_fix(self) -> None:
        with self.assertRaisesRegex(WorkError, "--yes requires --fix"):
            run(["doctor", "--yes"])

    @patch('workspace_manager.cli.load_project')
    @patch('workspace_manager.cli.WorkspaceManager')
    def test_focus_and_stop_route_to_their_lifecycle_operations(self, manager, project):
        manager.return_value.focus.return_value = 0
        self.assertEqual(run(['demo']), 0)
        manager.return_value.focus.assert_called_once_with(project.return_value, no_attach=False,
                                                           force_gui=False, restart=False)
        self.assertEqual(run(['stop', 'demo']), 0)
        manager.return_value.stop.assert_called_once_with(project.return_value)


if __name__ == "__main__":
    unittest.main()
