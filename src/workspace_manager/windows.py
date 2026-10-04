from __future__ import annotations

import json
import re
import time
from typing import Callable

from workspace_manager.errors import WorkError
from workspace_manager.models import ProjectConfig
from workspace_manager.paths import state_home
from workspace_manager.system import CommandRunner


TERMINAL = "com.apple.Terminal"


class ProjectWindows:
    def __init__(self, command: str, runner: CommandRunner, project: ProjectConfig, desktop: str | None):
        self.command, self.runner, self.project, self.desktop = command, runner, project, desktop
        self.path = state_home() / "windows" / f"{project.name}.json"
        self._boot_id: str | None = None

    def _boot(self) -> str:
        if self._boot_id is None:
            result = self.runner.capture(("/usr/sbin/sysctl", "-n", "kern.boottime"))
            match = re.search(r"\bsec\s*=\s*(\d+)", result.stdout)
            if result.returncode or match is None:
                raise WorkError("Cannot identify this macOS boot; saved window IDs cannot be safely reused")
            self._boot_id = match[1]
        return self._boot_id

    def inventory(self, bundle: str | None = None) -> list[dict]:
        result = self.runner.capture((self.command, "windows", *((bundle,) if bundle else ())))
        if result.returncode:
            raise WorkError(f"Cannot inspect desktop windows: {result.stderr.strip()}")
        try:
            rows = json.loads(result.stdout)
            if not isinstance(rows, list) or any(
                not isinstance(row, dict)
                or any(type(row.get(field)) is not int or row[field] <= 0 for field in ("id", "pid"))
                or any(not isinstance(row.get(field), str) for field in ("bundle", "title", "document"))
                or not isinstance(row.get("spaces"), list)
                or any(not isinstance(space, str) or not space for space in row["spaces"])
                for row in rows
            ):
                raise ValueError("invalid window inventory")
            return rows
        except ValueError as exc:
            raise WorkError(f"Cannot read desktop windows: {exc}") from exc

    @staticmethod
    def identity(window: dict) -> tuple:
        return window["id"], window["pid"], window["bundle"]

    def _saved(self) -> dict:
        try:
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not isinstance(data, dict) or any(
                not isinstance(window, dict) or any(field not in window for field in ("id", "pid", "bundle"))
                for window in data.values()
            ):
                raise ValueError("invalid window bindings")
            return data
        except (ValueError, OSError) as exc:
            raise WorkError(f"Cannot read project window bindings: {exc}") from exc

    def remember(self, slot: str, window: dict, *, pending_move: bool = False,
                 binding_context: str | None = None) -> None:
        data = self._saved()
        data[slot] = {field: window[field] for field in ("id", "pid", "bundle")}
        data[slot]["boot"] = self._boot()
        data[slot]["pending_move"] = pending_move
        if binding_context is not None:
            data[slot]["context"] = binding_context
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data) + "\n")
        temporary.replace(self.path)

    def move(self, window: dict) -> None:
        if self.desktop is None or window["spaces"] == [self.desktop]:
            return
        result = self.runner.capture((self.command, "move-window", str(window["id"]),
                                      str(window["pid"]), window["bundle"], self.desktop))
        if result.returncode:
            raise WorkError(f"Could not move {window['title']!r}: {result.stderr.strip()}")
        current = next((row for row in self.inventory(window["bundle"])
                        if self.identity(row) == self.identity(window)), None)
        if current is None or current["spaces"] != [self.desktop]:
            raise WorkError("Window move was not confirmed; no replacement window was opened")

    def _existing(self, slot: str, rows: list[dict], matches: Callable[[dict], bool], *,
                  reuse_existing: bool = True, binding_context: str | None = None,
                  adopt_single_on_desktop: bool = False,
                  allow_unversioned_binding: bool = False,
                  peer_slots: tuple[str, ...] = ()) -> tuple[dict | None, bool]:
        bindings = self._saved()
        saved = bindings.get(slot)
        claimed = {self.identity(binding) for name, binding in bindings.items()
                   if name != slot and name in peer_slots and binding.get("boot") == self._boot()}
        rows = [row for row in rows if self.identity(row) not in claimed]
        if saved and ((saved.get("context") != binding_context and not
                       (allow_unversioned_binding and "context" not in saved)) or saved.get("boot") != self._boot()):
            saved = None
        # Retry a previously requested window's failed move even when discovery
        # is disabled; a retry must not create another replacement window.
        candidates = [row for row in rows if saved and (reuse_existing or saved.get("pending_move"))
                      and self.identity(row) == self.identity(saved)]
        if not candidates and reuse_existing:
            candidates = [row for row in rows if matches(row)]
        if not candidates and reuse_existing and adopt_single_on_desktop and self.desktop:
            # An extension may explicitly allow adoption on the project desktop.
            candidates = [row for row in rows if row["spaces"] == [self.desktop]]
        if len(candidates) > 1:
            raise WorkError(f"Several {slot} windows match this project; refusing to move an arbitrary window")
        requested_new = bool(candidates and saved and saved.get("pending_move")
                             and self.identity(candidates[0]) == self.identity(saved))
        return (candidates[0] if candidates else None), requested_new

    def find_existing(self, slot: str, bundle: str, matches: Callable[[dict], bool], *,
                      binding_context: str | None = None, allow_unversioned_binding: bool = False,
                      peer_slots: tuple[str, ...] = ()) -> dict | None:
        window, _ = self._existing(slot, self.inventory(bundle), matches, binding_context=binding_context,
                                   allow_unversioned_binding=allow_unversioned_binding, peer_slots=peer_slots)
        return window

    def ensure(self, slot: str, bundle: str, launch: Callable[[], None],
               matches: Callable[[dict], bool] = lambda _: False, *,
               reuse_existing: bool = True, require_match: bool = False,
               binding_context: str | None = None, adopt_single_on_desktop: bool = False,
               allow_unversioned_binding: bool = False, peer_slots: tuple[str, ...] = ()) -> None:
        if self.runner.dry_run:
            print(f"plan reuse/move {slot} window; open one only if missing" if reuse_existing else
                  f"plan request a new {slot} window; leave existing windows in place")
            launch()
            return
        rows = self.inventory(bundle)
        existing, requested_new = self._existing(slot, rows, matches, reuse_existing=reuse_existing,
                                                binding_context=binding_context,
                                                adopt_single_on_desktop=adopt_single_on_desktop,
                                                allow_unversioned_binding=allow_unversioned_binding,
                                                peer_slots=peer_slots)
        candidates = [existing] if existing else []
        if not candidates:
            before = {self.identity(row) for row in rows}
            launch()
            deadline = time.monotonic() + 12
            while True:
                rows = self.inventory(bundle)
                appeared = [row for row in rows if self.identity(row) not in before]
                matching = [row for row in appeared if matches(row)]
                candidates = matching if require_match else (matching or appeared)
                if candidates:
                    break
                if time.monotonic() >= deadline:
                    kept = "desktop kept for inspection" if self.desktop else "windows kept for inspection"
                    raise WorkError(f"No new {slot} window appeared; existing windows were not moved. "
                                    f"The application may have reused an existing window; {kept}")
                time.sleep(0.2)
            if len(candidates) != 1:
                raise WorkError(f"Several {slot} windows appeared; refusing an ambiguous assignment")
            requested_new = True
        window = candidates[0]
        # Record before moving so a failed move can be retried without duplicates.
        self.remember(slot, window, pending_move=requested_new, binding_context=binding_context)
        self.move(window)
        self.remember(slot, window, binding_context=binding_context)
        print(f"ok  {slot} window is on the project desktop" if self.desktop else f"ok  {slot} window is open")

    def close_on_desktop(self, *, terminals: bool) -> None:
        if self.runner.dry_run:
            print("plan close desktop Terminal windows" if terminals else "plan close desktop application windows")
            return
        for window in self.inventory():
            if window["spaces"] != [self.desktop] or (window["bundle"] == TERMINAL) != terminals:
                continue
            result = self.runner.capture((self.command, "close-window", str(window["id"]),
                                          str(window["pid"]), window["bundle"], self.desktop))
            if result.returncode:
                raise WorkError(f"Could not close {window['title']!r}; desktop kept: {result.stderr.strip()}")

    def require_empty(self) -> None:
        if not self.runner.dry_run and any(row["spaces"] == [self.desktop] for row in self.inventory()):
            raise WorkError("Desktop still contains windows; resolve save/close prompts and retry focus stop")
