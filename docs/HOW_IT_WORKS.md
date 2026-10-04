# How Focus works

Focus reads a named TOML project and coordinates three local components:

| Component | Responsibility |
| --- | --- |
| Focus CLI (`focus`, or compatible `work`) | Project configuration, extension interpretation, service reconciliation, window matching, saved assignments, operation locks |
| tmux | Foreground service processes, retained output, independent Terminal views |
| Desktop Names | macOS desktop names/UUIDs, desktop creation/switching/removal, window inventory/move/close |

There is no Focus daemon, cloud account, or synchronization service. The custom
Desktop Names app provides the desktop operations; yabai is not used. Follow
[Setup](SETUP.md) to connect the components and configure permissions.

## Focus: switch or create

For a project with `[space] enabled = true` and `target = "project"`:

1. Resolve a valid explicitly configured UUID, then a saved assignment, then a
   unique desktop name match. Duplicate matching names are an error.
2. If a desktop already exists, switch to it and return. No applications are
   reopened, services restarted, or Terminal clients attached on this path.
3. Otherwise, check required launch permissions, paths, and executables. Create
   a regular desktop, save its UUID, switch to it, and give it the project name.
4. Create/reuse the tmux session and configured service windows, then restore
   enabled `[[apps]]` resources using existing project windows where they can be
   identified. VS Code and Chrome are bundled extension specifications in this
   same resource list.
5. Open/reuse the configured Terminal views and verify window desktop membership.
   Return to the project desktop after application launches.

Saving the desktop UUID before later launch steps lets a retry reuse a partial
desktop. Because normal refocus only switches, use `--force-gui` to finish a
partial launch. If someone deletes the desktop in Mission Control, Focus searches
again and creates a replacement when necessary; manual state-file deletion is
not needed.

`--no-attach` skips Terminal client setup/attachment, while still restoring services
and enabled GUI apps. `--dry-run` prints the intended mutations without changing
desktops, launching applications, or starting services; it is not a live permission
or placement check.

## Reusing windows

By default (`reuse_existing = true`), Focus first checks its saved window identity (window ID, owning process, bundle,
and macOS boot identity). Boot identity prevents an old saved ID from being used
after a reboot. If that window is gone, it searches for a unique project match:

- **Bundled `vscode` specification:** the original workspace/folder title, plus the active document's
  project path when the app exposes it. Workspace folder paths and symlinks are
  resolved. The original workspace and editor/profile state are retained.
- **Bundled `chrome` specification:** a recorded window or an exposed document URL that exactly matches a
  configured URL. During repair, a sole Chrome window already on the project
  desktop can be adopted. A shared Chrome process or a page title does not prove
  project ownership. This fallback is disabled with explicit resource matchers or
  multiple enabled resources for the same bundle. An existing window is reused
  without resetting its tabs.
- **Terminal:** an owned tmux view and its project/service window title.
- **Direct bundle entries:** a recorded identity or a unique match for the entry's
  `match_title`/`match_document` rules within its bundle ID. Both rules must match
  when both are present. There is no adoption based only on an app bundle.

If a unique window exists elsewhere, Focus requests a move and checks its actual
desktop membership afterward. If none can be identified, it opens one new window
in the existing app, records it, and moves it if necessary. Ambiguous matches
stop the operation. A failed move does not launch a replacement window.
An active resource's saved window cannot be claimed by another app resource.

Each app independently chooses `launch_mode = "window"` or `"instance"` and
`reuse_existing = true` or `false`. Reuse takes priority; launch mode applies when
a new window is needed. With reuse disabled, saved/matching existing windows are
left alone, and only a new window from the launch may be placed. An already
requested new window whose move failed is retried without opening another.

