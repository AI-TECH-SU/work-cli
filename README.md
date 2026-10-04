# Focus

`focus` is a local-first macOS project workspace manager. One command restores
the terminal and application context for a named project:

```console
focus projectA
```

Start with the [setup and daily-use guide](docs/SETUP.md), including project TOML,
profiles, permissions, updates, and troubleshooting. Read
[How Focus works](docs/HOW_IT_WORKS.md) for the desktop lifecycle, window reuse,
saved state, and current live verification status. [Application extensions](docs/EXTENSIONS.md)
explains how TOML specifications provide app behavior.

The v1 CLI is written in Python and uses only the standard library at runtime.
It has no cloud service, account, telemetry, AI integration, background daemon,
or Focus-specific macOS Accessibility permission. Configuration is ordinary TOML and
every external command can be inspected with `focus projectA --dry-run`. An
optional Desktop Names integration creates, names, and switches project desktops,
moves existing windows, and removes empty project desktops through our custom
macOS app. Desktop and window management require its Accessibility permission.
Direct bundle entries and extensions without a status probe also use Desktop
Names to identify windows when desktop management is disabled.
Window moving uses a runtime-checked macOS operation; unsupported systems report
a failure without opening duplicate windows. The corrected move path still needs
a live launch test. Default Terminal window mode requires macOS Automation access to Terminal.

## Compatibility with Work

`focus` is the primary command. `work` remains an alias with identical behavior;
its help and version output identify Focus. Both commands share configurations,
locks, desktop assignments, and tmux sessions. Existing `~/.config/work` and
`~/.local/state/work` directories are reused when their `focus` equivalents do
not exist. New installations use `focus` directories. If both directories exist,
Focus uses the `focus` directory; it does not merge or delete either directory.
Existing configured tmux session names stay unchanged, including implicit names
in legacy Work project files. Newly generated configurations use `focus-` names.
The Python distribution and GitHub repository retain their existing identifiers.

## What it restores

- a named tmux session;
- an optional development command in a dedicated tmux window;
- a VS Code workspace, profile, and project documents through the bundled `vscode` extension;
- a Chrome profile and configured URLs through the bundled `chrome` extension;
- other installed macOS applications through extension specifications or direct `[[apps]]` entries;
- an optional dedicated project desktop, selected before opening applications;
- optional separate Terminal windows for each service.

For desktop-managed projects, `focus <project>` switches to an existing project
desktop and returns immediately. It does not reopen apps, restart services, or
attach another Terminal client. When the desktop is missing, Focus creates and
names one, moves matching existing project windows there, and opens missing
windows. Use `--force-gui` explicitly to repair a partial desktop. Without desktop
management, the existing tmux-based reconciliation workflow remains available.

`focus stop <project>` closes the windows on that dedicated desktop, stops its
services, and removes the empty desktop. Shared apps' windows on other desktops
stay open. Save/close prompts stop cleanup and keep the desktop for a retry.
Cleanup includes windows opened manually on the dedicated desktop.
For projects without a dedicated desktop, it stops only their tmux services.

## Requirements

- macOS
- Python 3.11 or newer
- tmux
- the applications enabled for a project
- Desktop Names for desktop management, direct bundle entries, or extensions without a status probe

