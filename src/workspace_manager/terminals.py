"""macOS Terminal presentation for declarative managed-session extensions."""
from __future__ import annotations

import hashlib
import shlex

from workspace_manager.errors import WorkError
from workspace_manager.macos import terminal_automation_permission
from workspace_manager.paths import state_home
from workspace_manager.system import CommandRunner
from workspace_manager.windows import ProjectWindows


OPEN_WINDOW_SCRIPT = '''on run arguments
    tell application "Terminal"
        set projectTab to do script (item 1 of arguments)
        set custom title of projectTab to (item 2 of arguments)
    end tell
    return "opened"
end run'''


class TerminalWindows:
    """Present independently selected session views without provider commands."""

    def __init__(self, session, runner: CommandRunner) -> None:
        self.session = session
        self.runner = runner

    def _resolved(self, project=None):
        # Compatibility for callers of the earlier Python helper. Runtime
        # resources pass a resolved session capability directly.
        if not isinstance(self.session, str):
            return self.session
        from workspace_manager.app_compat import configured_apps
        from workspace_manager.extensions import load_extension, resolve_extension
        from workspace_manager.models import CommandConfig
        if project is None:
            raise WorkError("A project is required by the compatibility Terminal helper")
        app = next(app for app in configured_apps(project) if app.extension and load_extension(app.extension).kind == "session")
        return resolve_extension(app, project, CommandConfig(self.session, "code", "chrome"))

    @staticmethod
    def matches_title(actual: str, expected: str) -> bool:
        # Terminal combines its custom title with optional profile/user/process
        # fields. Match a complete title field, never a partial service name.
        return f" — {expected} — " in f" — {actual} — "

    def open(self, project=None, *, windows: ProjectWindows | None = None) -> None:
        session = self._resolved(project)
        if not self.runner.dry_run and session.app.launch_mode == 'window':
            self.check_access()
        for name in session.view_units():
            if self.runner.dry_run:
                reuse = 'reuse an attached view, otherwise ' if session.app.reuse_existing else ''
                print(f'plan {reuse}new Terminal {session.app.launch_mode}: {session.project.name} / {name}')
                continue
            view, exists, reusable = session.view_plan(self.runner, name)
            if reusable:
                if windows:
                    self._place(windows, session, name)
                print(f'ok  Terminal client for {name!r} is attached')
                continue

            def launch() -> None:
                session.prepare_view(self.runner, name, view, exists)
                self._open_client(session, name, view)

            if windows:
                title = session.view_title(name)
                windows.ensure(session.view_slot(name), session.bundle_id, launch,
                               lambda row: TerminalWindows.matches_title(row['title'], title),
                               reuse_existing=False, require_match=True, binding_context=session.binding_context,
                               allow_unversioned_binding=bool(session.app.legacy_slot))
            else:
                launch()
            print(f'ok  Terminal view for {name!r} is ready')

    def _open_client(self, session, name: str, view: str) -> None:
        cleared = session.settings.get('attach', {}).get('clear_env', [])
        command = ('unset ' + shlex.join(cleared) + '; ' if cleared else '')
        command += 'exec ' + shlex.join(session.operation('view_attach', view=view))
        title = session.view_title(name)
        if session.app.launch_mode == 'instance':
            folder = state_home() / 'terminal-launchers' / session.project.name
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (hashlib.sha256(view.encode()).hexdigest()[:16] + '.command')
            path.write_text('#!/bin/sh\n' + "printf '\\033]0;%s\\007' " + shlex.quote(title) + '\n' + command + '\n')
            path.chmod(0o700)
            session.mutate(self.runner, 'view_enable_title', view=view)
            session.mutate(self.runner, 'view_set_title', view=view, title=title)
            if self.runner.mutate(('/usr/bin/open', '-n', '-b', session.bundle_id, str(path))):
                raise WorkError(f'Terminal could not launch an instance for {name!r}')
        else:
            opened = self.runner.capture(('/usr/bin/osascript', '-e', OPEN_WINDOW_SCRIPT, '--', command, title))
            if opened.returncode or opened.stdout.strip() != 'opened':
                raise WorkError(f'Terminal could not open the {name!r} window: {opened.stderr.strip()}')
        session.confirm_view(self.runner, view, name)

    @staticmethod
    def _place(windows: ProjectWindows, session, name: str) -> None:
        def missing():
            raise WorkError(f'The {name!r} session client is attached but its Terminal window could not be identified')
        title = session.view_title(name)
        windows.ensure(session.view_slot(name), session.bundle_id, missing,
                       lambda row: TerminalWindows.matches_title(row['title'], title),
                       binding_context=session.binding_context,
                       allow_unversioned_binding=bool(session.app.legacy_slot))

    def check_access(self) -> None:
        if self.runner.dry_run:
            return
        status = terminal_automation_permission(ask_user=True)
        if status == -600:
            raise WorkError('Open Terminal before using separate Terminal windows, then run focus again')
        if status:
            raise WorkError(f'Terminal Automation access is unavailable ({status}). In System Settings > '
                            'Privacy & Security > Automation, allow the app running Focus to control '
                            'Terminal, then retry. No project windows were opened.')

    def stop(self, project=None) -> None:
        self._resolved(project).stop_views(self.runner)
