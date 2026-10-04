from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Iterator

from workspace_manager.errors import WorkError
from workspace_manager.apps import ConfiguredApps
from workspace_manager.models import (
    CommandConfig,
    ProjectConfig,
    TmuxWindowConfig,
    WorkspaceStatus,
)
from workspace_manager.paths import project_lock_path, state_home
from workspace_manager.system import CommandRunner, executable_exists, attach_tmux
from workspace_manager.desktop import DesktopNamesAdapter
from workspace_manager.terminals import TerminalWindows
from workspace_manager.windows import ProjectWindows


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
        self.desktop_uuid: str | None = None

    def focus(self, project: ProjectConfig, *, no_attach: bool = False,
              force_gui: bool = False, restart: bool = False) -> int:
        lock = nullcontext() if self.runner.dry_run else self.lock(project.name)
        with lock:
            desktop = DesktopNamesAdapter(self.commands.desktop_names, self.runner)
            if project.space.enabled and not restart and not force_gui and desktop.switch_existing(project):
                return 0
            if restart:
                self._validate_paths(project)
                self._validate_commands(project)
            if not no_attach and project.tmux.presentation == "windows" and project.tmux.launch_mode == "window":
                TerminalWindows(self.commands.tmux, self.runner).check_access()
            if restart:
                self._stop_services(project, lock_held=True)
            self.reconcile(project, force_gui=force_gui, assume_stopped=restart and self.runner.dry_run,
                           lock_held=True)
            if not no_attach and project.tmux.presentation == "windows":
                windows = (ProjectWindows(self.commands.desktop_names, self.runner, project, self.desktop_uuid)
                           if project.space.enabled else None)
                TerminalWindows(self.commands.tmux, self.runner).open(project, windows=windows)
                self._return_to_desktop(self.desktop_uuid)
        if not no_attach and project.tmux.presentation == "current" and not self.runner.dry_run:
            return attach_tmux(self.commands.tmux, project.tmux.session)
        return 0

    def stop(self, project: ProjectConfig) -> None:
        if not project.space.enabled or project.space.target != "project":
            self._stop_services(project)
            return
        lock = nullcontext() if self.runner.dry_run else self.lock(project.name)
        with lock:
            desktop = DesktopNamesAdapter(self.commands.desktop_names, self.runner)
            key = desktop.find(project)
            if key is None and not self.runner.dry_run:
                self._stop_services(project, lock_held=True)
                desktop.binding_path(project).unlink(missing_ok=True)
                print("ok  project desktop is already absent")
                return
            windows = ProjectWindows(self.commands.desktop_names, self.runner, project, key)
            self._return_to_desktop(key)
            # Honor editor/browser save prompts before stopping development services.
            windows.close_on_desktop(terminals=False)
            self._stop_services(project, lock_held=True)
            windows.close_on_desktop(terminals=True)
            windows.require_empty()
            desktop.remove(project, key or "pending-desktop")
            if not self.runner.dry_run:
                windows.path.unlink(missing_ok=True)
            print(f"ok  stopped {project.name!r}")

    @contextmanager
    def lock(self, project: str) -> Iterator[None]:
        path = project_lock_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkError(f"another Focus operation is active for {project!r}") from exc
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

    def status(self, project: ProjectConfig) -> WorkspaceStatus:
        apps = ConfiguredApps(self.commands.desktop_names, self.runner, project, commands=self.commands)
        states = apps.status()
        legacy = {resource.app.legacy_slot: states[resource.app.name]
                  for resource in apps.resources if resource.app.legacy_slot}
        return WorkspaceStatus(
            project=project.name, tmux=self.tmux_running(project), startup=self.startup_running(project),
            vscode=legacy.get("vscode"), chrome=legacy.get("chrome"),
            apps={resource.app.name: states[resource.app.name]
                  for resource in apps.resources if not resource.app.legacy_slot},
        )

    def reconcile(
        self,
        project: ProjectConfig,
        *,
        force_gui: bool = False,
        assume_stopped: bool = False,
        lock_held: bool = False,
    ) -> WorkspaceStatus:
        self._validate_paths(project)
        self._validate_commands(project)
        lock = nullcontext() if self.runner.dry_run or lock_held else self.lock(project.name)
        with lock:
            desktop_uuid = None
            if project.space.enabled:
                desktop_uuid = DesktopNamesAdapter(self.commands.desktop_names, self.runner).prepare(project)
            self.desktop_uuid = desktop_uuid
            windows = (ProjectWindows(self.commands.desktop_names, self.runner, project, desktop_uuid)
                       if project.space.enabled else None)
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

            self._return_to_desktop(desktop_uuid)
            ConfiguredApps(self.commands.desktop_names, self.runner, project, desktop_uuid,
                           commands=self.commands, windows=windows).restore(
                               force_gui=force_gui, fresh_boot=fresh_boot)
            self._return_to_desktop(desktop_uuid)

        return self.status(project) if not self.runner.dry_run else WorkspaceStatus(
            project=project.name,
            tmux=True,
            startup=True if project.startup.command else None,
            vscode=True if project.vscode.enabled else None,
            chrome=True if project.chrome.enabled else None,
            apps={app.name: True if app.enabled else None for app in project.apps},
        )

    def _return_to_desktop(self, key: str | None) -> None:
        if key and self.runner.mutate((self.commands.desktop_names, "switch", key)):
            raise WorkError("Could not return to the project desktop after opening its apps")

    def _stop_services(self, project: ProjectConfig, *, lock_held: bool = False) -> bool:
        lock = nullcontext() if self.runner.dry_run or lock_held else self.lock(project.name)
        with lock:
            TerminalWindows(self.commands.tmux, self.runner).stop(project)
            if not self.tmux_running(project):
                print(f"ok  tmux session {project.tmux.session!r} is already stopped")
                return False
            code = self.runner.mutate(
                (self.commands.tmux, "kill-session", "-t", project.tmux.session)
            )
            if code != 0:
                raise WorkError(f"tmux failed to stop session {project.tmux.session!r}")
            print("ok  project tmux session stopped")
            return True

    def _validate_paths(self, project: ProjectConfig) -> None:
        for window in project.tmux.windows:
            if not window.root.is_dir():
                raise WorkError(
                    f"tmux window root does not exist or is not a directory: {window.root}"
                )
        ConfiguredApps(self.commands.desktop_names, self.runner, project,
                       commands=self.commands).validate_paths()

    def _validate_commands(self, project: ProjectConfig) -> None:
        if not self.validate_executables:
            return
        required = [("tmux", self.commands.tmux)]
        required.extend(ConfiguredApps(self.commands.desktop_names, self.runner, project,
                                       commands=self.commands).required_commands())
        missing = [f"{label}: {command}" for label, command in required if not executable_exists(command)]
        if missing:
            raise WorkError(
                "required executable not found (run 'focus doctor'): " + ", ".join(missing)
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
        if self.runner.mutate(command) != 0:
            raise WorkError(f"tmux failed to create session {project.tmux.session!r}")
        if first_window and first_window.command:
            self._start_window_command(project, first_window)
        print(f"ok  created tmux session {project.tmux.session!r}")

    def _reconcile_tmux_windows(self, project: ProjectConfig, *, fresh_boot: bool) -> None:
        if not project.tmux.windows:
            return
        existing = (
            {project.tmux.windows[0].name: False}
            if fresh_boot
            else self._tmux_window_names(project)
        )
        for window in project.tmux.windows:
            if window.name in existing:
                if not existing[window.name]:
                    print(f"ok  tmux window {window.name!r} is running")
                continue
            self._create_tmux_window(project, window)

    def _tmux_window_names(self, project: ProjectConfig) -> dict[str, bool]:
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
        if result.returncode != 0:
            raise WorkError(f"tmux failed to inspect session {project.tmux.session!r}")
        running = {}
        for line in result.stdout.splitlines():
            name, _, dead = line.rpartition(":")
            if dead == "1":
                print(f"ERR tmux command in {name!r} exited; its output is retained. "
                      "Use 'focus restart' to retry after reviewing the error.")
            running[name] = dead == "1"
        return running

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
        if self.runner.mutate(command) != 0:
            raise WorkError(f"tmux failed to create window {window.name!r}")
        if window.command:
            self._start_window_command(project, window)
        print(f"ok  created tmux window {window.name!r}")

    def _start_window_command(self, project: ProjectConfig, window: TmuxWindowConfig) -> None:
        # Set retention before starting the service so even an immediate error
        # remains visible instead of deleting its window and the last session.
        target = f"{project.tmux.session}:{window.name}"
        commands = (
            (self.commands.tmux, "set-window-option", "-t", target, "remain-on-exit", "on"),
            (self.commands.tmux, "respawn-pane", "-k", "-t", target, "-c", str(window.root),
             *self._tmux_shell_command(window.command or "")),
        )
        for command in commands:
            if self.runner.mutate(command) != 0:
                raise WorkError(f"tmux failed to start command for {window.name!r}")

    @staticmethod
    def _tmux_shell_command(command: str) -> tuple[str, ...]:
        shell = os.environ.get("SHELL", "/bin/zsh")
        return (shell, "-lc", f"exec {command}")

    def _start_development_command(self, project: ProjectConfig) -> None:
        startup = project.startup
        assert startup.command is not None
        self._create_tmux_window(project, TmuxWindowConfig(startup.window, project.root, startup.command))
        print(f"ok  started development command in window {startup.window!r}")
