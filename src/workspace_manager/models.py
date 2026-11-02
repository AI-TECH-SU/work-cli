from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CommandConfig:
    tmux: str
    code: str
    chrome: str
    desktop_names: str = "desktop-names"


@dataclass(frozen=True)
class FollowAppConfig:
    name: str
    bundle_id: str
    enabled: bool = True
    match_title: str | None = None


@dataclass(frozen=True)
class TmuxWindowConfig:
    name: str
    root: Path
    command: str | None = None


@dataclass(frozen=True)
class TmuxConfig:
    session: str
    windows: tuple[TmuxWindowConfig, ...] = field(default_factory=tuple)
    presentation: str = "current"
    launch_mode: str = "window"
    reuse_existing: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class VSCodeConfig:
    enabled: bool = True
    workspace: Path | None = None
    profile: str | None = None
    separate_instance: bool = False
    launch_mode: str = "window"
    reuse_existing: bool = True


@dataclass(frozen=True)
class ChromeConfig:
    enabled: bool = True
    profile: str = "Default"
    urls: tuple[str, ...] = field(default_factory=tuple)
    reopen_on_restore: bool = True
    launch_mode: str = "window"
    reuse_existing: bool = True


@dataclass(frozen=True)
class AppConfig:
    name: str
    bundle_id: str = ""
    enabled: bool = True
    launch_mode: str = "window"
    reuse_existing: bool = True
    open: tuple[str, ...] = field(default_factory=tuple)
    window_command: tuple[str, ...] = field(default_factory=tuple)
    instance_command: tuple[str, ...] = field(default_factory=tuple)
    match_title: str | None = None
    match_document: str | None = None
    extension: str | None = None
    options: dict[str, object] = field(default_factory=dict)
    # Compatibility metadata is derived from old project sections, never
    # accepted as user-authored [[apps]] settings.
    legacy_slot: str | None = None
    restore_missing: bool | None = None


@dataclass(frozen=True)
class ContextConfig:
    path: Path | None = None
    open_in_vscode: bool = True


@dataclass(frozen=True)
class SpaceConfig:
    enabled: bool = False
    manager: str = "desktop-names"
    target: str = "project"
    name: str | None = None
    uuid: str | None = None


@dataclass(frozen=True)
class StartupConfig:
    command: str | None = None
    window: str = "dev"
    check: str | None = None


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    root: Path
    tmux: TmuxConfig
    vscode: VSCodeConfig
    chrome: ChromeConfig
    context: ContextConfig
    startup: StartupConfig
    source: Path
    space: SpaceConfig = field(default_factory=SpaceConfig)
    apps: tuple[AppConfig, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class WorkspaceStatus:
    project: str
    tmux: bool | None
    startup: bool | None
    vscode: bool | None
    chrome: bool | None
    apps: dict[str, bool | None] = field(default_factory=dict)
    resources_ready: bool | None = None

    @property
    def booted(self) -> bool:
        return self.resources_ready if self.resources_ready is not None else bool(self.tmux)
