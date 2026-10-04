# Implementation plan

## Accepted v1 workflow

The user runs:

```console
focus projectA
```

The CLI loads the named TOML file, serializes concurrent launches with a local
file lock, creates or selects the configured project desktop, and creates or
repairs resources. It either attaches the calling terminal or opens a Terminal
window per service. Desktop Names needs Accessibility for desktop creation and
switching; separate Terminal windows need Automation access to Terminal.

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
│       └── focus.rb.in
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
  session is created with either the first configured project window or a
  default shell rooted at the project directory. Each configured window has an
  independent root and optional command; missing windows are restored without
  duplicating healthy ones.
- **startup command:** an optional health check is authoritative when supplied;
  otherwise a live tmux window with the configured name is the marker. A
  missing command is started in a new tmux window.
- **VS Code:** `code --status` is searched for the configured workspace/folder.
  A missing workspace is opened in a new window using the configured profile.
  `PROJECT.md` is included in that launch when configured. New windows in the
  existing app are the default across all integrations. Explicit opt-in to VS
  Code's separate-instance mode uses a project-specific user-data directory and
  detects its process directly; this is not a desktop-placement fallback.
  Desktop-managed projects use a stable workspace file per desktop UUID and
  source workspace path, since VS Code reuses an already-open workspace despite
  `--new-window`. Relative folders become absolute and the initial workspace
  settings are copied. Existing copies preserve edits and are not overwritten.
  Source-workspace changes are not automatically merged. Workspace trust remains
  a VS Code user decision; Focus never edits trust state.
- **Chrome:** the current user's process list is checked for the configured
  Chrome profile. URLs open in a new Chrome window on a fresh boot or when that
  profile is not running. Individual tab inspection is deliberately excluded
  because it would require browser automation. `reopen_on_restore = false`
  limits new windows to fresh boots and explicit `--force-gui` requests.
- **Terminal:** grouped tmux sessions give each service window an independent
  selection while sharing the underlying processes. Terminal's `do script`
  command without a target tab opens a new window in its existing app. Access
  is checked before project changes; attached views are reused. Extra app
  instances are forbidden because they can restore unrelated saved windows.
- **stop:** project-owned tmux views and the base session are terminated. Shared GUI
  applications are left intact.

## Safety and auditability

- Subprocesses use argument arrays; no project value is interpolated into an
  implicit shell command.
- Only explicitly configured tmux-window, startup, and health-check commands run
  through the user's shell.
- Configuration validation rejects unknown versions, invalid project names,
  invalid URLs, missing roots, and malformed value types before launching.
- `--dry-run` shows mutating external commands.
- Runtime coordination files stay under the user's XDG state directory.
- No network request is made by `focus` itself.

## Known v1 boundary

Desktop creation, naming, and switching are available through the optional Desktop Names
adapter. It uses the custom app’s UUID inventory, preference store, and native
navigation. Focus reuses a valid assigned UUID, then an unambiguous matching name,
and creates a named desktop when neither exists. Deleted assignments recover
automatically. Focus persists project UUID bindings before subsequent launch
steps so a retry reuses a newly created desktop. Moving application windows remains
outside this integration. Creating and switching desktops require the
Desktop Names app’s macOS Accessibility permission; yabai is not used.

## Distribution boundary

The Homebrew formula declares tmux as a package dependency, so Homebrew users
receive it automatically. tmux is not vendored inside the Python application.
For pipx and source installations, `focus doctor --fix` can install the missing
dependency through an existing Homebrew installation after explicit approval.

## Focus rename validation (2026-10-03)

- `focus` is the primary entry point; `work` invokes the same implementation.
- Existing Work configuration/state directories are reused; new installations
  use Focus directories. Existing session names and desktop UUIDs are preserved.
- All 40 unit tests and isolated CLI smoke checks pass. Shell scripts/completions
  pass syntax checks. The local `focus doctor` passes for the configured project.
- Live isolated check: Focus created a real tmux session and desktop binding;
  after leaving that desktop, Work reused the session and returned to its saved
  UUID. The temporary session was removed and the original desktop restored.
