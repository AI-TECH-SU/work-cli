from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from workspace_manager import __version__
from workspace_manager.apps import ConfiguredApps
from workspace_manager.config import (
    default_commands,
    list_project_names,
    load_commands,
    load_follow_apps,
    load_project,
    render_global_config,
    render_project_config,
    validate_project_name,
)
from workspace_manager.errors import WorkError
from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import CommandConfig, WorkspaceStatus
from workspace_manager.paths import global_config_path, project_config_path
from workspace_manager.system import CommandRunner, executable_exists, print_error


COMMANDS = {"init", "edit", "status", "stop", "restart", "doctor", "list"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="focus",
        description="Restore a local project workspace on the current macOS desktop.",
    )
    parser.add_argument("--version", action="version", version=f"focus {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    boot = subparsers.add_parser(
        "boot",
        help="restore a project workspace (normally invoked as: focus <project>)",
    )
    boot.add_argument("project")
    boot.add_argument("--dry-run", action="store_true")
    boot.add_argument("--no-attach", action="store_true")
    boot.add_argument("--force-gui", action="store_true", help="repair the desktop's windows instead of only switching")

    init = subparsers.add_parser("init", help="create a project configuration")
    init.add_argument("project")
    init.add_argument("--root")
    init.add_argument("--workspace")
    init.add_argument("--vscode-profile")
    init.add_argument("--chrome-profile")
    init.add_argument("--url", action="append", default=[])
    init.add_argument("--context")
    init.add_argument("--startup")
    init.add_argument("--non-interactive", action="store_true")
    init.add_argument("--force", action="store_true")
    init.add_argument("--desktop", action="store_true", help="name and remember this project’s desktop using Desktop Names")

    edit = subparsers.add_parser("edit", help="edit a project configuration")
    edit.add_argument("project")

    status = subparsers.add_parser("status", help="show workspace status")
    status.add_argument("project", nargs="?")
    status.add_argument("--json", action="store_true")

    stop = subparsers.add_parser("stop", help="close a project's desktop windows, stop services, and remove its desktop")
    stop.add_argument("project")
    stop.add_argument("--dry-run", action="store_true")

    restart = subparsers.add_parser("restart", help="restart and restore a workspace")
    restart.add_argument("project")
    restart.add_argument("--dry-run", action="store_true")
    restart.add_argument("--no-attach", action="store_true")

    doctor = subparsers.add_parser("doctor", help="check or repair local requirements")
    doctor.add_argument(
        "--fix",
        action="store_true",
        help="offer to install missing dependencies declared by enabled extensions",
    )
    doctor.add_argument(
        "--yes",
        action="store_true",
        help="approve dependency installation without prompting (requires --fix)",
    )
    subparsers.add_parser("list", help="list configured projects")
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS and argv[0] != "boot":
        return ["boot", *argv]
    return argv


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default or ""


