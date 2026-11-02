from __future__ import annotations

import fcntl
import json
from pathlib import Path

from workspace_manager.errors import WorkError
from workspace_manager.models import ProjectConfig
from workspace_manager.paths import state_home
from workspace_manager.system import CommandRunner


class DesktopNamesAdapter:
    """Desktop Names owns names/navigation; Focus retains only project UUID bindings."""

    def __init__(self, command: str, runner: CommandRunner) -> None:
        self.command = command
        self.runner = runner

    @staticmethod
    def binding_path(project: ProjectConfig) -> Path:
        return state_home() / "desktops" / f"{project.name}.json"

    def find(self, project: ProjectConfig) -> str | None:
        """Resolve an existing assignment without creating, switching, or renaming."""
        if self.runner.dry_run:
            return None
        rows, saved = self._read(project, self.binding_path(project))
        target = self._existing(project, rows, saved)
        return target["uuid"] if target else None

    def other_desktop(self, project: ProjectConfig, excluding: str) -> str:
        rows, _ = self._read(project, self.binding_path(project))
        candidates = [row for row in rows if row["regular"] and row["uuid"] != excluding]
        if not candidates:
            raise WorkError("No other regular desktop is available to preserve follow_apps; desktop kept")
        return next((row["uuid"] for row in candidates if row["active"]), candidates[0]["uuid"])

    def switch_existing(self, project: ProjectConfig) -> bool:
        if self.runner.dry_run or project.space.target != "project":
            return False
        key = self.find(project)
        if key is None:
            return False
        if self.runner.mutate((self.command, "switch", key)):
            raise WorkError("Desktop Names switch failed")
        binding = self.binding_path(project)
        binding.parent.mkdir(parents=True, exist_ok=True)
        self._save_binding(binding, key)
        print(f"ok  switched to {project.space.name or project.name!r}")
        return True

    def remove(self, project: ProjectConfig, key: str) -> None:
        if self.runner.dry_run:
            print(f"plan remove empty desktop for {project.name!r}")
            return
        result = self.runner.capture((self.command, "remove", key))
        if result.returncode:
            raise WorkError(f"Desktop removal failed; assignment retained: {result.stderr.strip()}")
        rows, _ = self._read(project, self.binding_path(project))
        if any(row["uuid"] == key for row in rows):
            raise WorkError("Desktop removal was not confirmed; assignment retained")
        self.binding_path(project).unlink(missing_ok=True)

    def prepare(self, project: ProjectConfig) -> str | None:
        name = project.space.name or project.name
        if self.runner.dry_run:
            print(f"plan Desktop Names: {project.space.target} desktop, name {name!r}")
            print("plan resolve desktop UUID (create a project desktop if missing), switch, rename")
            return
        folder = state_home() / "desktops"
        folder.mkdir(parents=True, exist_ok=True)
        # Serialize bindings and desktop selection across project invocations.
        with (folder / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._prepare(project, name, folder / f"{project.name}.json")

    def _read(self, project: ProjectConfig, binding: Path) -> tuple[list[dict], dict | None]:
        result = self.runner.capture((self.command, "list"))
        if result.returncode:
            raise WorkError(f"Desktop Names list failed: {result.stderr.strip()}")
        try:
            rows = json.loads(result.stdout)
            if not isinstance(rows, list) or not rows or any(
                not isinstance(row, dict)
                or not isinstance(row.get("uuid"), str) or not row["uuid"]
                or type(row.get("active")) is not bool
                or type(row.get("regular")) is not bool
                for row in rows
            ) or len({row["uuid"] for row in rows}) != len(rows):
                raise ValueError("invalid desktop list")
            saved = json.loads(binding.read_text()) if project.space.target == "project" and binding.exists() else None
            if saved is not None and (
                not isinstance(saved, dict) or not isinstance(saved.get("uuid"), str) or not saved["uuid"]
            ):
                raise ValueError("invalid desktop binding")
        except (ValueError, OSError) as exc:
            raise WorkError(f"Cannot read Desktop Names state: {exc}") from exc
        active = [row for row in rows if row["active"]]
        if len(active) != 1:
            raise WorkError("Desktop Names currently supports one active display")
        return rows, saved

    @staticmethod
    def _existing(project: ProjectConfig, rows: list[dict], saved: dict | None) -> dict | None:
        for key in (project.space.uuid, saved["uuid"] if saved else None):
            match = next((row for row in rows if row["uuid"] == key), None)
            if match is not None:
                if not match["regular"]:
                    raise WorkError("The assigned desktop is not a regular desktop")
                return match
        name = project.space.name or project.name
        matches = [row for row in rows if row["regular"] and row.get("name") == name]
        if len(matches) > 1:
            raise WorkError(f"Several desktops are named {name!r}; set space.uuid to choose one")
        return matches[0] if matches else None

    def _prepare(self, project: ProjectConfig, name: str, binding: Path) -> str:
        rows, saved = self._read(project, binding)
        if project.space.target == "project":
            target = self._existing(project, rows, saved)
            if target is None:
                created = self.runner.capture((self.command, "create"))
                if created.returncode or not created.stdout.strip():
                    raise WorkError(f"Desktop Names create failed: {created.stderr.strip()}")
                target = {"uuid": created.stdout.strip()}
                # Save before switching/renaming so retrying a partial launch
                # reuses this desktop even if an explicitly configured UUID vanished.
                self._save_binding(binding, target["uuid"])
        else:
            target = next(row for row in rows if row["active"])
            if not target["regular"]:
                raise WorkError("Select a regular desktop before naming it")
        for args in (("switch", target["uuid"]), ("rename", target["uuid"], name)):
            if self.runner.mutate((self.command, *args)):
                raise WorkError(f"Desktop Names {args[0]} failed; project launch stopped")
        if project.space.target == "project" and (saved is None or saved["uuid"] != target["uuid"]):
            self._save_binding(binding, target["uuid"])
        print(f"ok  desktop {name!r}")
        return target["uuid"]

    @staticmethod
    def _save_binding(binding: Path, key: str) -> None:
        temporary = binding.with_suffix(".tmp")
        temporary.write_text(json.dumps({"uuid": key}) + "\n")
        temporary.replace(binding)
