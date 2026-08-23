from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CommandConfig:
    tmux: str
    code: str
    chrome: str


@dataclass(frozen=True)
class TmuxConfig:
    session: str


@dataclass(frozen=True)
class VSCodeConfig:
    enabled: bool = True
    workspace: Path | None = None
    profile: str | None = None


@dataclass(frozen=True)
class ChromeConfig:
    enabled: bool = True
    profile: str = "Default"
    urls: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ContextConfig:
    path: Path | None = None
    open_in_vscode: bool = True


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


@dataclass(frozen=True)
class WorkspaceStatus:
    project: str
    tmux: bool
    startup: bool | None
    vscode: bool | None
    chrome: bool | None

    @property
    def booted(self) -> bool:
        return self.tmux
