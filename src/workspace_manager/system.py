from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from workspace_manager.errors import WorkError


def format_command(command: Sequence[str]) -> str:
    return shlex.join(str(part) for part in command)


class CommandRunner:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def capture(self, command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise WorkError(f"command not found: {command[0]}") from exc

    def mutate(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        detached: bool = False,
    ) -> int:
        print(f"$ {format_command(command)}")
        if self.dry_run:
            return 0
        try:
            if detached:
                with open(os.devnull, "rb") as stdin, open(os.devnull, "ab") as output:
                    subprocess.Popen(
                        list(command),
                        cwd=cwd,
                        stdin=stdin,
                        stdout=output,
                        stderr=output,
                        start_new_session=True,
                    )
                return 0
            return subprocess.run(list(command), cwd=cwd, check=False).returncode
        except FileNotFoundError as exc:
            raise WorkError(f"command not found: {command[0]}") from exc

    def shell_capture(self, command: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
        shell = os.environ.get("SHELL", "/bin/zsh")
        return self.capture((shell, "-lc", command), cwd=cwd)


def executable_exists(command: str) -> bool:
    path = Path(command).expanduser()
    if path.is_absolute() or "/" in command:
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def attach_tmux(command: str, session: str) -> int:
    args = (
        [command, "switch-client", "-t", session]
        if os.environ.get("TMUX")
        else [command, "attach-session", "-t", session]
    )
    try:
        return subprocess.run(args, check=False).returncode
    except FileNotFoundError as exc:
        raise WorkError(f"command not found: {command}") from exc


def print_error(message: str) -> None:
    print(f"work: error: {message}", file=sys.stderr)