- The local Focus command is linked to the existing editable Work launcher;
  future package installs declare both entry points. No dependency was downloaded.
- Homebrew templates and completion installation support Focus and the Work alias.
  A tagged release and published formula remain pending; no release was published.

## Separate-window acceptance (2026-10-03)

- Live desktop creation, UUID assignment, naming, and switching succeeded.
- The four Wellprept services stayed running in their original tmux panes.
- Background `.command` launches attached clients, but the user could not see
  their windows on the project desktop. A subsequent new-instance experiment
  restored excessive Terminal windows. Those four extra instances were closed.
- The launcher now uses Terminal's new-window scripting command. Its AppleScript
  compiles, and unit/smoke checks cover access denial before changes, independent
  views, migration, and reuse. Live visible placement remains pending user
  validation; process state alone is not acceptance evidence.

## Live new-window test (2026-10-03)

- The previous desktop UUID was no longer present. Its binding was preserved,
  and a replacement project desktop was created and named successfully.
- A single forced GUI launch requested the VS Code and Chrome windows, then
  failed on the first Terminal window with Apple Events error `-1743`. No
  successful Terminal-window creation or complete desktop layout was verified.
- Reading Terminal's version had incorrectly passed the initial permission
  check. The check now uses `AEDeterminePermissionToAutomateTarget` for Terminal's
  `core/dosc` event without sending that event. A live query returned `-1743`;
  rerunning Focus then stopped before desktop, app, or service mutations.
- The full live test remains blocked on the calling app's macOS Automation
  permission. Codex's Computer Use tool also forbids inspecting Terminal, so
  attached clients alone must not be reported as visible-window confirmation.

## Automatic missing-desktop recovery (2026-10-03)

- Missing assignments now resolve by project name or create a named replacement;
  no manual binding-file cleanup is required. A saved replacement is reused even
  when the original explicitly configured UUID is unavailable.
- All 53 unit checks and the isolated CLI smoke test pass. Recovery tests cover
  replacement creation, custom names, name reuse, ambiguity, failed creation,
  and retries after a failed rename without another creation.
- A live test used an isolated stale UUID binding for Wellprept. Focus found the
  existing named desktop, switched successfully, and repaired the test binding
  without creating another desktop. The original desktop was restored afterward.
- This validates desktop recovery; the separate Terminal Automation blocker
  still prevents a complete application-window acceptance test from Codex.

## VS Code workspace identity fix (2026-10-03)

