from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Iterator

from workspace_manager.errors import WorkError
from workspace_manager.models import (
    CommandConfig,
    ProjectConfig,
    TmuxWindowConfig,
    WorkspaceStatus,
)
from workspace_manager.paths import project_lock_path
from workspace_manager.system import CommandRunner, executable_exists


class WorkspaceManager:
    def __init__(
        self,
        commands: CommandConfig,
        runner: CommandRunner,
        *,
        validate_executables: bool = True,
    ) -> None:
        self.commands = commands
        self.runner = runner
        self.validate_executables = validate_executables

    @contextmanager
    def lock(self, project: str) -> Iterator[None]:
        path = project_lock_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkError(f"another work operation is active for {project!r}") from exc
            yield

    def tmux_running(self, project: ProjectConfig) -> bool:
        result = self.runner.capture(
            (self.commands.tmux, "has-session", "-t", project.tmux.session)
        )
        return result.returncode == 0

    def startup_running(self, project: ProjectConfig) -> bool | None:
        startup = project.startup
        if not startup.command:
            return None
        if startup.check:
            return self.runner.shell_capture(startup.check, cwd=project.root).returncode == 0
        if not self.tmux_running(project):
            return False
        result = self.runner.capture(
            (
                self.commands.tmux,
                "list-windows",
                "-t",
                project.tmux.session,
                "-F",
                "#{window_name}:#{window_dead}",
            )
        )
        return any(
            line == f"{startup.window}:0" for line in result.stdout.splitlines()
        )

    def vscode_running(self, project: ProjectConfig) -> bool | None:
        if not project.vscode.enabled:
            return None
        result = self.runner.capture((self.commands.code, "--status"))
        output = (result.stdout + result.stderr).casefold()
        targets = {str(project.vscode.workspace or project.root), str(project.root)}
        return result.returncode == 0 and any(target.casefold() in output for target in targets)

    def chrome_running(self, project: ProjectConfig) -> bool | None:
        if not project.chrome.enabled:
            return None
        result = self.runner.capture(("/bin/ps", "-axo", "command="))
        marker = f"--profile-directory={project.chrome.profile}"
        executable_name = Path(self.commands.chrome).name
        return result.returncode == 0 and any(
            marker in line and executable_name in line for line in result.stdout.splitlines()
        )

    def status(self, project: ProjectConfig) -> WorkspaceStatus:
        return WorkspaceStatus(
            project=project.name,
            tmux=self.tmux_running(project),
            startup=self.startup_running(project),
            vscode=self.vscode_running(project),
            chrome=self.chrome_running(project),
        )

    def reconcile(
        self,
        project: ProjectConfig,
        *,
        force_gui: bool = False,
        assume_stopped: bool = False,
    ) -> WorkspaceStatus:
        self._validate_paths(project)
        self._validate_commands(project)
        lock = nullcontext() if self.runner.dry_run else self.lock(project.name)
        with lock:
            fresh_boot = assume_stopped or not self.tmux_running(project)
            if fresh_boot:
                self._create_tmux(project)
            else:
                print(f"ok  tmux session {project.tmux.session!r} is running")

            self._reconcile_tmux_windows(project, fresh_boot=fresh_boot)

            if project.startup.command:
                if self.startup_running(project):
                    print(f"ok  startup window {project.startup.window!r} is running")
                else:
                    self._start_development_command(project)

            vscode_open = self.vscode_running(project)
            if project.vscode.enabled:
                if force_gui or not vscode_open:
                    self._open_vscode(project)
                else:
                    print("ok  VS Code workspace is open")

            chrome_open = self.chrome_running(project)
            if project.chrome.enabled:
                if force_gui or fresh_boot or not chrome_open:
                    self._open_chrome(project)
                else:
                    print(f"ok  Chrome profile {project.chrome.profile!r} is running")

        return self.status(project) if not self.runner.dry_run else WorkspaceStatus(
            project=project.name,
            tmux=True,
            startup=True if project.startup.command else None,
            vscode=True if project.vscode.enabled else None,
            chrome=True if project.chrome.enabled else None,
        )

    def stop(self, project: ProjectConfig) -> bool:
        lock = nullcontext() if self.runner.dry_run else self.lock(project.name)
        with lock:
            if not self.tmux_running(project):
                print(f"ok  tmux session {project.tmux.session!r} is already stopped")
                return False
            code = self.runner.mutate(
                (self.commands.tmux, "kill-session", "-t", project.tmux.session)
            )
            if code != 0:
                raise WorkError(f"tmux failed to stop session {project.tmux.session!r}")
            print("ok  project tmux session stopped; shared GUI applications were left open")
            return True

    def _validate_paths(self, project: ProjectConfig) -> None:
        for window in project.tmux.windows:
            if not window.root.is_dir():
                raise WorkError(
                    f"tmux window root does not exist or is not a directory: {window.root}"
                )
        if project.vscode.enabled and project.vscode.workspace and not project.vscode.workspace.exists():
            raise WorkError(f"VS Code workspace does not exist: {project.vscode.workspace}")
        if (
            project.context.path
            and project.context.open_in_vscode
            and not project.context.path.is_file()
        ):
            raise WorkError(f"project context file does not exist: {project.context.path}")

    def _validate_commands(self, project: ProjectConfig) -> None:
        if not self.validate_executables:
            return
        required = [("tmux", self.commands.tmux)]
        if project.vscode.enabled:
            required.append(("VS Code", self.commands.code))
        if project.chrome.enabled:
            required.append(("Chrome", self.commands.chrome))
        missing = [f"{label}: {command}" for label, command in required if not executable_exists(command)]
        if missing:
            raise WorkError(
                "required executable not found (run 'work doctor'): " + ", ".join(missing)
            )

    def _create_tmux(self, project: ProjectConfig) -> None:
        first_window = project.tmux.windows[0] if project.tmux.windows else None
        command = [
            self.commands.tmux,
            "new-session",
            "-d",
            "-s",
            project.tmux.session,
            "-n",
            first_window.name if first_window else "shell",
            "-c",
            str(first_window.root if first_window else project.root),
        ]
        if first_window and first_window.command:
            command.extend(self._tmux_shell_command(first_window.command))
        if self.runner.mutate(command) != 0:
            raise WorkError(f"tmux failed to create session {project.tmux.session!r}")
        print(f"ok  created tmux session {project.tmux.session!r}")

    def _reconcile_tmux_windows(self, project: ProjectConfig, *, fresh_boot: bool) -> None:
        if not project.tmux.windows:
            return
        existing = (
            {project.tmux.windows[0].name}
            if fresh_boot
            else self._tmux_window_names(project)
        )
        for window in project.tmux.windows:
            if window.name in existing:
                print(f"ok  tmux window {window.name!r} is running")
                continue
            self._create_tmux_window(project, window)

    def _tmux_window_names(self, project: ProjectConfig) -> set[str]:
        result = self.runner.capture(
            (
                self.commands.tmux,
                "list-windows",
                "-t",
                project.tmux.session,
                "-F",
                "#{window_name}",
            )
        )
        if result.returncode != 0:
            raise WorkError(f"tmux failed to inspect session {project.tmux.session!r}")
        return set(result.stdout.splitlines())

    def _create_tmux_window(self, project: ProjectConfig, window: TmuxWindowConfig) -> None:
        command = [
            self.commands.tmux,
            "new-window",
            "-d",
            "-t",
            project.tmux.session,
            "-n",
            window.name,
            "-c",
            str(window.root),
        ]
        if window.command:
            command.extend(self._tmux_shell_command(window.command))
        if self.runner.mutate(command) != 0:
            raise WorkError(f"tmux failed to create window {window.name!r}")
        print(f"ok  created tmux window {window.name!r}")

    @staticmethod
    def _tmux_shell_command(command: str) -> tuple[str, ...]:
        shell = os.environ.get("SHELL", "/bin/zsh")
        return (shell, "-lc", f"exec {command}")

    def _start_development_command(self, project: ProjectConfig) -> None:
        startup = project.startup
        assert startup.command is not None
        shell = os.environ.get("SHELL", "/bin/zsh")
        command = (
            self.commands.tmux,
            "new-window",
            "-d",
            "-t",
            project.tmux.session,
            "-n",
            startup.window,
            "-c",
            str(project.root),
            shell,
            "-lc",
            f"exec {startup.command}",
        )
        if self.runner.mutate(command) != 0:
            raise WorkError("tmux failed to start the development command")
        print(f"ok  started development command in window {startup.window!r}")

    def _open_vscode(self, project: ProjectConfig) -> None:
        vscode = project.vscode
        command = [self.commands.code, "--new-window"]
        if vscode.profile:
            command.extend(("--profile", vscode.profile))
        command.append(str(vscode.workspace or project.root))
        if (
            project.context.open_in_vscode
            and project.context.path
            and project.context.path != vscode.workspace
        ):
            command.append(str(project.context.path))
        if self.runner.mutate(command, cwd=project.root, detached=True) != 0:
            raise WorkError("VS Code failed to launch")
        print("ok  opened VS Code workspace")

    def _open_chrome(self, project: ProjectConfig) -> None:
        command = [
            self.commands.chrome,
            f"--profile-directory={project.chrome.profile}",
            "--new-window",
            *project.chrome.urls,
        ]
        if self.runner.mutate(command, cwd=project.root, detached=True) != 0:
            raise WorkError("Chrome failed to launch")
        print(f"ok  opened Chrome profile {project.chrome.profile!r}")
