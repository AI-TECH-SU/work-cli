from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from workspace_manager.errors import WorkError
from workspace_manager.models import (
    ChromeConfig,
    CommandConfig,
    ContextConfig,
    ProjectConfig,
    StartupConfig,
    TmuxConfig,
    VSCodeConfig,
)
from workspace_manager.paths import global_config_path, project_config_path, projects_dir


PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
RESERVED_PROJECT_NAMES = {"boot", "doctor", "edit", "init", "list", "restart", "status", "stop"}
DEFAULT_CODE_PATHS = (
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code",
)
DEFAULT_TMUX_PATHS = ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux")
DEFAULT_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
)


def validate_project_name(name: str) -> str:
    if not PROJECT_NAME.fullmatch(name):
        raise WorkError(
            "project names must start with a letter or number and contain only "
            "letters, numbers, '-' and '_'"
        )
    if name in RESERVED_PROJECT_NAMES:
        raise WorkError(f"{name!r} is reserved for a work command; choose another project name")
    return name


def _discover_command(name: str, candidates: tuple[str, ...] = ()) -> str:
    found = shutil.which(name)
    if found:
        return found
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return name


def default_commands() -> CommandConfig:
    return CommandConfig(
        tmux=_discover_command("tmux", DEFAULT_TMUX_PATHS),
        code=_discover_command("code", DEFAULT_CODE_PATHS),
        chrome=_discover_command("google-chrome", DEFAULT_CHROME_PATHS),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise WorkError(f"configuration not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise WorkError(f"invalid TOML in {path}: {exc}") from exc


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise WorkError(f"[{key}] must be a TOML table")
    return value


def _string(table: dict[str, Any], key: str, default: str | None = None) -> str | None:
    value = table.get(key, default)
    if value is not None and not isinstance(value, str):
        raise WorkError(f"{key} must be a string")
    return value.strip() if isinstance(value, str) and value.strip() else default


def _boolean(table: dict[str, Any], key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise WorkError(f"{key} must be true or false")
    return value


def _resolve(root: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def load_commands() -> CommandConfig:
    defaults = default_commands()
    path = global_config_path()
    if not path.exists():
        return defaults
    data = _read_toml(path)
    if data.get("version", 1) != 1:
        raise WorkError(f"unsupported global configuration version in {path}")
    commands = _table(data, "commands")
    tmux = _string(commands, "tmux", defaults.tmux) or defaults.tmux
    # An initializer run before Homebrew installed tmux may have persisted the
    # bare fallback. Prefer a newly discoverable absolute Homebrew path later.
    if tmux == "tmux" and defaults.tmux != "tmux":
        tmux = defaults.tmux
    return CommandConfig(
        tmux=tmux,
        code=_string(commands, "code", defaults.code) or defaults.code,
        chrome=_string(commands, "chrome", defaults.chrome) or defaults.chrome,
    )


def load_project(name: str) -> ProjectConfig:
    validate_project_name(name)
    source = project_config_path(name)
    data = _read_toml(source)
    if data.get("version") != 1:
        raise WorkError(f"{source}: version must be 1")

    root_value = data.get("root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise WorkError(f"{source}: root must be a non-empty string")
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise WorkError(f"project root does not exist or is not a directory: {root}")

    tmux = _table(data, "tmux")
    vscode = _table(data, "vscode")
    chrome = _table(data, "chrome")
    context = _table(data, "context")
    startup = _table(data, "startup")

    session = _string(tmux, "session", f"work-{name}") or f"work-{name}"
    if any(character in session for character in ".:"):
        raise WorkError("tmux.session cannot contain '.' or ':'")

    workspace = _resolve(root, _string(vscode, "workspace"))
    context_path = _resolve(root, _string(context, "path", "PROJECT.md"))
    urls_value = chrome.get("urls", [])
    if not isinstance(urls_value, list) or not all(isinstance(url, str) for url in urls_value):
        raise WorkError("chrome.urls must be an array of strings")
    urls = tuple(url.strip() for url in urls_value if url.strip())
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WorkError(f"unsupported Chrome URL: {url!r}; use http or https")

    return ProjectConfig(
        name=name,
        root=root,
        tmux=TmuxConfig(session=session),
        vscode=VSCodeConfig(
            enabled=_boolean(vscode, "enabled", True),
            workspace=workspace or root,
            profile=_string(vscode, "profile"),
        ),
        chrome=ChromeConfig(
            enabled=_boolean(chrome, "enabled", True),
            profile=_string(chrome, "profile", "Default") or "Default",
            urls=urls,
        ),
        context=ContextConfig(
            path=context_path,
            open_in_vscode=_boolean(context, "open_in_vscode", True),
        ),
        startup=StartupConfig(
            command=_string(startup, "command"),
            window=_string(startup, "window", "dev") or "dev",
            check=_string(startup, "check"),
        ),
        source=source,
    )


def list_project_names() -> list[str]:
    directory = projects_dir()
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.toml") if PROJECT_NAME.fullmatch(path.stem))


def toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_global_config(commands: CommandConfig) -> str:
    return "\n".join(
        (
            "version = 1",
            "",
            "[commands]",
            f"tmux = {toml_quote(commands.tmux)}",
            f"code = {toml_quote(commands.code)}",
            f"chrome = {toml_quote(commands.chrome)}",
            "",
        )
    )


def render_project_config(
    *,
    name: str,
    root: Path,
    workspace: Path | None,
    vscode_profile: str | None,
    chrome_profile: str | None,
    urls: list[str],
    context: Path | None,
    startup: str | None,
) -> str:
    def relative(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(path.resolve())

    workspace_value = relative(workspace) or "."
    context_value = relative(context) or "PROJECT.md"
    url_list = ", ".join(toml_quote(url) for url in urls)
    lines = [
        "version = 1",
        f"root = {toml_quote(str(root.resolve()))}",
        "",
        "[tmux]",
        f"session = {toml_quote(f'work-{name}')}",
        "",
        "[vscode]",
        "enabled = true",
        f"workspace = {toml_quote(workspace_value)}",
    ]
    if vscode_profile:
        lines.append(f"profile = {toml_quote(vscode_profile)}")
    lines.extend(
        [
            "",
            "[chrome]",
            f"enabled = {'true' if chrome_profile else 'false'}",
            f"profile = {toml_quote(chrome_profile or 'Default')}",
            f"urls = [{url_list}]",
            "",
            "[context]",
            f"path = {toml_quote(context_value)}",
            "open_in_vscode = true",
            "",
            "[startup]",
        ]
    )
    if startup:
        lines.append(f"command = {toml_quote(startup)}")
    lines.extend(("window = \"dev\"", ""))
    return "\n".join(lines)
