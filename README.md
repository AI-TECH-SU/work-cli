# work

`work` is a local-first macOS project workspace manager. One command restores
the terminal and application context for a named project on the current macOS
desktop:

```console
work projectA
```

The v1 CLI is written in Python and uses only the standard library at runtime.
It has no cloud service, account, telemetry, AI integration, background daemon,
or macOS Accessibility requirement. Configuration is ordinary TOML and every
external command can be inspected with `work projectA --dry-run`.

## What it restores

- a named tmux session;
- an optional development command in a dedicated tmux window;
- a VS Code workspace or folder with a named VS Code profile;
- a Chrome profile and configured URLs;
- the project's `PROJECT.md`, opened with the VS Code workspace.

The tmux session is the canonical boot marker. On every invocation, `work`
also checks the configured resources and repairs anything it can safely detect
as missing. Detecting an individual closed Chrome tab is not possible without
browser automation, so URLs are reopened when the workspace is first booted or
the configured Chrome profile is no longer running.

## Requirements

- macOS
- Python 3.11 or newer
- tmux
- VS Code and/or Google Chrome when enabled for a project

No application receives permission to create or switch Mission Control Spaces.
For v1, select the desired desktop, open Terminal, and run `work <project>`.

## Install for development

Use an editable installation inside a project virtual environment:

```console
cd /path/to/work-cli
./scripts/setup-dev.sh
source .venv/bin/activate
```

The setup script creates `.venv` when needed and installs this repository in
editable mode. Set `PYTHON` to use a specific interpreter, for example
`PYTHON=python3.13 ./scripts/setup-dev.sh`.

Confirm that the active command comes from the virtual environment:

```console
which work
work --version
```

`which work` should resolve to `.venv/bin/work`. Because the installation is
editable, changes under `src/workspace_manager/` take effect immediately.
Reinstall only after changing packaging metadata or entry points in
`pyproject.toml`.

If tmux is missing, let `work` repair that system dependency through Homebrew:

```console
work doctor --fix
```

The command prints the exact operation and asks before running it. Published
Homebrew packages declare tmux directly, so the normal release installation is
one command and does not need this repair step:

```console
brew install AI-TECH-SU/tap/work
```

The tap name becomes concrete when this repository has a release location. See
[docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) for the release process and formula
contract.

## Create a project

Run the guided initializer:

```console
work init projectA
```

It detects a `.code-workspace` file and `PROJECT.md` when possible, asks only
for missing choices, and writes:

```text
~/.config/work/
├── config.toml
└── projects/
    └── projectA.toml
```

For scripts or repeatable setup, supply values non-interactively:

```console
work init projectA \
  --root "$PWD" \
  --vscode-profile "Project A" \
  --chrome-profile "Profile 2" \
  --url http://localhost:3000 \
  --startup "npm run dev" \
  --non-interactive
```

Then select the macOS desktop where the apps should open and run:

```console
work projectA
```

## Commands

```text
work <project>                 boot or repair, then attach to tmux
work <project> --no-attach     boot or repair without attaching
work <project> --dry-run       print mutations without performing them
work init <project>            create a project configuration
work edit <project>            open its TOML in your editor
work status [project]          inspect one or all configured projects
work stop <project>            stop its tmux session and development command
work restart <project>         restart tmux and reconcile all resources
work doctor                    validate the local installation
work doctor --fix              offer to install missing tmux through Homebrew
work list                      list configured projects
```

`work stop` does not close VS Code or Chrome because either application may be
shared with unrelated work. Closing shared GUI applications would be surprising
and unsafe.

## Configuration

Example `~/.config/work/projects/projectA.toml`:

```toml
version = 1
root = "/Users/me/src/projectA"

[tmux]
session = "work-projectA"

[vscode]
enabled = true
workspace = "projectA.code-workspace"
profile = "Project A"

[chrome]
enabled = true
profile = "Profile 2"
urls = ["http://localhost:3000", "http://localhost:8000/docs"]

[context]
path = "PROJECT.md"
open_in_vscode = true

[startup]
command = "npm run dev"
window = "dev"
# Optional. Exit 0 while the service is healthy.
check = "curl -fsS http://localhost:3000 >/dev/null"
```

Paths in a project file may be absolute or relative to `root`. The optional
startup and health-check strings are explicitly user-authored shell commands;
they run through the user's shell inside the project directory.

Global command overrides live in `~/.config/work/config.toml`:

```toml
version = 1

[commands]
tmux = "/opt/homebrew/bin/tmux"
code = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the design,
file structure, and future boundaries.

## Development

Activate the project virtual environment before running any development
command:

```console
source .venv/bin/activate
```

### Automated checks

```console
./scripts/check.sh
```

This runs the unit tests, byte-compiles the Python sources, checks Git diffs for
whitespace errors, and runs the isolated CLI smoke test. It prefers the virtual
environment's Python and falls back to `python3`; set `PYTHON` to override it.

### Isolated manual testing

Run the repeatable smoke test without touching real Work configuration, creating
a tmux session, or opening applications:

```console
./scripts/smoke-test.sh
```

The script creates temporary XDG directories, supplies inert stand-ins for
external commands, exercises initialization and inspection, and removes the
temporary files when it exits.

For exploratory testing, point the XDG configuration and state directories at a
disposable location so manual tests cannot modify real projects under
`~/.config/work`:

```console
export XDG_CONFIG_HOME="/tmp/work-cli-dev/config"
export XDG_STATE_HOME="/tmp/work-cli-dev/state"
```

Create or replace an isolated project configuration for this repository:

```console
work init work-cli-dev \
  --root "$PWD" \
  --non-interactive \
  --force
```

Inspect the configured behavior without creating a tmux session or opening GUI
applications:

```console
work list
work work-cli-dev --dry-run
work status work-cli-dev --json
```

### Real integration test

Validate the local requirements and install tmux through Homebrew if needed:

```console
work doctor
work doctor --fix
```

Then exercise the complete project-owned lifecycle:

```console
work work-cli-dev --no-attach
work status work-cli-dev
work stop work-cli-dev
```

The non-interactive configuration leaves Chrome disabled. The real boot test
creates a tmux session and opens this repository in VS Code. `work stop` removes
only the project-owned tmux session and intentionally leaves VS Code open.

### Homebrew release testing

Do not use the Homebrew package as the daily development installation. The
editable virtual environment is the live development command; Homebrew should
test only an immutable tagged release and its rendered formula. Before running
tests, use `which work` to confirm whether the shell resolves the editable or
Homebrew-installed executable.

See [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) for release archive and formula
testing.

When finished, leave the environment with:

```console
deactivate
```