New windows use the configured profiles. Reused windows retain their current
profile/state; changing configuration does not convert an existing window's profile.
The bundled specifications configure separate user-data directories for VS Code
and Chrome instance mode. Terminal instance mode uses Launch Services to open an owned command
document in a new process; Terminal may also restore saved windows. Default window
mode uses the existing application. See [per-app setup](SETUP.md#choose-what-opens-and-how-per-app).

Window movement uses a macOS private operation resolved at runtime. Fullscreen
and all-desktop windows cannot be moved by this adapter; unsupported operations
fail explicitly. This behavior needs validation on the actual macOS version.

## Applications and extension specifications

Every `[[apps]]` entry is an application resource. It can select a reusable TOML
specification with `extension`, or declare its bundle identity and launch details
directly. The bundled `vscode` and `chrome` specifications use the same loader and
interpreter as a local extension. Focus does not dispatch to a Python application
handler based on the extension ID.

A specification declares its option schema/defaults, bundle ID, executable
discovery, launch argument templates, window matcher, and optional status probes.
The project supplies values in `[apps.options]` and controls launch/reuse policy.
Supported matcher primitives include workspace title/document rules, exact
document values, and globs. Desktop selection, verified window movement, saved
identity, and cleanup remain common lifecycle operations. Terminal service views
remain part of tmux orchestration. See [Application extensions](EXTENSIONS.md) for
the specification contract.

Existing `[vscode]`, `[chrome]`, and `[context]` sections are translated into
extension resources. They do not keep a separate launch path. New initialization
uses `[[apps]]`; migration is optional. With an `apps` list present, omitted
legacy sections add no default applications. Explicit legacy sections still
translate, and conflicting enabled declarations are rejected. Files without an
`apps` key retain their original defaults. Valid legacy window bindings are
upgraded to the extension identity format without reopening their windows.

Direct-entry matching uses case-sensitive globs. File document URLs are decoded to
absolute paths; those paths and absolute document-path patterns are normalized
through the filesystem, including symlinks. Other document URIs are compared as
exposed. With no matchers, only a saved owned window can be reused. Saved identities
are bound to the project root, configured target, matching rules, and launch mode,
so a configuration change requires identification again. After a launch, Focus requires a unique
new window; configured matchers must also match it. A launch that merely
activates an existing window or produces ambiguous candidates fails explicitly.

For a direct entry, the default launch asks macOS to open the bundle and requested resources using
`open -b`, adding `-n` for instance mode. Each application determines how that
request behaves; there is no universal new-window guarantee or automatic profile
isolation. A configured `window_command` or `instance_command` replaces the
entire launch command for that mode. Its literal argument array runs in the
project root without a shell or placeholder expansion. Relative executable paths
containing `/` resolve against that root; bare command names use `PATH`. Documents,
URLs, and isolation flags belong in that command; combining it with `open` is rejected.

All desktop-managed app resources use Desktop Names to identify and verify their
windows. Without desktop management, an extension can use its declared status
probe for reconciliation. Direct entries and extensions without a probe still
use Desktop Names inventory, but do not move windows. Explicit app-level title
or document rules also require inventory instead of a probe. A probe can report process
or workspace presence without proving a visible window; its signal is defined
by the specification. Without a dedicated project desktop, `stop` remains a
services-only operation and leaves application windows open.

## Services and Terminal views

Each `[[tmux.windows]]` entry runs in its configured directory. Its command runs
once when that service window is created. Missing windows are restored by repair;
dead service windows keep their output for diagnosis and need `focus restart`
after fixing the underlying problem.

With `presentation = "windows"`, each Terminal window attaches to an owned tmux
group view. Those views share the original service processes but have independent
selected windows, so selecting `api` in one Terminal does not switch all the others.
Focus marks ownership and refuses to adopt a conflicting, unrelated tmux view.
When reuse is disabled, a new client gets a separate owned view without detaching
or changing the existing client's selection; the underlying services are not duplicated.

With `presentation = "current"`, the invoking terminal attaches to the base tmux
session. Closing a client does not by itself mean its underlying service stopped.
Focus manages the tmux sessions it owns; it does not supervise arbitrary external
daemons or containers.

## Stop: desktop cleanup

`focus stop <project>` treats a dedicated project desktop as the cleanup boundary:

1. Find the assigned desktop without creating one, and switch to it.
2. Close standard non-Terminal application windows exclusively on that desktop,
   using each application's Close button. Verify each window disappears.
3. Stop the project's owned tmux views and base service session.
4. Close remaining Terminal windows on that desktop and verify it is empty.
5. Switch away as necessary, remove the desktop through Mission Control, and
   verify its UUID is absent. Only then clear the saved assignments.

All exclusive windows on that desktop are included, even ones opened manually.
Shared application processes are kept alive; windows on other desktops and those
assigned to all desktops are preserved. If an app asks to save or refuses to
close, cleanup stops and keeps the desktop. Resolve the prompt and retry; there
is no automatic discard or force quit, and earlier successful closes are not undone.

If window inspection fails, cleanup stops unless WindowServer independently
confirms that the unresponsive process owns no windows, including offscreen ones.
The last regular desktop and nonempty desktops cannot be removed.

If the project desktop is already gone, stop shuts down its services and clears
the stale desktop assignment. Project configuration and source files are never
removed. The next focus can create the project desktop again.

## Restart, repair, and projects without a dedicated desktop

| Operation | Effect |
| --- | --- |
| `focus <project>` on an existing project desktop | Switch only |
| `focus <project> --force-gui` | Reconcile missing services/windows using existing windows first |
| `focus restart <project>` | Stop services internally, then reconcile resources while retaining the desktop and app windows |
| `focus stop <project>` | Close dedicated-desktop windows, stop services, and remove the desktop |
| Stop with desktop management disabled or `target = "current"` | Stop only the project's tmux services/views |

Without a dedicated project desktop, normal invocation uses the original resource
reconciliation flow. `target = "current"` explicitly names the current desktop
on restoration. With desktop management disabled, extensions that use status
probes can have less precise detection; for example, `--force-gui` can open
additional GUI windows. Direct bundle entries and extensions without probes
still verify window identity through Desktop Names, without moving windows.
Switch-only behavior requires a dedicated project desktop.

## Configuration and saved state

| Data | Default location on a new installation |
| --- | --- |
| Executable overrides | `~/.config/focus/config.toml` |
| Project definitions | `~/.config/focus/projects/<project>.toml` |
| Local extension specifications | `~/.config/focus/extensions/<extension>.toml` |
| Project → desktop UUID | `~/.local/state/focus/desktops/<project>.json` |
| Resource → window identity | `~/.local/state/focus/windows/<project>.json` |
| Extension instance data | `~/.local/state/focus/extensions/<extension>/<project>/<resource>/` |
| Legacy VS Code / Chrome instance data | `~/.local/state/focus/vscode/<project>/`, `~/.local/state/focus/chrome/<project>/` |
| Terminal instance command documents | `~/.local/state/focus/terminal-launchers/<project>/` |
| Operation locks | `~/.local/state/focus/locks/` |
| Desktop names/preferences | `~/Library/Application Support/DesktopNames/preferences.json` |
| Signing metadata | `~/Library/Application Support/DesktopNames/signing/` |
| Managed signed app | `~/Applications/Desktop Names.app` |

`XDG_CONFIG_HOME` and `XDG_STATE_HOME` replace the corresponding base directories.
For configuration and state independently, an existing `work` directory is reused
if no `focus` directory exists. If both exist, `focus` wins; no merge occurs.
Both CLI names follow the same selection rules and share locks and assignments.

New configurations use `focus-<project>` tmux names. Existing explicit names are
preserved, as are implicit `work-<project>` names in legacy Work configurations.
Desktop Names stores names by stable UUID, not by the desktop's current number.

## Verification status

Evidence as of **2026-10-04**:

- The CLI's automated tests and isolated smoke checks pass. They verify decisions
  and failure handling, not whether a window is visible on a real desktop.
- Live desktop switching has passed. The latest signed update was installed
  with the existing identity and worked without another permission change.
- Live `focus stop wellprept-foundation` passed: window cleanup and emptiness
  checks completed, its service session and four owned views stopped, only its
  desktop UUID disappeared, and its saved assignments were cleared.
- The corrected native window-move path and separate Terminal creation still
  need a full live launch test. Do not infer their success from the stop test.
- Per-app launch/reuse policies are covered by automated checks; the new instance
  modes have not been launched against real GUI apps as part of their implementation.
- Additional `[[apps]]` integrations and the extension refactor have not yet been
  live-tested with GUI apps; actual launch behavior depends on each app's support
  for the configured command.

`focus status` reports tmux, startup, and app-detection signals, including a
read-only `apps` map keyed by app resource name in JSON output. For direct entries,
the signal is an identified window; an extension can supply a status probe. Its `booted` value
means the tmux session exists; it does not certify service health, open URLs,
Terminal visibility, or desktop placement. See the dated
[implementation/validation log](IMPLEMENTATION_PLAN.md) for detailed evidence.