def _yes_no(label: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{label} [{hint}]: ").strip().casefold()
    if not value:
        return default
    return value in {"y", "yes"}


def command_init(args: argparse.Namespace) -> int:
    name = validate_project_name(args.project)
    destination = project_config_path(name)
    if destination.exists() and not args.force:
        raise WorkError(f"project already exists: {destination}; use --force to replace it")

    interactive = not args.non_interactive and sys.stdin.isatty()
    root_text = args.root
    if interactive and not root_text:
        root_text = _prompt("Project root", str(Path.cwd()))
    root = Path(root_text or Path.cwd()).expanduser().resolve()
    if not root.is_dir():
        raise WorkError(f"project root does not exist or is not a directory: {root}")

    detected_workspaces = sorted(root.glob("*.code-workspace"))
    workspace_text = args.workspace
    if interactive and workspace_text is None:
        default_workspace = str(detected_workspaces[0]) if len(detected_workspaces) == 1 else str(root)
        workspace_text = _prompt("VS Code workspace or folder", default_workspace)
    workspace = Path(workspace_text).expanduser() if workspace_text else (
        detected_workspaces[0] if len(detected_workspaces) == 1 else root
    )
    if not workspace.is_absolute():
        workspace = root / workspace
    if not workspace.exists():
        raise WorkError(f"VS Code workspace or folder does not exist: {workspace}")

    vscode_profile = args.vscode_profile
    if interactive and vscode_profile is None:
        vscode_profile = _prompt("VS Code profile (blank for default)") or None

    chrome_profile = args.chrome_profile
    urls = list(args.url)
    if interactive and chrome_profile is None and _yes_no("Open Chrome for this project?", True):
        chrome_profile = _prompt("Chrome profile directory", "Default")
        if not urls:
            raw_urls = _prompt("URLs, comma-separated (blank for none)")
            urls = [url.strip() for url in raw_urls.split(",") if url.strip()]

    context_text = args.context
    detected_context = root / "PROJECT.md"
    if interactive and context_text is None:
        context_text = _prompt("Project context file", str(detected_context))
    context = Path(context_text).expanduser() if context_text else detected_context
    if not context.is_absolute():
        context = root / context
    if not context.exists():
        if interactive and _yes_no(f"Create {context}?", True):
            context.parent.mkdir(parents=True, exist_ok=True)
            context.write_text(
                f"# {name}\n\n## Context\n\nDescribe the project, current goals, and useful commands here.\n",
                encoding="utf-8",
            )
            print(f"created {context}")
        else:
            raise WorkError(f"project context file does not exist: {context}")

    startup = args.startup
    if interactive and startup is None:
        startup = _prompt("Development startup command (blank for none)") or None

    for url in urls:
        if not url.startswith(("http://", "https://")):
            raise WorkError(f"URL must start with http:// or https://: {url}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if not global_config_path().exists():
        global_config_path().write_text(render_global_config(default_commands()), encoding="utf-8")
        print(f"created {global_config_path()}")
    destination.write_text(
        render_project_config(
            name=name,
            root=root,
            workspace=workspace,
            vscode_profile=vscode_profile,
            chrome_profile=chrome_profile,
            urls=urls,
            context=context,
            startup=startup,
            desktop=args.desktop,
        ),
        encoding="utf-8",
    )
    # Parse what we wrote before reporting success.
    load_project(name)
    print(f"created {destination}")
    print(f"next: run 'focus {name}'" if args.desktop else
          f"next: select a macOS desktop and run 'focus {name}'")
    return 0


def command_edit(project: str) -> int:
    validate_project_name(project)
    path = project_config_path(project)
    if not path.is_file():
        raise WorkError(f"configuration not found: {path}; run 'focus init {project}'")
    editor = os.environ.get("EDITOR")
    if editor:
        command = [*shlex.split(editor), str(path)]
    else:
        code = load_commands().code
        command = [code, "--wait", str(path)] if executable_exists(code) else ["/usr/bin/open", "-t", str(path)]
    return subprocess.run(command, check=False).returncode


def _status_dict(status: WorkspaceStatus) -> dict[str, object]:
    return {
        "project": status.project,
        "booted": status.booted,
        "tmux": status.tmux,
        "startup": status.startup,
        "vscode": status.vscode,
        "chrome": status.chrome,
        "apps": status.apps,
    }


def _state(value: bool | None) -> str:
    if value is None:
        return "disabled"
    return "running" if value else "stopped"


def command_status(project_name: str | None, *, as_json: bool) -> int:
    names = [project_name] if project_name else list_project_names()
    if not names:
        raise WorkError("no projects configured; run 'focus init <project>'")
    manager = WorkspaceManager(load_commands(), CommandRunner())
    statuses = [manager.status(load_project(name)) for name in names]
    if as_json:
        payload: object = _status_dict(statuses[0]) if project_name else [_status_dict(item) for item in statuses]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("PROJECT\tTMUX\tSTARTUP\tVSCODE\tCHROME")
        for status in statuses:
            print(
                f"{status.project}\t{_state(status.tmux)}\t{_state(status.startup)}\t"
                f"{_state(status.vscode)}\t{_state(status.chrome)}"
            )
        if any(status.apps for status in statuses):
            print("\nPROJECT\tAPP\tSTATE")
            for status in statuses:
                for name, opened in status.apps.items():
                    state = "disabled" if opened is None else "running" if opened else "stopped"
                    print(f"{status.project}\t{name}\t{state}")
    return 0


def find_homebrew() -> str | None:
    found = shutil.which("brew")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if executable_exists(candidate):
            return candidate
    return None


def repair_dependency(*, command: str, package: str, assume_yes: bool) -> bool:
    if executable_exists(command):
        return True
    brew = find_homebrew()
    if brew is None:
        raise WorkError("Homebrew was not found; install the declared dependency yourself, "
                        "or install Homebrew and rerun 'focus doctor --fix'")
    operation = (brew, "install", package)
    if not assume_yes:
        if not sys.stdin.isatty():
            raise WorkError("dependency installation needs confirmation; rerun with --fix --yes")
        if not _yes_no(f"Run {shlex.join(operation)}?", True):
            print(f"info installation of {package!r} skipped")
            return False
    if CommandRunner().mutate(operation) != 0:
        raise WorkError(f"Homebrew failed to install {package!r}")
    print(f"ok  installed {package!r} with Homebrew")
    return True


def command_doctor(*, fix: bool = False, assume_yes: bool = False) -> int:
    commands = load_commands()
    names = list_project_names()
    projects = {}
    configuration_errors = {}
    for name in names:
        try:
            projects[name] = load_project(name)
        except WorkError as exc:
            configuration_errors[name] = str(exc)
    checks = [
        (sys.platform == "darwin", f"platform is macOS (found {sys.platform})"),
        (sys.version_info >= (3, 11), f"Python is 3.11+ (found {sys.version.split()[0]})"),
    ]
    failed = False
    for ok, message in checks:
        print(f"{'ok ' if ok else 'ERR'} {message}")
        failed = failed or not ok

    repaired_packages: set[str] = set()
    for name in names:
        if name in configuration_errors:
            print(f"ERR project configuration {name}: {configuration_errors[name]}")
            failed = True
            continue
        project = projects[name]
        try:
            apps = ConfiguredApps(commands.desktop_names, CommandRunner(), project, commands=commands)
            if fix:
                for resource in apps.resources:
                    extension = resource.extension
                    if not resource.app.enabled or extension is None:
                        continue
                    installer = extension.spec.data.get("install")
                    if not installer or executable_exists(extension.executable):
                        continue
                    package = installer["package"]
                    if package not in repaired_packages:
                        repair_dependency(command=extension.executable, package=package, assume_yes=assume_yes)
                        repaired_packages.add(package)
                commands = load_commands()
                apps = ConfiguredApps(commands.desktop_names, CommandRunner(), project, commands=commands)
            required = apps.required_commands()
        except WorkError as exc:
            print(f"ERR project configuration {name}: {exc}")
            failed = True
            continue
        print(f"ok  project configuration: {name}")
        for label, command in required:
            available = executable_exists(command)
            print(f"{'ok ' if available else 'ERR'} {label} executable for {name}: {command}")
            failed = failed or not available
    if not names:
        print("info no projects configured; run 'focus init <project>'")
    return 1 if failed else 0


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(list(argv if argv is not None else sys.argv[1:])))
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "init":
        return command_init(args)
    if args.command == "edit":
        return command_edit(args.project)
    if args.command == "list":
        names = list_project_names()
        print("\n".join(names) if names else "No projects configured.")
        return 0
    if args.command == "status":
        return command_status(args.project, as_json=args.json)
    if args.command == "doctor":
        if args.yes and not args.fix:
            raise WorkError("--yes requires --fix")
        return command_doctor(fix=args.fix, assume_yes=args.yes)

    project = load_project(args.project)
    runner = CommandRunner(dry_run=args.dry_run)
    manager = WorkspaceManager(load_commands(), runner, follow_apps=load_follow_apps())
    if args.command == "stop":
        manager.stop(project)
        return 0
    if args.command in {"boot", "restart"}:
        return manager.focus(project, no_attach=args.no_attach, force_gui=getattr(args, "force_gui", False),
                             restart=args.command == "restart")
    raise WorkError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except (WorkError, KeyboardInterrupt) as exc:
        print_error("interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc))
        return 2
