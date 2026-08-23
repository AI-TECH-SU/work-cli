from __future__ import annotations

import os
from pathlib import Path


def config_home() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "work"


def state_home() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "work"


def global_config_path() -> Path:
    return config_home() / "config.toml"


def projects_dir() -> Path:
    return config_home() / "projects"


def project_config_path(name: str) -> Path:
    return projects_dir() / f"{name}.toml"


def project_lock_path(name: str) -> Path:
    return state_home() / "locks" / f"{name}.lock"
