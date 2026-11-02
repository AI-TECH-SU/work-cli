from __future__ import annotations

import fcntl
from contextlib import contextmanager, nullcontext
from typing import Iterator

from workspace_manager.apps import ConfiguredApps
from workspace_manager.desktop import DesktopNamesAdapter
from workspace_manager.errors import WorkError
from workspace_manager.follow_apps import FollowApps
from workspace_manager.models import CommandConfig, FollowAppConfig, ProjectConfig, WorkspaceStatus
from workspace_manager.paths import project_lock_path
from workspace_manager.system import CommandRunner, executable_exists
from workspace_manager.windows import ProjectWindows


class WorkspaceManager:
    def __init__(self, commands: CommandConfig, runner: CommandRunner, *,
                 validate_executables: bool = True, follow_apps: tuple[FollowAppConfig, ...] = ()) -> None:
        self.commands = commands
        self.runner = runner
        self.validate_executables = validate_executables
        self.desktop_uuid: str | None = None
        self.follow_apps = FollowApps(follow_apps)

    def _resources(self, project: ProjectConfig, *, desktop: str | None = None,
                   windows: ProjectWindows | None = None) -> ConfiguredApps:
        return ConfiguredApps(self.commands.desktop_names, self.runner, project, desktop,
                              commands=self.commands, windows=windows)

    def focus(self, project: ProjectConfig, *, no_attach: bool = False,
              force_gui: bool = False, restart: bool = False) -> int:
        lock = nullcontext() if self.runner.dry_run else self.lock(project.name)
        with lock:
            desktop = DesktopNamesAdapter(self.commands.desktop_names, self.runner)
            if project.space.enabled and not restart and not force_gui and desktop.switch_existing(project):
                if self.follow_apps.enabled:
                    self._move_follow_apps(project, desktop.find(project))
                return 0
            resources = self._resources(project)
            self._validate_paths(project)
            self._validate_commands(project)
            if not no_attach:
                resources.preflight_sessions()
            if restart:
                self._stop_services(project, lock_held=True)
            self.reconcile(project, force_gui=force_gui, assume_stopped=restart and self.runner.dry_run,
                           lock_held=True)
            if not no_attach:
                self._resources(project, desktop=self.desktop_uuid).present_sessions()
                self._return_to_desktop(self.desktop_uuid)
        if not no_attach and not self.runner.dry_run:
            return resources.attach_sessions()
        return 0

    def stop(self, project: ProjectConfig) -> None:
        if not project.space.enabled or project.space.target != "project":
            self._stop_services(project)
            return
        lock = nullcontext() if self.runner.dry_run else self.lock(project.name)
        with lock:
            resources = self._resources(project)
            desktop = DesktopNamesAdapter(self.commands.desktop_names, self.runner)
            key = desktop.find(project)
            if key is None and not self.runner.dry_run:
                self._stop_services(project, lock_held=True)
                desktop.binding_path(project).unlink(missing_ok=True)
                resources.clear_launch_state()
                print("ok  project desktop is already absent")
                return
            windows = ProjectWindows(self.commands.desktop_names, self.runner, project, key)
            self._return_to_desktop(key)
            deferred = resources.deferred_close_bundles
            self.follow_apps.preserve_on_stop(windows, desktop)
            # Save/close prompts must resolve before stopping project services.
            windows.close_on_desktop(exclude_bundles=deferred)
            self._stop_services(project, lock_held=True)
            if deferred:
                windows.close_on_desktop(include_bundles=deferred)
            windows.require_empty()
            desktop.remove(project, key or "pending-desktop")
            resources.clear_launch_state()
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

    def status(self, project: ProjectConfig, *, planned: bool = False) -> WorkspaceStatus:
        resources = self._resources(project)
        states = ({resource.app.name: True if resource.app.enabled else None for resource in resources.resources}
                  if planned else resources.status())
        legacy = {resource.app.legacy_slot: states[resource.app.name]
                  for resource in resources.resources if resource.app.legacy_slot}
        startup = None
        for resource in resources.session_resources:
            if resource.app.legacy_slot:
                startup = (True if resource.extension.options.get("startup_command") else None) if planned else resource.extension.startup_status(self.runner)
        enabled = [state for state in states.values() if state is not None]
        return WorkspaceStatus(
            project=project.name, tmux=legacy.get("tmux"), startup=startup,
            vscode=legacy.get("vscode"), chrome=legacy.get("chrome"),
            apps={resource.app.name: states[resource.app.name]
                  for resource in resources.resources if not resource.app.legacy_slot},
            resources_ready=bool(enabled) and all(enabled) and startup is not False,
        )

    def reconcile(self, project: ProjectConfig, *, force_gui: bool = False,
                  assume_stopped: bool = False, lock_held: bool = False) -> WorkspaceStatus:
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
            resources = self._resources(project, desktop=desktop_uuid, windows=windows)
            fresh_boot = resources.restore_sessions(assume_stopped=assume_stopped)
            self._return_to_desktop(desktop_uuid)
            resources.restore(force_gui=force_gui, fresh_boot=fresh_boot)
            if project.space.enabled:
                self._move_follow_apps(project, desktop_uuid)
            self._return_to_desktop(desktop_uuid)
        return self.status(project, planned=self.runner.dry_run)

    def _move_follow_apps(self, project: ProjectConfig, desktop: str | None) -> None:
        if self.follow_apps.enabled:
            windows = ProjectWindows(self.commands.desktop_names, self.runner, project, desktop)
            self.follow_apps.move(windows)

    def _return_to_desktop(self, key: str | None) -> None:
        if key and self.runner.mutate((self.commands.desktop_names, "switch", key)):
            raise WorkError("Could not return to the project desktop after opening its apps")

    def _stop_services(self, project: ProjectConfig, *, lock_held: bool = False) -> bool:
        lock = nullcontext() if self.runner.dry_run or lock_held else self.lock(project.name)
        with lock:
            return self._resources(project).stop_sessions()

    def _validate_paths(self, project: ProjectConfig) -> None:
        self._resources(project).validate_paths()

    def _validate_commands(self, project: ProjectConfig) -> None:
        if not self.validate_executables:
            return
        missing = [f"{label}: {command}" for label, command in self._resources(project).required_commands()
                   if not executable_exists(command)]
        if missing:
            raise WorkError("required executable not found (run 'focus doctor'): " + ", ".join(missing))
