from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from workspace_manager.errors import WorkError
from workspace_manager.extensions import load_extension, validate_options
from workspace_manager.models import (
    AppConfig,
    ChromeConfig,
    CommandConfig,
    ContextConfig,
    ProjectConfig,
    SpaceConfig,
    StartupConfig,
    TmuxConfig,
    TmuxWindowConfig,
    VSCodeConfig,
)
from workspace_manager.paths import global_config_path, project_config_path, projects_dir


PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
RESERVED_PROJECT_NAMES = {"boot", "doctor", "edit", "init", "list", "restart", "status", "stop"}
DEFAULT_TMUX_PATHS = ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux")
DEFAULT_DESKTOP_NAMES_PATHS = ("/opt/homebrew/bin/desktop-names", "/usr/local/bin/desktop-names")


def validate_project_name(name: str) -> str:
    if not PROJECT_NAME.fullmatch(name):
        raise WorkError(
            "project names must start with a letter or number and contain only "
            "letters, numbers, '-' and '_'"
        )
    if name in RESERVED_PROJECT_NAMES:
        raise WorkError(f"{name!r} is reserved for a Focus command; choose another project name")
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
    def extension_command(extension_id: str) -> str:
        declaration = load_extension(extension_id).data["command"]
        return _discover_command(declaration["name"], tuple(declaration.get("candidates", ())))

    return CommandConfig(
        tmux=_discover_command("tmux", DEFAULT_TMUX_PATHS),
        code=extension_command("vscode"),
        chrome=extension_command("chrome"),
        desktop_names=_discover_command("desktop-names", DEFAULT_DESKTOP_NAMES_PATHS),
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


def _launch_mode(table: dict[str, Any], section: str) -> str:
    legacy = _boolean(table, "separate_instance", False) if section == "vscode" else False
    mode = _string(table, "launch_mode", "instance" if legacy else "window")
    if mode not in {"window", "instance"}:
        raise WorkError(f"{section}.launch_mode must be 'window' or 'instance'")
    if section == "vscode" and "separate_instance" in table and legacy != (mode == "instance"):
        raise WorkError("vscode.launch_mode conflicts with vscode.separate_instance; keep only launch_mode")
    return mode


def _integer(
    table: dict[str, Any], key: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise WorkError(f"{key} must be between {minimum} and {maximum}")
    return value


def _tmux_windows(tmux: dict[str, Any], root: Path) -> tuple[TmuxWindowConfig, ...]:
    value = tmux.get("windows", [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise WorkError("tmux.windows must be an array of tables")

    windows: list[TmuxWindowConfig] = []
    names: set[str] = set()
    for item in value:
        name = _string(item, "name")
        if name is None:
            raise WorkError("each tmux window requires a non-empty name")
        if any(character in name for character in ".:"):
            raise WorkError("tmux window names cannot contain '.' or ':'")
        if name in names:
            raise WorkError(f"duplicate tmux window name: {name!r}")
        names.add(name)

        window_root = _resolve(root, _string(item, "root", ".")) or root
        windows.append(
            TmuxWindowConfig(
                name=name,
                root=window_root,
                command=_string(item, "command"),
            )
        )
    return tuple(windows)


def _resolve(root: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _app_strings(item: dict[str, Any], key: str, section: str) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(part, str) and part.strip() and "\0" not in part for part in value
    ):
        raise WorkError(f"{section}.{key} must be an array of non-empty strings")
    if key.endswith("_command") and key in item and not value:
        raise WorkError(f"{section}.{key} cannot be empty; omit it to use the default launcher")
    # Command arguments are literal argv; never trim them or parse shell syntax.
    return tuple(value)


def _app_match(item: dict[str, Any], key: str, section: str) -> str | None:
    if key not in item:
        return None
    value = item[key]
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise WorkError(f"{section}.{key} must be a non-empty glob pattern")
    if not re.search(r"[^\s*?\[\]!]", value):
        raise WorkError(f"{section}.{key} must identify the project; wildcard-only patterns are unsafe")
    return value


def _apps(data: dict[str, Any], root: Path) -> tuple[AppConfig, ...]:
    value = data.get("apps", [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise WorkError("apps must be an array of tables ([[apps]])")
    allowed = {
        "name", "bundle_id", "enabled", "launch_mode", "reuse_existing", "open",
        "window_command", "instance_command", "match_title", "match_document",
        "extension", "options",
    }
    apps: list[AppConfig] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        section = f"apps[{index}]"
        unknown = set(item) - allowed
        if unknown:
            raise WorkError(f"{section}: unknown setting(s): {', '.join(sorted(unknown))}")
        name = _string(item, "name")
        if name is None or not PROJECT_NAME.fullmatch(name):
            raise WorkError(
                f"{section}.name must start with a letter or number and contain only "
                "letters, numbers, '-' and '_'"
            )
        if name in names:
            raise WorkError(f"duplicate app name: {name!r}")
        names.add(name)
        extension = _string(item, "extension")
        if "extension" in item and (extension is None or not PROJECT_NAME.fullmatch(extension)):
            raise WorkError(f"{section}.extension must be an extension identifier containing letters, numbers, '-' or '_'")
        options = item.get("options", {})
        if not isinstance(options, dict):
            raise WorkError(f"{section}.options must be a TOML table")
        if extension:
            conflicts = {"bundle_id", "open", "window_command", "instance_command"} & set(item)
            if conflicts:
                raise WorkError(
                    f"{section}.extension cannot be combined with {', '.join(sorted(conflicts))}; "
                    "provide extension options instead"
                )
            options = validate_options(load_extension(extension), options, root)
            bundle_id = ""
        else:
            if "options" in item:
                raise WorkError(f"{section}.options requires an extension")
            bundle_id = _string(item, "bundle_id")
            if bundle_id is None or "\0" in bundle_id:
                raise WorkError(f"{section}.bundle_id must be a non-empty string, or provide extension")
        if "launch_mode" in item and not _string(item, "launch_mode"):
            raise WorkError(f"{section}.launch_mode must be 'window' or 'instance'")
        mode = _launch_mode(item, section)
        targets = _app_strings(item, "open", section)
        window_command = _app_strings(item, "window_command", section)
        instance_command = _app_strings(item, "instance_command", section)
        if targets and (window_command if mode == "window" else instance_command):
            raise WorkError(
                f"{section}.open cannot be combined with {mode}_command; "
                "include documents or URLs in that command's arguments instead"
            )
        apps.append(AppConfig(
            name=name,
            bundle_id=bundle_id,
            enabled=_boolean(item, "enabled", True),
            launch_mode=mode,
            reuse_existing=_boolean(item, "reuse_existing", True),
            open=tuple(
                target if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target)
                else str(_resolve(root, target))
                for target in targets
            ),
            window_command=window_command,
            instance_command=instance_command,
            match_title=_app_match(item, "match_title", section),
            match_document=_app_match(item, "match_document", section),
            extension=extension,
            options=options,
        ))
    return tuple(apps)


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
        desktop_names=_string(commands, "desktop_names", defaults.desktop_names) or defaults.desktop_names,
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
    space = _table(data, "space")
    apps = _apps(data, root)
    explicit_extensions = {app.extension for app in apps if app.extension}
    legacy_enabled = {}
    for section, table in (("vscode", vscode), ("chrome", chrome)):
        # Declaring the applications list makes it authoritative. Old projects
        # without that list retain their historical app defaults.
        enabled = _boolean(table, "enabled", section in data or "apps" not in data)
        if section in explicit_extensions:
            if section in data and enabled:
                raise WorkError(
                    f"[{section}] and [[apps]] extension = {section!r} configure the same app; "
                    f"remove [{section}] or set its enabled = false"
                )
            enabled = False
        legacy_enabled[section] = enabled

    prefix = "work" if source.parent.parent.name == "work" else "focus"
    session = _string(tmux, "session", f"{prefix}-{name}") or f"{prefix}-{name}"
    if any(character in session for character in ".:"):
        raise WorkError("tmux.session cannot contain '.' or ':'")
    tmux_windows = _tmux_windows(tmux, root)
    presentation = _string(tmux, "presentation", "current")
    if presentation not in {"current", "windows"}:
        raise WorkError("tmux.presentation must be 'current' or 'windows'")

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

    space_manager = _string(space, "manager", "desktop-names") or "desktop-names"
    if space_manager != "desktop-names":
        raise WorkError("space.manager must be 'desktop-names'; replace the retired yabai configuration")
    space_target = _string(space, "target", "project") or "project"
    if space_target not in {"current", "project"}:
        raise WorkError("space.target must be 'current' or 'project'")


    if _string(space, "uuid") and space_target != "project":
        raise WorkError("space.uuid requires space.target = 'project'")

    project = ProjectConfig(
        name=name,
        root=root,
        tmux=TmuxConfig(session=session, windows=tmux_windows, presentation=presentation,
                        launch_mode=_launch_mode(tmux, "tmux"),
                        reuse_existing=_boolean(tmux, "reuse_existing", True)),
        vscode=VSCodeConfig(
            enabled=legacy_enabled["vscode"],
            workspace=workspace or root,
            profile=_string(vscode, "profile"),
            separate_instance=_boolean(vscode, "separate_instance", False),
            launch_mode=_launch_mode(vscode, "vscode"),
            reuse_existing=_boolean(vscode, "reuse_existing", True),
        ),
        chrome=ChromeConfig(
            enabled=legacy_enabled["chrome"],
            profile=_string(chrome, "profile", "Default") or "Default",
            urls=urls,
            reopen_on_restore=_boolean(chrome, "reopen_on_restore", True),
            launch_mode=_launch_mode(chrome, "chrome"),
            reuse_existing=_boolean(chrome, "reuse_existing", True),
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
        space=SpaceConfig(
            enabled=_boolean(space, "enabled", False),
            manager=space_manager,
            target=space_target,
            name=_string(space, "name"),
            uuid=_string(space, "uuid"),
        ),
        apps=apps,
    )
    # Resolve compatibility resources here as well as at runtime so duplicate
    # names and extension option errors fail before any desktop mutation.
    from workspace_manager.app_compat import configured_apps

    configured_apps(project)
    return project


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
            f"desktop_names = {toml_quote(commands.desktop_names)}",
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
    desktop: bool = False,
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
        f"session = {toml_quote(f'focus-{name}')}",
        'launch_mode = "window"',
        'reuse_existing = true',
        "",
        "[[apps]]",
        'name = "vscode"',
        'extension = "vscode"',
        "enabled = true",
        'launch_mode = "window"',
        'reuse_existing = true',
        "[apps.options]",
        f"workspace = {toml_quote(workspace_value)}",
        f"context = {toml_quote(context_value)}",
    ]
    if vscode_profile:
        lines.append(f"profile = {toml_quote(vscode_profile)}")
    lines.extend(
        [
            "",
            "[[apps]]",
            'name = "chrome"',
            'extension = "chrome"',
            f"enabled = {'true' if chrome_profile else 'false'}",
            'launch_mode = "window"',
            'reuse_existing = true',
            "[apps.options]",
            f"profile = {toml_quote(chrome_profile or 'Default')}",
            f"urls = [{url_list}]",
            "",
            "[startup]",
        ]
    )
    if startup:
        lines.append(f"command = {toml_quote(startup)}")
    lines.extend(("window = \"dev\"", ""))
    if desktop:
        lines.extend(("[space]", "enabled = true", 'manager = "desktop-names"', 'target = "project"', ""))
    return "\n".join(lines)
