# Implementation plan

This opening section describes the current design. The dated records below are
historical evidence; later corrections supersede earlier implementation choices.

## Accepted workflow

```console
focus projectA
```

The CLI loads a named TOML project under a per-project lock. With a dedicated
project desktop, an existing desktop is switched to immediately; it does not
relaunch resources or attach clients. A missing desktop is created and named,
then its enabled resources are restored. `--force-gui` explicitly repairs an
existing partial desktop. Matching project windows are reused and moved before
new windows are requested. Desktop Names supplies desktop/window operations
through its existing Accessibility identity; yabai is not used.

All apps and service providers use `[[apps]]`. Bundled `vscode`, `chrome`, and
`tmux` TOML specifications are interpreted by the same generic application and
session capabilities as local extensions. An explicit resource list is
authoritative: no implicit tmux session or unused executable is required.
Legacy app, tmux, and startup sections translate into the same model.

`focus stop` closes individual windows exclusively on a dedicated project
desktop, stops configured service sessions, verifies emptiness, and removes the
desktop. Without a dedicated desktop, stop only shuts down service resources.
`focus restart` restarts services while retaining the desktop and app windows.

## Implementation organization

The core owns configuration, project/desktop coordination, operation locks,
verified window identity/movement, and cleanup. Extensions supply provider-specific
commands, option schemas, matching, output formats, ownership keys, environment
rules, and dependency metadata. The engine dispatches by reusable capability;
it does not select a Python handler by provider ID.

```text
src/workspace_manager/
├── cli.py                 # Commands, initialization, status, diagnostics
├── config.py              # Project parsing and initialization rendering
├── app_compat.py          # Legacy sections translated into resources
├── apps.py                # Resource reconciliation and shared lifecycle
├── extensions.py          # Specification loading and GUI capability
├── session_extensions.py  # Generic service and independent-view capability
├── extension_specs/
│   ├── vscode.toml
│   ├── chrome.toml
│   └── tmux.toml
├── terminals.py           # macOS Terminal client presentation
├── desktop.py             # Desktop Names adapter and project assignment
├── windows.py             # Inventory, matching, verified move/close
├── manager.py             # Project operations and orchestration
├── models.py
├── paths.py
└── system.py
```

The [setup guide](SETUP.md), [lifecycle guide](HOW_IT_WORKS.md), and
[extension reference](EXTENSIONS.md) describe the supported contracts.
`packaging/homebrew/focus.rb.in` and `scripts/render_homebrew_formula.py` cover
release packaging. Tests exercise configuration, generic providers, resource
reconciliation, failure recovery, CLI behavior, and native adapter boundaries.

## Reconciliation rules

- **Resource selection:** an `apps` list determines enabled resources. Projects
  without an `apps` key retain legacy defaults. Explicit legacy sections still
  translate; conflicting enabled declarations are rejected. New initialization
  emits extension resources which users can remove or disable independently.
- **Session capability:** declared operations inspect/create the base session,
  restore missing service units, retain failed output, present clients, and stop
  owned resources. Service commands run once on unit creation. Dead units retain
  output and need an intentional restart after the underlying problem is fixed.
  A configured startup check supplies readiness; otherwise the startup unit's
  live/dead state is used. Resource status includes that readiness; the legacy
  session/startup columns remain separate.
- **GUI capability:** on managed desktops, reuse a saved project window or a
  unique semantic match, verify movement, and launch only when needed. Saved
  identity includes window ID, process, bundle, boot identity, and configuration
  context. Failed placement of a newly requested window retries the same window.
  Ambiguous matches and unsupported app behavior fail explicitly.
- **Launch policy:** each resource independently chooses window/instance mode
  and reuse. Reuse takes priority; disabling it leaves old windows in place and
  requires a newly appeared window. Refocusing an existing desktop still only
  switches. Instance isolation is defined by the specification's actual command
  flags; it is not a desktop-placement workaround.
- **Bundled editor/browser behavior:** VS Code uses the original workspace and
  profile, with workspace title/document matching. No workspace descriptor is
  copied. Chrome uses recorded identity or configured document URLs; a sole
  window on the project desktop may be adopted when the declared policy and
  resource ambiguity checks permit it. The specifications provide optional
  instance data directories and non-desktop status probes.
- **Probe-only restoration:** without desktop management, a GUI extension may
  use its declared read-only status probe. `restore_on_fresh_boot` supports a
  fresh configured service session. In an app-only project it uses per-resource
  first-launch state keyed by configuration identity and macOS boot, so initial launch does not
  depend on tmux. Save the marker only after a successful launch request, never
  during dry-run; unreadable state blocks a launch rather than risking duplicates.
  Subsequent restoration honors `reopen_on_restore = false`; an explicit repair
  can still request a launch. Dedicated-desktop stop clears the marker, while
  service-only stop retains it because GUI apps remain open. Probe results do not
  prove visible window placement.
