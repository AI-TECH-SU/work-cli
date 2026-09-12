from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import (
    ChromeConfig,
    CommandConfig,
    ContextConfig,
    ProjectConfig,
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
        root = Path(self.temp.name)
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
        self.assertTrue(any("new-window" in command and "npm run dev" in command for command in flattened))
        self.assertTrue(any(command.startswith("code --new-window") for command in flattened))
        self.assertTrue(any("--profile-directory=Profile 2" in command for command in flattened))

    def test_healthy_workspace_does_not_duplicate_resources(self) -> None:
        responses = {
            ("tmux", "has-session", "-t", "work-demo"): (0, ""),
            ("tmux", "list-windows", "-t", "work-demo", "-F", "#{window_name}:#{window_dead}"): (0, "shell:0\ndev:0\n"),
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
            ("tmux", "has-session", "-t", "work-demo"): (0, ""),
            ("tmux", "list-windows", "-t", "work-demo", "-F", "#{window_name}:#{window_dead}"): (0, "shell:0\ndev:0\n"),
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

        self.assertFalse((Path(os.environ["XDG_STATE_HOME"]) / "work").exists())

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
        self.assertIn(
            (
                "tmux",
                "new-window",
                "-d",
                "-t",
                "work-demo",
                "-n",
                "web",
                "-c",
                str(web),
                os.environ.get("SHELL", "/bin/zsh"),
                "-lc",
                "exec npm run dev",
            ),
            runner.mutations,
        )

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
            ("tmux", "has-session", "-t", "work-demo"): (0, ""),
            ("tmux", "list-windows", "-t", "work-demo", "-F", "#{window_name}"): (0, "api\n"),
        }
        runner = FakeRunner(responses)
        manager = WorkspaceManager(
            self.commands, runner, validate_executables=False  # type: ignore[arg-type]
        )

        manager.reconcile(project)

        self.assertEqual(len(runner.mutations), 1)
        self.assertEqual(runner.mutations[0][6], "web")


if __name__ == "__main__":
    unittest.main()
