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
from workspace_manager.config import (
    default_commands,
    list_project_names,
    load_commands,
    load_project,
    render_global_config,
    render_project_config,
    validate_project_name,
)
from workspace_manager.errors import WorkError
from workspace_manager.manager import WorkspaceManager
from workspace_manager.models import CommandConfig, WorkspaceStatus
from workspace_manager.paths import global_config_path, project_config_path
from workspace_manager.system import CommandRunner, attach_tmux, executable_exists, print_error


COMMANDS = {"init", "edit", "status", "stop", "restart", "doctor", "list"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="work",
        description="Restore a local project workspace on the current macOS desktop.",
    )
    parser.add_argument("--version", action="version", version=f"work {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    boot = subparsers.add_parser(
        "boot",
        help="restore a project workspace (normally invoked as: work <project>)",
    )
    boot.add_argument("project")
    boot.add_argument("--dry-run", action="store_true")
    boot.add_argument("--no-attach", action="store_true")
    boot.add_argument("--force-gui", action="store_true")

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

    edit = subparsers.add_parser("edit", help="edit a project configuration")
    edit.add_argument("project")

    status = subparsers.add_parser("status", help="show workspace status")
    status.add_argument("project", nargs="?")
    status.add_argument("--json", action="store_true")

    stop = subparsers.add_parser("stop", help="stop a project's tmux session")
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
        help="offer to install a missing tmux dependency with Homebrew",
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
        ),
        encoding="utf-8",
    )
    # Parse what we wrote before reporting success.
    load_project(name)
    print(f"created {destination}")
    print(f"next: select a macOS desktop and run 'work {name}'")
    return 0


def command_edit(project: str) -> int:
    validate_project_name(project)
    path = project_config_path(project)
    if not path.is_file():
        raise WorkError(f"configuration not found: {path}; run 'work init {project}'")
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
    }


def _state(value: bool | None) -> str:
    if value is None:
        return "disabled"
    return "running" if value else "stopped"


def command_status(project_name: str | None, *, as_json: bool) -> int:
    names = [project_name] if project_name else list_project_names()
    if not names:
        raise WorkError("no projects configured; run 'work init <project>'")
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
    return 0


def find_homebrew() -> str | None:
    found = shutil.which("brew")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if executable_exists(candidate):
            return candidate
    return None


def repair_tmux(*, tmux_command: str, assume_yes: bool) -> str | None:
    if executable_exists(tmux_command):
        return tmux_command
    brew = find_homebrew()
    if brew is None:
        raise WorkError(
            "tmux is missing and Homebrew was not found; install Homebrew from "
            "https://brew.sh, then rerun 'work doctor --fix'"
        )
    if not assume_yes:
        if not sys.stdin.isatty():
            raise WorkError("dependency installation needs confirmation; rerun with --fix --yes")
        if not _yes_no("Install tmux with Homebrew now?", True):
            print("info tmux installation skipped")
            return None
    if CommandRunner().mutate((brew, "install", "tmux")) != 0:
        raise WorkError("Homebrew failed to install tmux")
    resolved_tmux = tmux_command
    tmux_available = executable_exists(resolved_tmux)
    if not tmux_available:
        resolved_tmux = default_commands().tmux
        tmux_available = executable_exists(resolved_tmux)
    if not tmux_available:
        raise WorkError(
            "Homebrew installed tmux, but the configured tmux command is still unavailable; "
            "update the tmux path in ~/.config/work/config.toml"
        )
    print("ok  installed tmux with Homebrew")
    return resolved_tmux


def command_doctor(*, fix: bool = False, assume_yes: bool = False) -> int:
    commands = load_commands()
    if fix and not executable_exists(commands.tmux):
        repaired_tmux = repair_tmux(tmux_command=commands.tmux, assume_yes=assume_yes)
        if repaired_tmux:
            commands = CommandConfig(
                tmux=repaired_tmux,
                code=commands.code,
                chrome=commands.chrome,
            )
    checks = [
        (sys.platform == "darwin", f"platform is macOS (found {sys.platform})"),
        (sys.version_info >= (3, 11), f"Python is 3.11+ (found {sys.version.split()[0]})"),
        (executable_exists(commands.tmux), f"tmux executable: {commands.tmux}"),
        (executable_exists(commands.code), f"VS Code executable: {commands.code}"),
        (executable_exists(commands.chrome), f"Chrome executable: {commands.chrome}"),
    ]
    failed = False
    for ok, message in checks:
        print(f"{'ok ' if ok else 'ERR'} {message}")
        failed = failed or not ok

    for name in list_project_names():
        try:
            load_project(name)
            print(f"ok  project configuration: {name}")
        except WorkError as exc:
            print(f"ERR project configuration {name}: {exc}")
            failed = True
    if not list_project_names():
        print("info no projects configured; run 'work init <project>'")
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
    manager = WorkspaceManager(load_commands(), runner)
    if args.command == "stop":
        manager.stop(project)
        return 0
    if args.command == "restart":
        manager.stop(project)
        manager.reconcile(project, force_gui=False, assume_stopped=args.dry_run)
        if not args.no_attach and not args.dry_run:
            return attach_tmux(manager.commands.tmux, project.tmux.session)
        return 0
    if args.command == "boot":
        manager.reconcile(project, force_gui=args.force_gui)
        if not args.no_attach and not args.dry_run:
            return attach_tmux(manager.commands.tmux, project.tmux.session)
        return 0
    raise WorkError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except (WorkError, KeyboardInterrupt) as exc:
        print_error("interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc))
        return 2
