from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from workspace_manager.app_compat import configured_apps
from workspace_manager.errors import WorkError
from workspace_manager.extensions import resolve_extension
from workspace_manager.models import AppConfig, CommandConfig, ProjectConfig
from workspace_manager.system import CommandRunner
from workspace_manager.windows import ProjectWindows


def _literal_command(project: ProjectConfig, command: tuple[str, ...]) -> tuple[str, ...]:
    executable = command[0]
    if "/" in executable and not Path(executable).is_absolute():
        executable = str(project.root / executable)
    return (executable, *command[1:])


class AppResource:
    """One application resource, optionally configured by an extension specification."""

    def __init__(self, app: AppConfig, project: ProjectConfig, commands: CommandConfig):
        self.app, self.project = app, project
        self.extension = resolve_extension(app, project, commands) if app.extension else None
        self.slot = app.legacy_slot or f"app:{app.name}"
        self.bundle_id = self.extension.bundle_id if self.extension else app.bundle_id

    def command(self) -> tuple[str, ...]:
        if self.extension:
            return self.extension.command(self.app.launch_mode)
        override = self.app.window_command if self.app.launch_mode == "window" else self.app.instance_command
        if override:
            return _literal_command(self.project, override)
        return ("/usr/bin/open", *(("-n",) if self.app.launch_mode == "instance" else ()),
                "-b", self.bundle_id, *self.app.open)

    @property
    def uses_probe(self) -> bool:
        return bool(self.extension and self.extension.has_status(self.app.launch_mode)
                    and not (self.app.match_title or self.app.match_document))

    @property
    def binding_context(self) -> str:
        data = {key: getattr(self.app, key) for key in (
            "bundle_id", "launch_mode", "open", "window_command", "instance_command",
            "match_title", "match_document",
        )}
        data["root"] = str(self.project.root.resolve())
        if self.extension:
            data["extension"] = self.extension.binding_context
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def matches(self, window: dict) -> bool:
        custom = bool(self.app.match_title or self.app.match_document)
        extension_match = bool(self.extension and self.extension.has_match)
        return ((not custom or ConfiguredApps.matches(self.app, window))
                and (not extension_match or self.extension.matches(window))
                and (custom or extension_match))

    def launch(self, runner: CommandRunner) -> None:
        if self.extension:
            detached = self.extension.detached
        else:
            override = self.app.window_command if self.app.launch_mode == "window" else self.app.instance_command
            detached = bool(override)
        if runner.mutate(self.command(), cwd=self.project.root, detached=detached):
            raise WorkError(f"Could not launch configured app {self.app.name!r}")
        print(f"ok  requested {self.app.name!r} application window")


class ConfiguredApps:
    def __init__(self, command: str, runner: CommandRunner, project: ProjectConfig,
                 desktop: str | None = None, *, commands: CommandConfig | None = None,
                 windows: ProjectWindows | None = None):
        self.runner, self.project = runner, project
        self.windows = windows or ProjectWindows(command, runner, project, desktop)
        self.commands = commands or CommandConfig("tmux", "code", "google-chrome", command)
        self.resources = tuple(AppResource(app, project, self.commands) for app in configured_apps(project))

    @staticmethod
    def launch_command(project: ProjectConfig, app: AppConfig,
                       commands: CommandConfig | None = None) -> tuple[str, ...]:
        return AppResource(app, project, commands or CommandConfig("tmux", "code", "google-chrome")).command()

    def binding_context(self, app: AppConfig) -> str:
        return AppResource(app, self.project, self.commands).binding_context

    @staticmethod
    def matches(app: AppConfig, window: dict) -> bool:
        if not (app.match_title or app.match_document):
            return False
        if app.match_title and not fnmatchcase(window["title"], app.match_title):
            return False
        document = window["document"]
        if document.startswith("file:"):
            document = str(Path(unquote(urlparse(document).path)).resolve())
        pattern = app.match_document
        if pattern and pattern.startswith("/"):
            pattern = str(Path(pattern).resolve())
        return not pattern or fnmatchcase(document, pattern)

    @property
    def needs_inventory(self) -> bool:
        return self.project.space.enabled or any(resource.app.enabled and not resource.uses_probe
                                                 for resource in self.resources)

    def required_commands(self) -> list[tuple[str, str]]:
        required = []
        if self.needs_inventory:
            required.append(("Desktop Names", self.commands.desktop_names))
        for resource in self.resources:
            if not resource.app.enabled:
                continue
            required.append((f"app {resource.app.name}", resource.command()[0]))
            if resource.uses_probe:
                probe = resource.extension.status_command(resource.app.launch_mode)
                if probe[0] != resource.command()[0]:
                    required.append((f"app {resource.app.name} status", probe[0]))
        return required

    def validate_paths(self) -> None:
        for resource in self.resources:
            if resource.app.enabled and resource.extension:
                resource.extension.validate_paths()

    def restore(self, *, force_gui: bool = False, fresh_boot: bool = False) -> None:
        peers = tuple(resource.slot for resource in self.resources if resource.app.enabled)
        for resource in self.resources:
            app, extension = resource.app, resource.extension
            if not app.enabled:
                continue
            if self.project.space.enabled or not resource.uses_probe:
                self.windows.ensure(
                    resource.slot, resource.bundle_id, lambda: resource.launch(self.runner), resource.matches,
                    reuse_existing=app.reuse_existing,
                    require_match=bool(app.match_title or app.match_document or (extension and extension.require_match)),
                    binding_context=resource.binding_context,
                    adopt_single_on_desktop=bool(extension and extension.adopt_single_on_desktop
                                                and not (app.match_title or app.match_document)
                                                and sum(other.app.enabled and other.bundle_id == resource.bundle_id
                                                        for other in self.resources) == 1),
                    allow_unversioned_binding=bool(app.legacy_slot),
                    peer_slots=peers,
                )
            else:
                reopen = app.restore_missing if app.restore_missing is not None else extension.reopen_on_restore
                if (self.runner.dry_run or force_gui or not app.reuse_existing
                        or (fresh_boot and extension.restore_on_fresh_boot)
                        or (reopen and not extension.running(self.runner, app.launch_mode))):
                    resource.launch(self.runner)
                else:
                    print(f"ok  keeping {app.name!r} application")

    def status(self) -> dict[str, bool | None]:
        result = {}
        for resource in self.resources:
            app = resource.app
            if not app.enabled:
                result[app.name] = None
            elif resource.uses_probe:
                result[app.name] = resource.extension.running(self.runner, app.launch_mode)
            else:
                result[app.name] = self.windows.find_existing(
                    resource.slot, resource.bundle_id, resource.matches,
                    binding_context=resource.binding_context,
                    allow_unversioned_binding=bool(app.legacy_slot),
                    peer_slots=tuple(other.slot for other in self.resources if other.app.enabled),
                ) is not None
        return result
