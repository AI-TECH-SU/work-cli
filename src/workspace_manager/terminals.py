from __future__ import annotations

import hashlib
import shlex
import time
import uuid

from workspace_manager.errors import WorkError
from workspace_manager.macos import terminal_automation_permission
from workspace_manager.models import ProjectConfig
from workspace_manager.paths import state_home
from workspace_manager.system import CommandRunner
from workspace_manager.windows import ProjectWindows, TERMINAL


OPEN_WINDOW_SCRIPT = '''on run arguments
    tell application "Terminal"
        set projectTab to do script (item 1 of arguments)
        set custom title of projectTab to (item 2 of arguments)
    end tell
    return "opened"
end run'''


class TerminalWindows:
    """Separate Terminal windows viewing shared tmux windows independently."""

    def __init__(self, tmux: str, runner: CommandRunner) -> None:
        self.tmux = tmux
        self.runner = runner

    def _capture(self, *args: str):
        return self.runner.capture((self.tmux, *args))

    def _mutate(self, *args: str) -> None:
        if self.runner.mutate((self.tmux, *args)):
            raise WorkError('tmux could not prepare a project Terminal window')

    def open(self, project: ProjectConfig, *, windows: ProjectWindows | None = None) -> None:
        if not self.runner.dry_run and project.tmux.launch_mode == 'window':
            self.check_access()
        names = [window.name for window in project.tmux.windows] or ['shell']
        if project.startup.command and project.startup.window not in names:
            names.append(project.startup.window)
        for name in names:
            suffix = hashlib.sha256(name.encode()).hexdigest()[:12]
            session = f'{project.tmux.session}-view-{suffix}'
            if self.runner.dry_run:
                reuse = 'reuse an attached view, otherwise ' if project.tmux.reuse_existing else ''
                print(f'plan {reuse}new Terminal {project.tmux.launch_mode}: {project.name} / {name}')
                continue
            exists = self._capture('has-session', '-t', '=' + session).returncode == 0
            if exists:
                owner = self._capture('show-options', '-v', '-t', session, '@focus-parent')
                if owner.returncode or owner.stdout.strip() != project.tmux.session:
                    raise WorkError(f'Refusing to reuse unrelated tmux session {session!r}')
            attached = self._capture('display-message', '-p', '-t', session, '#{session_attached}') if exists else None
            if attached and attached.returncode == 0 and attached.stdout.strip() not in {'', '0'}:
                mode = self._capture('show-options', '-v', '-t', session, '@focus-terminal-mode')
                if project.tmux.reuse_existing and mode.returncode == 0 and mode.stdout.strip() in {'window', 'instance'}:
                    if windows:
                        self._place(windows, project, name)
                    print(f'ok  Terminal client for {name!r} is attached')
                    continue
                if project.tmux.reuse_existing:
                    # Reconnect clients from legacy background launchers only.
                    self._mutate('detach-client', '-s', session)
                else:
                    # New clients need independent selections without detaching
                    # or retargeting a window the user asked us to leave alone.
                    session += '-' + uuid.uuid4().hex[:12]
                    exists = False

            def launch() -> None:
                if not exists:
                    self._mutate('new-session', '-d', '-s', session, '-t', project.tmux.session)
                    self._mutate('set-option', '-t', session, '@focus-parent', project.tmux.session)
                self._mutate('select-window', '-t', f'{session}:{name}')
                self._open_client(project, name, session)

            if windows:
                title = f'{project.name} / {name}'
                windows.ensure(f'terminal:{name}', TERMINAL, launch,
                               lambda row: row['title'] == title or row['title'].startswith(title + ' — '),
                               reuse_existing=False, require_match=True)
            else:
                launch()
            print(f'ok  Terminal view for {name!r} is ready')

    def _open_client(self, project: ProjectConfig, name: str, session: str) -> None:
        command = 'unset TMUX; exec ' + shlex.join([self.tmux, 'attach-session', '-t', session])
        title = f'{project.name} / {name}'
        if project.tmux.launch_mode == 'instance':
            # Launch Services opens this command document in the new process.
            # Do not use -F: it discards the user's saved application state.
            folder = state_home() / 'terminal-launchers' / project.name
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (hashlib.sha256(session.encode()).hexdigest()[:16] + '.command')
            path.write_text('#!/bin/sh\n' + "printf '\\033]0;%s\\007' " + shlex.quote(title) + '\n' + command + '\n')
            path.chmod(0o700)
            self._mutate('set-option', '-t', session, 'set-titles', 'on')
            self._mutate('set-option', '-t', session, 'set-titles-string', title)
            if self.runner.mutate(('/usr/bin/open', '-n', '-b', TERMINAL, str(path))):
                raise WorkError(f'Terminal could not launch an instance for {name!r}')
        else:
            # Omitting "in <tab>" creates one new window in the existing app.
            opened = self.runner.capture(('/usr/bin/osascript', '-e', OPEN_WINDOW_SCRIPT,
                                          '--', command, title))
            if opened.returncode or opened.stdout.strip() != 'opened':
                raise WorkError(f'Terminal could not open the {name!r} window: {opened.stderr.strip()}')
        deadline = time.monotonic() + 10
        while True:
            attached = self._capture('display-message', '-p', '-t', session, '#{session_attached}')
            if attached.returncode == 0 and attached.stdout.strip() not in {'', '0'}:
                break
            if time.monotonic() >= deadline:
                raise WorkError(f'Terminal did not attach to {name!r}; inspect its window before retrying')
            time.sleep(0.1)
        self._mutate('set-option', '-t', session, '@focus-terminal-mode', project.tmux.launch_mode)

    @staticmethod
    def _place(windows: ProjectWindows, project: ProjectConfig, name: str) -> None:
        def missing():
            raise WorkError(f'The {name!r} tmux client is attached but its Terminal window could not be identified')
        title = f'{project.name} / {name}'
        windows.ensure(f'terminal:{name}', TERMINAL, missing,
                       lambda row: row['title'] == title or row['title'].startswith(title + ' — '))

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

    def stop(self, project: ProjectConfig) -> None:
        if self.runner.dry_run:
            print('plan stop project Terminal tmux views')
            return
        result = self._capture('list-sessions', '-F', '#{session_name}\t#{@focus-parent}')
        if result.returncode:
            return
        for row in result.stdout.splitlines():
            name, _, owner = row.partition('\t')
            if owner == project.tmux.session:
                self._mutate('kill-session', '-t', '=' + name)