- Confirmed the installed VS Code window manager explicitly reuses an open
  workspace before its new-window creation path. The primary-source equivalent
  is [Microsoft's window manager](https://github.com/microsoft/vscode/blob/main/src/vs/platform/windows/electron-main/windowsMainService.ts).
- Added a workspace file per desktop UUID, preserving source folders and initial
  settings while keeping the existing VS Code app and configured profile.
- The live `focus wellprept-foundation --no-attach` run opened a distinct window
  named `wellprept-foundation (Workspace)` while the original `Foundation` window
  remained open. The main VS Code process ID was unchanged. Verified both windows
  in VS Code's Window menu and inspected the new window, folders, and profile.
- The new workspace opened in Restricted Mode. Desktop placement still needs
  user confirmation; the screenshot of a selected app window alone does not
  establish its Space. Terminal remains blocked by Automation permission.
- All 59 unit checks and isolated CLI smoke checks passed, including JSONC
  settings, path resolution, edit preservation, retries, replacement desktops,
  and an original workspace already open elsewhere.

### Project desktop lifecycle correction (2026-10-03)

The user clarified the lifecycle: refocus an existing project desktop by switching
only; create a missing desktop and move already-open project windows; stopping
closes that desktop's windows, stops project services, and removes the desktop.
This supersedes the earlier workspace-copy and always-reconcile behavior.

Implemented in the maintained Focus checkout:

- An existing project desktop returns before path/executable validation, Terminal
  Automation checks, service reconciliation, or application launch. Explicit
  `--force-gui` repairs a partial desktop using existing windows first.
- The original VS Code workspace file/profile is retained. Removed the generated
  per-desktop workspace descriptor. Match a unique workspace title plus exposed
  project document paths, including configured symlinked workspace folders.
- Persist window ID, owning PID, app bundle, and OS boot identity per project resource.
  Invalidate saved IDs after reboot before matching live windows. Revalidate
  this identity and actual desktop membership before/after moves. Persist before
  moving so a failed move retries the same window without creating a duplicate.
- Adopt the sole existing Chrome window on a dedicated project desktop during
  repair; never infer Chrome ownership from a shared process or page title.
- `focus stop <project>` closes individual windows exclusively on that desktop,
  honors save/close prompts, stops owned tmux groups/services, and removes the
  desktop only after emptiness and native UUID removal are verified. Windows on
  other desktops and all-desktop windows are preserved. Without a dedicated
  project desktop, only owned services are stopped.
- Updated completions, product constraints, README, and the personal code-craft
  ledger to make reuse-first and switch-only behavior the recorded default.

Desktop Names now exposes scoped `windows`, `move-window`, `close-window`, and
`remove` commands. They run through its existing signed app and Accessibility
identity. Moves use the runtime-resolved SkyLight asynchronous bridge submission
function on current Tahoe; removal uses the exposed Mission Control action after
switching away and checking emptiness again. No yabai installation, scripting
addition, SIP changes, new certificate, or permission changes were performed.

Validation so far: 71 Focus unit tests plus isolated smoke/syntax checks pass;
Desktop Names' 18 Swift assertions and 22 Python launcher/signing tests pass;
the native Swift build and C compilation pass. A read-only native probe confirms
the corrected asynchronous submission symbol exists on this Mac.

The first live move used the operation's direct perform selector and failed its
membership verification: original Foundation window 36961 (VS Code PID 78945)
remained on Focus, with no replacement window opened. The corrected submission
path is now signed and installed, as recorded in the stop test below. Live window
movement has not yet been retested; the stop test verifies closing and removal.
Terminal-window creation from Codex remains blocked by Automation permission;
no Terminal UI inspection/control workaround was used.

### Stop command naming (2026-10-04)

Use `focus stop <project>` for the full project desktop lifecycle; removed the
redundant `unfocus` command. Service shutdown remains an internal helper so
`focus restart` retains the desktop and its windows. Projects without a dedicated
desktop keep service-only shutdown. Updated CLI routing, completions, close-prompt
guidance, product documentation, and regression coverage for restart and stop.
Validation: all 73 unit tests and isolated CLI smoke/syntax checks pass. Both
local `focus` and `work` entry points expose `stop`; `unfocus` is no longer a
command. Live cleanup was subsequently verified below.

### Live stop verification (2026-10-04)

- Rebuilt and updated the managed Desktop Names app using the existing signing
  identity. No certificate or permission settings were changed.
- The first stop attempt failed before closing windows or stopping services:
  TextEdit returned AX error -25204 despite owning no WindowServer windows.
  Desktop Names now ignores an AX inspection failure only when a fresh
  WindowServer inventory confirms the process owns no windows, including
  offscreen windows. An app with windows still blocks cleanup on inspection failure.
- Confirmed the reproduced TextEdit query now returns an empty inventory.
- Retried `focus stop wellprept-foundation`: desktop window cleanup and emptiness
  verification completed, then the project desktop was removed. Independently
  verified that only its UUID disappeared, all other desktop UUIDs remained,
  the base tmux session and its four owned views were absent, and both desktop
  and window assignment files were cleared. Focus was active after cleanup.
- Desktop Names' 18 Swift assertions and 22 Python launcher/signing tests passed.
  Launching/moving project windows and Terminal creation remain separate checks.

### Per-app launch and reuse configuration (2026-10-04)

Each project's `[vscode]`, `[chrome]`, and `[tmux]` table now accepts independent
`launch_mode = "window" | "instance"` and `reuse_existing = true | false` settings.
Defaults preserve existing window/reuse behavior. Generated configurations expose
the settings; the older VS Code `separate_instance` flag remains accepted, with
conflicting settings rejected.

Reuse is evaluated first. Disabling it leaves previously saved/matching windows
alone and requires a new window ID after launch; an app that only focuses its old
window produces an error. A newly requested window whose move failed is retried
without opening another. Normal focus on an existing project desktop still only
switches, regardless of launch preferences.

VS Code and Chrome instance modes use persistent project-specific user-data
directories. Terminal instance mode opens a generated executable command document
through Launch Services in a new process. It does not request AppleScript
Automation or discard saved Terminal state; Terminal can restore other windows.
Placement requires the new service's title match and does not adopt unrelated
restored windows. New clients use independent owned tmux views without restarting
services or detaching old clients when reuse is disabled.

Validation: 89 tests, isolated smoke checks, Python syntax checks, and diff checks
pass. Tests cover each app's configuration choices, legacy conflicts, launch
arguments, existing-window preservation, failure retries, strict Terminal window
selection, ownership, dry-run behavior, and switch-only refocus. New instance modes
have not been launched against real GUI apps; existing user project settings were
not changed. Updated setup/reference docs and the Focus code-craft guideline.

### User-defined applications (2026-10-04)

Projects can now add `[[apps]]` entries with a user-chosen name, installed bundle
identifier, document/URL targets, independent launch and reuse policies, and
optional title/document matching rules. Complete argument arrays can override
the default macOS launcher for each mode. Arguments are passed without a shell;
relative executable paths resolve against the project root.

Generic resources use Desktop Names window inventory even without desktop
management. Existing windows require a saved identity or explicit unique match;
newly requested windows must have a new identity and satisfy configured matching
rules. Saved bindings include the project root and app target configuration, so
changing either cannot silently reuse an old saved target. No-desktop projects
track windows without moving them, and retain service-only shutdown. Existing
desktop refocus remains switch-only.

Status includes a read-only app window map. Doctor checks enabled applications
and their selected launchers, allowing generic-only projects without VS Code or
Chrome. Disabled VS Code no longer requires its context document to exist.
Setup and lifecycle docs describe configuration, matching, launcher limitations,
and the distinction between a new process and app-specific profile isolation.

Validation: all 139 tests, isolated CLI smoke checks, Python syntax checks, and
diff checks pass. New coverage exercises parsing, literal commands, window
identity and matching, root changes, failure retries, read-only status, dependency
checks, generic-only reconciliation, and switch/stop behavior. No real GUI apps
were launched for this change; generic application launch and movement still
require live acceptance. No user project configuration or installed app changed.

### Specification-based application extensions (2026-10-04)

Chrome and VS Code now ship as versioned TOML extension specifications. Their
command discovery, typed inputs, launch arguments, status probes, matching, and
restore policies are interpreted by the same engine as local user extensions.
The manager and window lifecycle no longer dispatch application-specific launch,
process-status, or matching handlers. Package metadata includes the bundled
specifications in source and wheel data.

Project `[[apps]]` resources select an extension and supply `options`. The app
list is authoritative when present. Older dedicated tables remain supported by
one compatibility translator, including their storage namespaces and saved
window identities. New initialization writes extension references. Plain bundle
entries remain supported and retain their previous binding fingerprints.

Explicit resource matchers require window inventory even when an extension has
a status probe. Multiple resources cannot adopt each other's tracked windows;
sole-desktop fallback is disabled when multiple resources use the same bundle.
Restart validates extension inputs and commands before stopping services, and
doctor isolates each project's specification resolution errors. App status uses
running/stopped wording because a process probe does not prove an open window.

Validation: all 187 tests, isolated CLI smoke tests, syntax checks, and diff
checks pass. Coverage includes legacy parity, malformed specifications, literal
argument expansion, user-provided extension launch/reuse through the manager,
matching and ownership, configuration migration, and failure ordering. All 15
documentation TOML examples parse; complete project/spec examples resolve
through the actual parser. A wheel build was not run because the development
environment has no setuptools installed; no dependency was installed for this
change. No live GUI launches or user configuration changes were performed.

Added `docs/EXTENSIONS.md` and recorded the specification-based architecture in
the Focus engineering guideline.
