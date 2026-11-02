from __future__ import annotations

from fnmatch import fnmatchcase

from workspace_manager.desktop import DesktopNamesAdapter
from workspace_manager.models import FollowAppConfig
from workspace_manager.windows import ProjectWindows


class FollowApps:
    """Move shared windows without launching apps or claiming project ownership."""

    def __init__(self, apps: tuple[FollowAppConfig, ...]):
        self.apps = tuple(app for app in apps if app.enabled)

    @property
    def enabled(self) -> bool:
        return bool(self.apps)

    def matching_windows(self, windows: ProjectWindows) -> list[dict]:
        result = []
        for bundle in dict.fromkeys(app.bundle_id for app in self.apps):
            for row in windows.inventory(bundle):
                # Leave windows assigned to multiple desktops or no desktop untouched.
                if len(row["spaces"]) != 1:
                    continue
                if any(app.bundle_id == row["bundle"] and
                       (app.match_title is None or fnmatchcase(row["title"], app.match_title))
                       for app in self.apps):
                    result.append(row)
        return result

    def move(self, windows: ProjectWindows) -> None:
        if windows.runner.dry_run:
            for app in self.apps:
                print(f"plan move open follow app {app.name!r} windows to the project desktop")
            return
        for window in self.matching_windows(windows):
            windows.move(window)
            print(f"ok  follow app {window['bundle']} window is on the project desktop")

    def preserve_on_stop(self, windows: ProjectWindows, desktop: DesktopNamesAdapter) -> None:
        if not self.enabled:
            return
        if windows.runner.dry_run:
            print("plan move follow app windows to another regular desktop before cleanup")
            return
        shared = [row for row in self.matching_windows(windows) if row["spaces"] == [windows.desktop]]
        if not shared:
            return
        target = desktop.other_desktop(windows.project, windows.desktop)
        destination = ProjectWindows(windows.command, windows.runner, windows.project, target)
        for window in shared:
            destination.move(window)
        print("ok  follow app windows preserved on another desktop")