- **Terminal views:** session specifications declare independent views sharing
  the original service processes, including ownership and title commands. The
  common presenter defaults to new windows in the existing Terminal app, with
  Automation checked before project mutations. Explicit instance mode uses an
  owned command document and may restore Terminal's saved windows. Attached
  provider clients do not by themselves prove visible placement.
- **Stop:** close desktop-exclusive windows with native Close actions, honoring
  save prompts. Defer declared session-client bundles until their services are
  stopped. Verify the desktop is empty before removal and clear its assignment
  only after native removal succeeds. Keep shared app processes and windows on
  other/all desktops. Projects without dedicated desktops stop services only.

## Safety and auditability

- Provider operations and GUI launches use argument arrays. Only explicitly
  configured service/startup/check commands cross the user's shell boundary.
- Specifications are local auditable files. Their commands run as the current
  user; Focus does not fetch providers or execute Python plugins.
- Strict validation checks schema, option types, paths, executable requirements,
  duplicate identities, and supported templates before restoration mutations.
- `--dry-run` shows intended mutations without creating desktops, launching apps,
  or starting services. It is not a live permission or placement test.
- Runtime assignments, window identities, launch markers, and locks stay under
  the selected XDG state directory. Both CLI names share configuration and state.
- `focus status` is read-only. `booted` means at least one resource is enabled and
  all enabled resources report ready; it does not imply every service's health
  or a visible desktop layout.
- A normal focus command does not install dependencies or make network requests
  itself. Dependency repair is a separate, explicitly approved operation.

## Platform and live-validation boundary

Desktop Names supplies UUID inventory, naming, creation/switching/removal, and
window inventory/move/close. Focus preserves a valid project assignment, otherwise
uses an unambiguous name match or creates a replacement. Persisting the UUID
before resource launch allows recovery from partial failures. Current desktop
support is for one display and regular desktops; fullscreen/all-desktop windows
cannot be moved by this adapter.

Window movement uses a runtime-resolved macOS private operation and verifies
membership. Unsupported operations and permission failures stop with an error.
Default Terminal window presentation separately requires the calling app's
Automation permission. Instance mode is explicit and follows app behavior.

Automated checks establish configuration/command/lifecycle behavior. Existing
live evidence establishes desktop switching and project stop cleanup, as recorded
below. The corrected window-move path, complete visible Terminal layout, and new
extension/instance configurations still require full live acceptance. A process
or session being present is not a substitute for that test.

## Distribution boundary

The Focus package includes the bundled TOML specifications, not provider binaries.
The Homebrew template installs Python and Focus; tmux is optional and required
only by enabled resources that select it. `focus doctor` checks configured,
enabled providers. Declared Homebrew repair metadata lets `focus doctor --fix`
show the exact install command and request approval; it does not install Homebrew.
No package is installed merely by selecting an extension or launching a project.
A published immutable release archive/formula remains a separate release step.

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

### Specification-based session extensions (2026-10-04)

tmux now ships as a session extension selected through the same `[[apps]]`
resource list. Its specification owns provider commands, flags, inventory
formats, attachment environment, grouped-view ownership markers, and optional
Homebrew installation metadata. The shared session engine handles lifecycle
operations; the Terminal adapter receives resolved commands and window titles.
Explicit resource titles and saved bindings distinguish multiple session
resources. Exact tmux targets prevent prefix matching unrelated sessions.

Legacy `[tmux]` and `[startup]` configurations translate into this resource model
while preserving session and view identities. New initialization emits a tmux
extension entry. Explicit app lists without a session resource never invoke or
require tmux. Doctor checks enabled resources and offers only declared dependency
installations; the Homebrew formula template no longer requires tmux globally.

Status includes configured startup readiness for explicit session resources.
The legacy TMUX and STARTUP fields retain their separate meanings. Apps-only
projects record successful initial probe-based launches so disabling restoration
does not skip the first launch after reboot or repeatedly reopen a closed browser.

Validation: all 226 tests, isolated CLI smoke checks, Python syntax compilation,
and diff checks pass through `scripts/check.sh`. Coverage includes a second
session provider using the same engine, exact targeting and ownership, retained
service output, legacy translation, disabled dependencies, startup readiness,
first-launch state, dependency repair selection, and lifecycle ordering. The 18
documentation TOML blocks parse, both local specification examples validate, and
nine project/resource examples load through the actual configuration parser.
No real tmux commands, GUI launches, package installations, or user project
configuration changes were performed. Live desktop acceptance remains pending.
