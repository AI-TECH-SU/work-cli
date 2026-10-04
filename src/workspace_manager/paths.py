from __future__ import annotations

import os
from pathlib import Path


def _application_home(base: Path) -> Path:
    # Both executable names resolve to the same location. Existing installations
    # retain their files; new installations use Focus's namespace.
    if (base / "focus").exists() or not (base / "work").exists():
        return base / "focus"
    return base / "work"


def config_home() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return _application_home(base)


def state_home() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return _application_home(base)


def global_config_path() -> Path:
    return config_home() / "config.toml"


def projects_dir() -> Path:
    return config_home() / "projects"


def project_config_path(name: str) -> Path:
    return projects_dir() / f"{name}.toml"


def project_lock_path(name: str) -> Path:
    return state_home() / "locks" / f"{name}.lock"