Without the optional adapter, select the desired desktop, open Terminal, and run
`focus <project>`. See [Desktop Names integration](#desktop-names-integration) to associate a project
with a desktop and return to it automatically.

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
which focus
focus --version
```

The setup script also installs Focus's packaged zsh completion into Homebrew's
standard `site-functions` directory when it is writable. Zsh loads packaged
completions after its generic completion system has been initialized:

```zsh
autoload -Uz compinit
compinit
```

After opening a new shell, project names and commands complete with Tab:

```console
focus wellprept-fo<Tab>
focus stop wellprept-fo<Tab>
```

`which focus` should resolve to `.venv/bin/focus`. Because the installation is
editable, changes under `src/workspace_manager/` take effect immediately.
Reinstall only after changing packaging metadata or entry points in
`pyproject.toml`.

If tmux is missing, let `focus` repair that system dependency through Homebrew:

```console
focus doctor --fix
```

The command prints the exact operation and asks before running it. A future
published Homebrew formula will declare tmux directly:

```console
brew install AI-TECH-SU/tap/focus
```

The local tap does not contain a published Focus formula yet. Use the source
installation above for this development version. Desktop Names is installed
separately; follow the [setup guide](docs/SETUP.md#2-set-up-desktop-names). See
[docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) for the release process.

## Create a project

Run the guided initializer:

```console
focus init projectA
```

It detects a `.code-workspace` file and `PROJECT.md` when possible, asks only
for missing choices, and writes:

```text
~/.config/focus/
├── config.toml
└── projects/
    └── projectA.toml
```

For scripts or repeatable setup, supply values non-interactively:

```console
focus init projectA \
  --root "$PWD" \
  --vscode-profile "Project A" \
  --chrome-profile "Profile 2" \
  --url http://localhost:3000 \
  --startup "npm run dev" \
  --non-interactive
```

Then select the macOS desktop where the apps should open and run:

```console
focus projectA
```

## Commands

```text
focus <project>                 switch to its desktop, or create and launch it
focus <project> --no-attach     skip Terminal setup on first launch
focus <project> --dry-run       print mutations without performing them
focus <project> --force-gui     repair windows/services on an existing desktop
focus init <project>            create a project configuration
focus edit <project>            open its TOML in your editor
focus status [project]          inspect one or all configured projects
focus stop <project>            close desktop windows, stop services, remove desktop
focus restart <project>         restart tmux and reconcile all resources
focus doctor                    validate the local installation
focus doctor --fix              offer to install missing tmux through Homebrew
focus list                      list configured projects
```

`focus stop` closes individual windows on a dedicated project desktop
(`space.target = "project"`). It preserves shared application processes and
windows assigned to all desktops, and refuses to remove the final regular desktop.
Without a dedicated desktop it only stops project services. `focus restart`
restarts services and restores resources while retaining the desktop and its windows.

## Configuration

Example `~/.config/focus/projects/projectA.toml`:

```toml
version = 1
root = "/Users/me/src/projectA"

[tmux]
session = "focus-projectA"
# "current" attaches the invoking terminal (default); "windows" opens Terminal windows.
presentation = "windows"
launch_mode = "window"  # Or "instance"; applies to Terminal clients.
reuse_existing = true

[[tmux.windows]]
name = "api"
root = "services/api"
command = "python -m api"

[[tmux.windows]]
name = "web"
root = "apps/web"
command = "npm run dev"

[[apps]]
name = "editor"
extension = "vscode"
launch_mode = "window"
reuse_existing = true

[apps.options]
workspace = "projectA.code-workspace"
profile = "Project A"
context = "PROJECT.md"

[[apps]]
name = "browser"
extension = "chrome"
launch_mode = "window"
reuse_existing = true

[apps.options]
profile = "Profile 2"
urls = ["http://localhost:3000", "http://localhost:8000/docs"]

[space]
# Optional. Requires our Desktop Names app; switching needs Accessibility.
enabled = true
manager = "desktop-names"
target = "project"
# name = "Project A"  # Defaults to the project name.
# uuid = "EXISTING-DESKTOP-UUID"  # Explicitly use an existing desktop instead.
```

Paths in a project file may be absolute or relative to `root`. The optional
startup and health-check strings are explicitly user-authored shell commands;
they run through the user's shell inside the project directory.

When `tmux.windows` is configured, the first entry becomes the session's first
window and each remaining entry is created independently. Window roots may be
absolute or relative to the project root. Commands are optional; omit one to
open an interactive project shell. Missing configured windows are restored during setup or an explicit repair. The legacy `[startup]` window remains available for a
single project-wide development command.

As an alternative to the service list, a single startup command can be configured
as follows. Do not put the same server in both places:

```toml
[startup]
command = "npm run dev"
window = "dev"
# Optional. Exit 0 while the service is healthy.
check = "curl -fsS http://localhost:3000 >/dev/null"
```

`presentation = "windows"` with `launch_mode = "window"` opens one Terminal window
per service using Terminal's scripting interface in the existing app. Each window
has an independent tmux view of the same service processes. The default mode does
not start a separate Terminal instance. Restoration reuses attached views when
`reuse_existing = true`; `--no-attach` skips opening them. macOS may ask the calling app for
Automation access to Terminal. Setup checks this before changing desktops or
launching project resources. Switching to an existing desktop does not need it. Failed service output remains available in tmux.
This permission belongs to the app that runs Focus. A test launched from Codex
can require Codex-to-Terminal permission; using Focus from your own terminal
does not require granting Codex that access. The check tests permission for
Terminal's actual script command, since reading its version can be allowed
even when Automation is denied.

With the default `reuse_existing = true`, Focus first reuses a recorded project window or an unambiguous matching VS Code
workspace. It moves that exact window and verifies its new desktop before
continuing. The original workspace file, selected profile, extensions, trust,
and open editor state are retained. No workspace descriptor is copied.

Missing windows use the app's configured launch mode. Focus records the new
window's ID, owning process, and app bundle, then moves it if the app opened it
elsewhere. It never infers Chrome project ownership from a shared app process or
page title; it uses a recorded window or an exposed exact configured URL. An
ambiguous match stops with an error rather than moving an arbitrary window.

Each `[[apps]]` entry and the `[tmux]` table independently accept
`launch_mode = "window" | "instance"` and `reuse_existing = true | false`.
Reuse happens first; launch mode applies when a new window is needed. Disabling
reuse requests new windows during initial launch or repair and leaves existing
ones in place. Refocusing an existing desktop still just switches.

In instance mode, the bundled extension specifications give VS Code/Chrome
separate project/resource settings and sign-in storage. Terminal instance mode
starts a new process per service client and can restore Terminal's saved windows;
it is explicit opt-in. The older
`[vscode]`, `[chrome]`, and `[context]` sections are accepted through compatibility
translation, including the `vscode.separate_instance` alias. See
[per-app setup](docs/SETUP.md#choose-what-opens-and-how-per-app) for examples and
behavior when an application refuses to create a new window.

The project file chooses **what** to open. The extension specification supplies
the option schema, launch commands, and window matching for **how** that app works.
Chrome and VS Code use the same extension engine as locally supplied app specifications;
adding another supported app does not require a Python handler.
An `apps` list chooses which apps run; omitted legacy sections add no applications.
Existing configurations without an `apps` key keep their original defaults.

### Add another application

Add an `[[apps]]` entry to the project configuration; no built-in handler is
required. For example, to open a project document in TextEdit:

```toml
[[apps]]
name = "notes"
bundle_id = "com.apple.TextEdit"
open = ["PROJECT.md"]
launch_mode = "window"
reuse_existing = true
match_title = "PROJECT.md"
```

`name` is your resource label. `bundle_id` identifies the installed app, and
`match_title` is a case-sensitive glob against the title the app exposes. Choose
a title or document rule that uniquely identifies this project's window. Existing
windows are only adopted from a saved identity or explicit matching rules.

By default, Focus uses macOS `open -b` (`open -n -b` in instance mode) and verifies
that the requested window appeared. Some apps reuse an old window or do not
support separate instances. For those with their own launch CLI, configure a
literal `window_command` or `instance_command` argument list instead of `open`.
See [other application setup](docs/SETUP.md#add-other-applications) for direct-entry
fields, finding bundle IDs, matching rules, and app-specific limits. For behavior
you want to reuse across projects, write a local [extension specification](docs/EXTENSIONS.md)
and select it with `extension = "your-extension"`. Terminal service views remain
configured through `[tmux]`.

### Executable overrides

Global command overrides live in `~/.config/focus/config.toml`:

```toml
version = 1

[commands]
tmux = "/opt/homebrew/bin/tmux"
code = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
desktop_names = "/opt/homebrew/bin/desktop-names"
```

### Desktop Names integration

Use `focus init projectA --desktop` for a new project, or enable `[space]` as above.
Focus reuses an existing desktop with the project's name or creates and names
one if none exists, remembering its UUID. Later runs only switch to that desktop;
use `--force-gui` to repair missing resources. `target = "current"` instead
renames the current desktop on every run without retaining an assignment.
Desktop Names owns the names and navigation; Focus stores only the assignment in
`~/.local/state/focus/desktops/<project>.json` (or `$XDG_STATE_HOME/focus/desktops`).
If an assigned desktop disappears, Focus searches for the same name and creates
a replacement automatically when none exists. It saves the new UUID before
switching or renaming, so a failed later step cannot create duplicates on retry.
To prefer a particular existing desktop, set `[space].uuid` explicitly. If that
UUID disappears, a saved replacement is reused. Multiple matching names without
a valid UUID are rejected so Focus cannot choose the wrong desktop.

Use `desktop-names create` to create a desktop and print its UUID,
`desktop-names list` to inspect UUIDs, `desktop-names rename current "Name"`
to rename, and `desktop-names switch UUID` to switch directly. Build the custom
Desktop Names app before using its source launcher; a packaged installation uses
`desktop-names setup`. Commands never initiate signing or setup automatically.
For a source checkout, set `[commands].desktop_names` to the executable
`desktop-names/scripts/desktop-names.py` launcher using its absolute path.

`focus doctor` checks the launcher; `focus <project> --dry-run` reports the desktop
plan without reading or changing desktop state. Desktop operations fail before
project resources launch when desktop selection fails. A later window-placement
failure can leave a partial project desktop, which can be repaired explicitly.
The integration supports a single display and regular desktops. It reuses matching
windows and verifies their destination; the updated move path still needs live
launch verification. macOS application Space assignments can affect where new windows appear.
An attached tmux client confirms a connection, not a window's visibility or Space.
Names appear in Desktop Names, not native Mission Control labels.

The yabai adapter has been retired. Replace old `manager = "yabai"` settings with
`manager = "desktop-names"`, remove `timeout_seconds`, and replace the global
`commands.yabai` setting with `commands.desktop_names`. Existing disabled projects
remain opt-in. No yabai installation or service is needed.

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

Run the repeatable smoke test without touching real Focus configuration, creating
a tmux session, or opening applications:

```console
./scripts/smoke-test.sh
```

The script creates temporary XDG directories, supplies inert stand-ins for
external commands, exercises initialization and inspection, and removes the
temporary files when it exits.

For exploratory testing, point the XDG configuration and state directories at a
disposable location so manual tests cannot modify real projects under
`~/.config/focus`:

```console
export XDG_CONFIG_HOME="/tmp/work-cli-dev/config"
export XDG_STATE_HOME="/tmp/work-cli-dev/state"
```

Create or replace an isolated project configuration for this repository:

```console
focus init work-cli-dev \
  --root "$PWD" \
  --non-interactive \
  --force
```

Inspect the configured behavior without creating a tmux session or opening GUI
applications:

```console
focus list
focus work-cli-dev --dry-run
focus status work-cli-dev --json
```

### Real integration test

Validate the local requirements and install tmux through Homebrew if needed:

```console
focus doctor
focus doctor --fix
```

Then exercise the complete project-owned lifecycle:

```console
focus work-cli-dev --no-attach
focus status work-cli-dev
focus stop work-cli-dev
```

The non-interactive configuration leaves Chrome disabled. The real boot test
creates a tmux session and opens this repository in VS Code. `focus stop` removes
only the project-owned tmux session in this example because desktop management
is disabled. For a dedicated-desktop test, follow the [setup guide](docs/SETUP.md);
stop then closes its windows and removes the empty desktop.

### Homebrew release testing

Do not use the Homebrew package as the daily development installation. The
editable virtual environment is the live development command; Homebrew should
test only an immutable tagged release and its rendered formula. Before running
tests, use `which focus` to confirm whether the shell resolves the editable or
Homebrew-installed executable.

See [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) for release archive and formula
testing.

When finished, leave the environment with:

```console
deactivate
```

Desktop Names signing uses its own shared per-user setup, whether installed
standalone or bundled by a host. Run the configured Desktop Names launcher with
`setup` before switching; add `--identity FULL_CERTIFICATE_SHA1` to reuse an existing
certificate. Focus never creates certificates during project launch. If a bundled
payload changes, quit Desktop Names and rerun that same launcher's `setup` command;
signing and update errors stop project launch instead of using an ad-hoc copy.
