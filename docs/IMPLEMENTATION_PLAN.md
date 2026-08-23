# Implementation plan

## Accepted v1 workflow

The user selects or creates a macOS Mission Control desktop, opens Terminal,
and runs:

```console
work projectA
```

The CLI loads the named TOML file, serializes concurrent launches with a local
file lock, checks the current state, creates or repairs resources, and attaches
the terminal to the project tmux session. Repeated invocations are idempotent
within the limits of application state that macOS exposes without Automation or
Accessibility permissions.

## Delivery phases

1. Define strict dataclasses and a versioned TOML contract.
2. Implement command discovery, subprocess execution, dry-run output, and a
   per-project local lock.
3. Implement tmux, startup-command, VS Code, Chrome, and context reconciliation.
4. Add guided initialization, configuration editing, list/status/stop/restart,
   and installation diagnostics.
5. Verify parsing, validation, command construction, idempotency, and CLI error
   handling with standard-library `unittest` tests.

## File structure

```text
.
├── PROJECT.md
├── README.md
├── docs/
│   ├── DISTRIBUTION.md
│   └── IMPLEMENTATION_PLAN.md
├── packaging/
│   └── homebrew/
│       └── work.rb.in
├── pyproject.toml
├── scripts/
│   └── render_homebrew_formula.py
├── src/
│   └── workspace_manager/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── errors.py
│       ├── manager.py
│       ├── models.py
│       ├── paths.py
│       └── system.py
└── tests/
    ├── test_cli.py
    ├── test_config.py
    └── test_manager.py
```

## Reconciliation rules

- **tmux:** `tmux has-session` is the authoritative boot check. A missing
  session is created with a shell window rooted at the project directory.
- **startup command:** an optional health check is authoritative when supplied;
  otherwise a live tmux window with the configured name is the marker. A
  missing command is started in a new tmux window.
- **VS Code:** `code --status` is searched for the configured workspace/folder.
  A missing workspace is opened in a new window using the configured profile.
  `PROJECT.md` is included in that launch when configured.
- **Chrome:** the current user's process list is checked for the configured
  Chrome profile. URLs open in a new Chrome window on a fresh boot or when that
  profile is not running. Individual tab inspection is deliberately excluded
  because it would require browser automation.
- **stop:** only the project-owned tmux session is terminated. Shared GUI
  applications are left intact.

## Safety and auditability

- Subprocesses use argument arrays; no project value is interpolated into an
  implicit shell command.
- Only the explicitly configured startup and health-check strings run through
  the user's shell.
- Configuration validation rejects unknown versions, invalid project names,
  invalid URLs, missing roots, and malformed value types before launching.
- `--dry-run` shows mutating external commands.
- Runtime coordination files stay under the user's XDG state directory.
- No network request is made by `work` itself.

## Known v1 boundary

macOS has no stable public command-line API for creating, selecting, or moving
windows between Mission Control Spaces. Adding UI scripting or a window manager
would require permissions or dependencies that contradict the v1 constraints.
Space automation remains a separate, explicit future adapter.

## Distribution boundary

The Homebrew formula declares tmux as a package dependency, so Homebrew users
receive it automatically. tmux is not vendored inside the Python application.
For pipx and source installations, `work doctor --fix` can install the missing
dependency through an existing Homebrew installation after explicit approval.
