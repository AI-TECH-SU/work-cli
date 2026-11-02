# Set up and use Focus

Focus combines a project configuration, optional service extensions, application windows, and
our Desktop Names app. The usual workflow is:

```sh
focus my-project                 # Create its desktop, or switch to it if it exists
focus my-project --force-gui     # Repair missing resources on an existing desktop
focus stop my-project            # Close its windows, stop services, remove desktop
```

`work` is a compatibility command with the same behavior. See
[How Focus works](HOW_IT_WORKS.md) for desktop selection, window reuse, and cleanup
details. The examples below use placeholder paths; replace them with your own.

## 1. Install the CLI from source

Use this path for the current development version. The local Homebrew tap has no
published Focus formula yet; its release procedure is in [Distribution](DISTRIBUTION.md).
Desktop Names is currently built separately and is not installed by Focus's
Python setup script.

You need macOS, Python 3.11+, and the executables/apps required by your enabled
extensions. tmux is needed only for a resource selecting the `tmux` extension.
Building Desktop Names also requires existing Apple Command Line Tools. The
desktop integration currently supports one display and regular desktops.

```sh
cd /path/to/work-cli
./scripts/setup-dev.sh
source .venv/bin/activate
command -v focus
focus --version
focus doctor
```

The script creates `.venv`, installs this checkout in editable mode, and links
zsh completions if a writable Homebrew completion directory exists. Re-activate
the environment in each new shell, or invoke `/path/to/work-cli/.venv/bin/focus`
directly. Python source edits take effect immediately.

If a configured extension declares a missing Homebrew dependency,
`focus doctor --fix` shows its exact install command and asks first. For the
bundled tmux specification this is `brew install tmux`. It does not install
Homebrew or unused providers. Application
and project dependencies (for example PHP, Node, Composer, or npm dependencies)
must be set up separately. `doctor` checks executable/configuration availability;
it does not prove window placement, service health, or macOS permission access.

## 2. Set up Desktop Names

From the directory containing Desktop Names' `build.sh`:

```sh
cd /path/to/desktop-names
bash build.sh
python3 scripts/desktop-names.py setup
python3 scripts/desktop-names.py launch
```

On first setup, the launcher describes its changes and asks for confirmation
before creating a local signing certificate and key in your Keychain. It installs
the signed app at `~/Applications/Desktop Names.app`. macOS may ask for access to
the signing key. Subsequent setup runs reuse the same identity; keep its signing
metadata and Keychain entry between updates.

If you already have an appropriate signing identity and have not configured this
launcher yet, use its full fingerprint instead of creating another:

```sh
python3 scripts/desktop-names.py setup --identity 'FULL_CERTIFICATE_SHA1'
```

This adopts that identity without importing a certificate or changing trust.
The launcher refuses to replace an already configured identity with a different one.

Grant **Desktop Names** Accessibility access in System Settings → Privacy &
Security → Accessibility. Select the installed app in `~/Applications`, which is
the copy used for switching and window management. Naming appears in the Desktop
Names menu; macOS's native “Desktop 1” labels are unchanged.

List desktops to inspect names and UUIDs:

```sh
python3 scripts/desktop-names.py list
```

Listing alone does not test Accessibility. The first actual project launch tests
switching/window management. The menu app may be open, but Focus commands do not
require it to remain running or launch at login.

## 3. Register the project and launcher path

From your actual project directory, with the Focus virtual environment active:

```sh
cd /path/to/my-project
focus init my-project --desktop
```

The initializer asks for the workspace/folder, VS Code profile, Chrome profile,
URLs, context document, and optional startup command. It can create the context
document interactively. Use the original `.code-workspace` file; there is no need
to copy it. Initialization writes `[[apps]]` entries using the bundled `tmux`,
`vscode`, and `chrome` extension specifications. Remove or disable any resource
you do not want, including tmux. Initialization writes configuration only; it
does not launch the project.

For noninteractive setup, the root, workspace, and context document must already
exist. For example:

```sh
focus init my-project \
  --root /Users/me/src/my-project \
  --workspace MyProject.code-workspace \
  --context PROJECT.md \
  --vscode-profile "My Project" \
  --chrome-profile "Profile 2" \
  --url http://localhost:3000 \
  --desktop \
  --non-interactive
```

New installs use `~/.config/focus/config.toml` and
`~/.config/focus/projects/my-project.toml`. Existing Work installs continue using
`~/.config/work` when no `focus` directory exists. The initializer prints the
actual paths. With the virtual environment active, this also prints the selected
global file without changing it:

```sh
python -c 'from workspace_manager.paths import global_config_path; print(global_config_path())'
```

In that global file's existing `[commands]` table, set the absolute path to the
executable Desktop Names **launcher**, not the app bundle or app binary:

```toml
version = 1

[commands]
desktop_names = "/absolute/path/to/desktop-names/scripts/desktop-names.py"
```

Keep any other command overrides already present. Omitted executables are
discovered automatically. Command paths are single executable paths, not shell
commands; do not include `python3`, arguments, `$HOME`, or an unexpanded `~` in
the setting. The source launcher is executable and uses its own Python shebang.

For an existing project, use `focus edit my-project` and enable `[space]` as shown
below. Do not rerun `init --force` unless you intend to replace its configuration.

## 4. Configure services and application windows

Open the actual configuration with:

```sh
focus edit my-project
```

This example starts two services in separate Terminal windows, uses the original
VS Code workspace, and opens a Chrome window with the selected profile:

```toml
version = 1
root = "/Users/me/src/my-project"

[[apps]]
name = "services"
extension = "tmux"
launch_mode = "window"
reuse_existing = true

[apps.options]
session = "focus-my-project"
presentation = "windows"

[[apps.options.windows]]
name = "api"
root = "services/api"
command = "python -m api"

[[apps.options.windows]]
name = "web"
root = "apps/web"
command = "npm run dev"

[[apps]]
name = "editor"
extension = "vscode"
launch_mode = "window"
reuse_existing = true

[apps.options]
workspace = "MyProject.code-workspace"
profile = "My Project"
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
enabled = true
manager = "desktop-names"
target = "project"
```

- Use an absolute project `root`; other paths can be relative to it. Service
  directories and the selected workspace/context files must exist before launch.
- Give each service resource a distinct session name. Add one `[[apps.options.windows]]`
  entry per service, with a unique name. Omit `command` for an interactive shell.
  Commands run using your login shell in the entry's directory. Run services in
  the foreground so tmux owns their lifetime; independently daemonized processes
  and external containers are not tracked by `focus stop`.
- VS Code uses its profile name. Chrome uses a **profile directory**, such as
  `Default` or `Profile 2`, rather than the person's display name. You can read
  that directory from the end of **Profile Path** on Chrome's `chrome://version`
  page in the desired profile.
- Set an app entry's `enabled = false` to omit it, or remove the entry. Omit the
  editor's `context` option if you do not want a context file opened.
- The initializer defaults to `presentation = "current"` (one tmux attachment
  in the invoking terminal). Set `"windows"` explicitly for one Terminal window
  per service, or `"none"` to start services without attaching a Terminal client.
  The launch/reuse settings below control how those windows open.
- For a single project-wide command, use one `windows` entry. Legacy `[startup]`
  remains accepted through translation; do not configure the same server in both
  the new and legacy forms.
- The `apps` list is authoritative. Removing the `services` resource from this
  example leaves an editor/browser project that does not require tmux.

Keep `space.target = "project"` for a dedicated desktop. Optional `space.name`
changes its displayed name; optional `space.uuid` selects a particular existing
desktop, whose windows will then be included in project cleanup. Without either,
Focus creates a desktop when no saved assignment or unique matching name exists.

`space.target = "current"` instead renames the current desktop on restoration;
it has no dedicated desktop assignment and `stop` only stops its services. Set
`space.enabled = false` to skip desktop management. Direct bundle entries and
GUI extensions without a status probe still require Desktop Names for window
identification and verification. The bundled VS Code/Chrome extensions provide
status probes and can run without Desktop Names when desktop management is disabled.
Explicit app-level `match_title` or `match_document` rules require window
inventory instead of a probe, so they also require Desktop Names in that mode.

### Choose what opens and how, per app

Set these independently on each `[[apps]]` resource, including `extension = "tmux"`
for Terminal service views:

| Setting | Choices | Default |
| --- | --- | --- |
| `launch_mode` | `"window"`: a new window in the normal app; `"instance"`: a separate app instance | `"window"` |
| `reuse_existing` | `true`: reuse an identifiable project window and move it to the project desktop; `false`: request a new window and leave existing ones in place | `true` |

Reuse is checked **first**. `launch_mode` controls what happens when a new window
is needed; it does not convert the profile or process of a reused window. For
example, use normal VS Code window reuse but a separate Chrome instance:

```toml
[[apps]]
name = "editor"
extension = "vscode"
launch_mode = "window"
reuse_existing = true

[apps.options]
workspace = "MyProject.code-workspace"

[[apps]]
name = "browser"
extension = "chrome"
launch_mode = "instance"
reuse_existing = false

[apps.options]
profile = "Profile 2"
urls = ["http://localhost:3000"]

[[apps]]
name = "services"
extension = "tmux"
launch_mode = "window"
reuse_existing = false

[apps.options]
session = "focus-my-project"
presentation = "windows"
```

Edit the existing resources rather than adding a duplicate editor or browser.
Each `[apps.options]` table belongs to the preceding `[[apps]]` entry. The options
specify **what** opens; the selected extension specification defines their schema
and the app's launch and detection behavior.

With reuse disabled, an initial launch or `--force-gui` requests new windows even
when matching or previously recorded ones are open. Normal focus on an existing
desktop still only switches. If a newly opened window's move failed, a retry
finishes moving that same window to avoid duplicates. If an app ignores a new-window
request and focuses an old window, Focus reports that instead of moving the old one.

Instance mode has application-specific behavior:

- **VS Code and Chrome:** use persistent user-data directories for each project
  resource under `extensions/<extension>/<project>/<resource>` in the selected
  state directory. Translated legacy sections retain `vscode/<project>` and
  `chrome/<project>`.
  They have separate settings/sign-in state; normal profile data is not copied.
  A later launch can use that project's already-running isolated instance.
  See the official [VS Code CLI](https://code.visualstudio.com/docs/configure/command-line)
  and [Chromium profile documentation](https://www.chromium.org/developers/creating-and-using-profiles/).
- **Terminal:** starts a separate Terminal process per new service client and
  opens an owned `.command` launcher. Terminal may also restore its saved windows;
  use `"window"` if you want to avoid that. Focus only places the identifiable new
  service window and does not discard Terminal's saved state. Terminal clients
  still share the project's original service processes through independent tmux views.

The older `[vscode] separate_instance = true` remains accepted as an alias for
`launch_mode = "instance"`. Prefer the new setting; conflicting values are rejected.
No settings change a running desktop until you explicitly repair/restart it or
launch it after stopping. The new modes still need live GUI acceptance tests.

### Use an application extension

`name` is your resource label, such as `editor` or `browser`. `extension` selects
a specification. The `tmux`, `vscode`, and `chrome` specifications ship with
Focus; they are files interpreted by the same engine that loads your own
extensions. They do not reserve special resource names.

The bundled options are:

| Extension | Option | Meaning |
| --- | --- | --- |
| `tmux` | `session` | Provider session name; use a distinct name for each service resource |
| `tmux` | `presentation` | `current` attaches the invoking terminal; `windows` opens separate Terminal views; `none` starts services only |
| `tmux` | `windows` | Service entries with `name`, optional `root` and `command` |
| `tmux` | `startup_command`, `startup_window`, `startup_check` | Optional project-rooted startup command, window name, and shell health check; do not duplicate the same service in `windows` |
| `vscode` | `workspace` | Workspace file or folder, relative to project `root` or absolute |
| `vscode` | `profile` | Optional VS Code profile name |
| `vscode` | `context` | Optional existing document to open with the workspace |
| `chrome` | `profile` | Chrome profile directory, for example `Default` or `Profile 2` |
| `chrome` | `urls` | List of URLs to open in a new window |
| `chrome` | `reopen_on_restore` | Without desktop management, reopen when the status probe reports absence; defaults to `true` |

An extension supplies its allowed options/defaults and provider behavior. GUI
specifications declare bundle identity, launch commands, matching, and status
probes; session specifications declare service/view operations and ownership.
Your project supplies option values and independently chooses window reuse and
launch mode. See [Extension specifications](EXTENSIONS.md) to inspect the bundled
files or create a local provider.

Existing `[vscode]`, `[chrome]`, `[context]`, `[tmux]`, and `[startup]` configurations
remain accepted. Focus translates them into extension resources. Once a project has an `apps`
list, that list chooses its apps; omitted legacy sections no longer add default
VS Code, Chrome, or tmux resources. Explicit legacy sections can still add
resources, but enabling both a legacy section and its extension is rejected. Old files without
an `apps` key keep their existing defaults. `focus init` writes the new form.

### Add other applications

For an app without an extension, use a direct `[[apps]]` entry. You choose the
resource name, installed app, documents or URLs, launch method, and rules for
recognizing an existing project window. No application-specific code in Focus is
needed. Move shared behavior into an extension when you want to reuse it across
projects.

For example, add a document that already exists in the project root:

```toml
[[apps]]
name = "notes"
bundle_id = "com.apple.TextEdit"
enabled = true
open = ["PROJECT.md"]
launch_mode = "window"
reuse_existing = true
match_title = "PROJECT.md"
```

Check the app's actual exposed title before using this rule; adjust it if the app
includes a suffix. For an app that exposes its document path, a stronger rule is
`match_document = "/Users/me/src/my-project/PROJECT.md"`. When both rules are set,
both must match the same window. Wildcard-only rules such as `match_title = "*"`
are rejected. Other broad rules can still match unrelated work; multiple matches
cause an error.

| Field | Meaning |
| --- | --- |
| `name` | Required unique resource label within this project's app list; start with a letter or digit and use only letters, digits, `_`, and `-` |
| `bundle_id` | Required for a direct entry: installed macOS app bundle identifier, such as `com.apple.TextEdit`; extension entries obtain it from the specification |
| `enabled` | Whether to restore this resource; defaults to `true` |
| `launch_mode` | `"window"` (default) or `"instance"`; used when a new window is needed |
| `reuse_existing` | Reuse a saved or uniquely matching project window before launching; defaults to `true` |
| `open` | Optional document paths relative to project `root` (or absolute paths), or app-supported URLs |
| `window_command` | Optional complete executable-and-arguments array used in window mode instead of macOS `open` |
| `instance_command` | Optional complete executable-and-arguments array used in instance mode instead of macOS `open` |
| `match_title` | Optional case-sensitive glob against the app's exposed window title |
| `match_document` | Optional case-sensitive glob against the app's exposed document path or URI |

Find a bundle identifier from the installed app's metadata, substituting its
actual path:

```sh
defaults read '/Applications/Your App.app/Contents/Info' CFBundleIdentifier
```

System apps may live in `/System/Applications`. After setting up Desktop Names,
inspect the app's window titles and document values with the configured launcher:

```sh
/absolute/path/to/desktop-names/scripts/desktop-names.py windows com.apple.TextEdit
```

Matching rules support `*`, `?`, and character groups such as `[abc]`. They are
case-sensitive, with no shell or placeholder expansion. Focus decodes exposed
`file:` URLs into absolute paths before applying `match_document`; other document
URIs are matched as URIs. Absolute document-path patterns and decoded file paths
are normalized through the filesystem, including symlinks, so equivalent paths
such as `/var` and `/private/var` agree. Use an absolute path in a document rule,
even when the corresponding `open` path is relative. Some apps do not expose a
document value, so inspect the inventory before choosing this rule.

With no matching rules, Focus can reuse only a window it previously recorded for
this resource. Otherwise it launches once and records the sole new window from
that app. With matching rules, the new window must also satisfy those rules.
Saved identities are tied to the project root, configured target, matching rules,
and launch mode; changing those settings makes Focus identify the resource again.
Focus never adopts an arbitrary existing window based only on its app bundle.
If multiple windows qualify, or the app merely activates an old window, Focus
reports an error rather than moving an unrelated window.

The default launch uses `/usr/bin/open -b BUNDLE_ID` with the `open` documents or
URLs. Instance mode adds `-n`. These requests follow the application's own macOS
open behavior: macOS has no universal command that makes every app create a new
window, and some apps ignore or reject separate instances. Generic instance mode
does not create a separate user-data directory or profile automatically.

If the app provides a command for the behavior you need, configure the complete
argument list. For example, this is the shape for an editor whose CLI supports
`--new-window`; replace the executable and flags with that app's documented CLI:

```toml
[[apps]]
name = "editor"
bundle_id = "com.example.editor"
launch_mode = "window"
reuse_existing = true
window_command = ["/absolute/path/to/editor-cli", "--new-window", "/Users/me/src/my-project"]
match_title = "*my-project*"
```

Commands run with the project root as their working directory. Each array item
is passed literally as one argument; Focus does not run a shell or expand `~`,
environment variables, or placeholders. A relative executable path containing
`/`, such as `./scripts/open-editor`, resolves against the project root. A bare
command name is looked up through `PATH`. `window_command` and `instance_command`
are independent overrides for their respective modes; setting one does not
configure the other. If the selected mode has a custom command, omit `open` and
include every document, URL, profile, and isolation flag in that command instead.
Focus rejects a selected custom command combined with `open` to avoid silently
ignoring either setting.

The app list chooses which applications run. A project containing only the
`notes` entry does not implicitly launch VS Code, Chrome, or a tmux session.
Remove an older explicit legacy section when replacing it with a direct entry for the same app.
Use `extension = "vscode"`, `extension = "chrome"`, or `extension = "tmux"` to
select bundled behavior. No provider is required merely because its specification
is bundled with Focus.

Direct entries require Desktop Names and its Accessibility permission even with
`space.enabled = false`. In that mode Focus identifies and verifies windows but
does not move them, and `focus stop` still stops only the project's services.
On an existing dedicated project desktop, normal refocus still only switches;
use `--force-gui` to apply new app entries. Additional app launch behavior and
the extension refactor have not yet been live-tested against GUI apps.

`focus status <project>` inspects apps without launching or moving anything; its
JSON output includes an `apps` map keyed by resource name. A direct entry reports
an observed window. An extension can declare a status probe, whose success means
what that specification defines. Neither signal certifies desktop placement,
application content, or network health. The text app table labels these signals
`running`, `stopped`, or `disabled`; `running` alone does not prove a window is visible.
A session resource reports running when its provider session exists and its
configured startup is ready: `startup_check` must exit 0 when supplied; otherwise
the configured startup unit must exist and be alive. Without a startup command,
session existence is sufficient. Other service health and visible placement are
separate checks.

The aggregate `booted` value is true when at least one resource is enabled and
all enabled resources report ready. It does not depend on a mandatory tmux
session. Legacy `TMUX`/`STARTUP` columns keep session existence and startup
readiness separate.

## 5. Allow separate Terminal windows and launch

Open Terminal first when using `presentation = "windows"` with the default
`launch_mode = "window"`. On first launch,
macOS may ask whether the app running Focus can control Terminal. Allow that
relationship in System Settings → Privacy & Security → Automation.

| Permission | Who needs it | What it enables |
| --- | --- | --- |
| Accessibility | Installed Desktop Names app | Switching, creating/removing desktops, inspecting/moving/closing windows |
| Automation → Terminal | The app from which Focus runs | Creating and titling windows in Terminal's `"window"` mode |

These are separate permissions. Running Focus yourself does not require granting
Codex access to Terminal. A test launched from Codex can require Codex → Terminal
permission. Switching to an existing project desktop bypasses Terminal setup.
Terminal's opt-in `"instance"` mode uses Launch Services instead of AppleScript;
it does not request Terminal Automation. Desktop Names still needs Accessibility
when managing those windows on a project desktop.

```sh
focus doctor
focus my-project --dry-run
focus my-project
```

For a newly created project desktop, check that each configured Terminal window,
the workspace, and Chrome are visible there. A tmux session or `status` result
alone does not prove placement. Run `focus my-project` again: it should just switch
to the existing desktop without opening more windows.

## Apps that follow every workspace switch

Put shared apps in the global configuration (`~/.config/focus/config.toml`, or
`~/.config/work/config.toml` for an existing Work installation):

```toml
[[follow_apps]]
name = "slack"
bundle_id = "com.tinyspeck.slackmacgap"
match_title = "* - Slack [[]Main[]]*"

[[follow_apps]]
name = "chatgpt"
bundle_id = "com.openai.codex"

[[follow_apps]]
name = "viber"
bundle_id = "com.viber.osx"
```

Use the installed application's actual bundle ID. Every `focus <project>` or
`work <project>` switch moves all matching open windows, including when the
project desktop already exists. Closed apps stay closed. `match_title` is an
optional shell-style pattern; omit it to move all of that app's windows. Set
`enabled = false` to disable an entry. Windows assigned to multiple desktops
are left alone. This requires Desktop Names and a project with `space.enabled`.

These windows are shared, not owned by the project. Stopping a dedicated
workspace moves its matching shared windows to another regular desktop before
closing project windows. If none is available, stop reports an error before
cleanup. Remove redundant project `[[apps]]` entries for these shared windows
if you no longer want the project to launch them when closed.

## Daily commands and recovery

| Command | Use it when |
| --- | --- |
| `focus my-project` | Start a missing desktop or switch to an existing one |
| `focus my-project --force-gui` | A desktop exists but a configured app/service window is missing |
| `focus my-project --no-attach` | Start services/apps without opening or attaching Terminal clients |
| `focus restart my-project` | Restart services and reconcile resources on the retained desktop |
| `focus stop my-project` | Close the dedicated desktop's windows, stop services, remove the empty desktop |
| `focus stop my-project --dry-run` | Preview cleanup without closing anything |
| `focus status my-project --json` | Inspect configured resource status signals |
| `focus list` | List configured projects |

`stop` closes windows exclusively on the project desktop, including windows you
opened there manually. Other desktops' windows and windows assigned to all
desktops stay open. Save prompts pause cleanup; resolve them in the application
and retry the same stop command. Cleanup is sequential, so some windows may have
closed before a later one asks to save. Stopping does not delete project files or
configuration; the next focus creates a new project desktop if needed.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Repeated focus only switches, but windows are missing | Use `focus my-project --force-gui`; normal refocus intentionally does not repair resources. |
| `Desktop Names payload has changed` | Quit Desktop Names from its menu, then run the **configured launcher's** `setup` after rebuilding. Do not execute a stale/ad-hoc copy instead. |
| Signing appears to wait indefinitely | Check for a macOS Keychain prompt and authorize use of the existing Desktop Names signing key locally. Keep the current identity and signing metadata. |
| `Desktop Names needs Accessibility` | Enable the installed `~/Applications/Desktop Names.app` in Accessibility and retry. Permission for another build is not sufficient. |
| Terminal Automation error `-1743` | Allow the calling app → Terminal in Automation, or use `--no-attach` if you do not need Terminal clients. |
| Terminal error `-600` | Open Terminal, then retry. |
| A service exits immediately | Review its retained tmux output; fix its command/dependencies and use `focus restart my-project`. Repair does not restart dead configured service windows. |
| Multiple windows/desktops match | Resolve duplicate names/workspaces or select a known desktop UUID. Focus does not choose an arbitrary match. |
| An app reuses a window whose title was initially unreadable | With reuse enabled, Focus rechecks project matching after launch as accessibility metadata becomes available. Update Desktop Names to include focused/main window metadata; unrelated or ambiguous windows remain untouched. |
| A move is unsupported or not confirmed | Keep the reported window and desktop; inspect placement before retrying. This is a native macOS compatibility failure, not proof that another window must be opened. |
| Save/close prompt blocks stop | Resolve the prompt in that app and rerun `focus stop my-project`. |
| A window inventory error blocks stop | Resolve the unresponsive app and retry. Windowless processes are skipped only after a separate WindowServer check confirms they own no windows. |
| Old projects disappear from `focus list` | Check whether both `focus` and `work` config directories exist. Focus takes precedence; they are not merged. |
| `space.manager` rejects `yabai` | Use `desktop-names`, remove the old `timeout_seconds`, and replace `commands.yabai` with `commands.desktop_names`. |

Do not delete desktop/window assignment files to recover a partial launch. Focus
uses them to reuse resources, and automatically replaces a desktop that was deleted.

## Updating this development installation

For Focus Python changes, update the checkout and run `./scripts/check.sh`.
Re-run `./scripts/setup-dev.sh` after packaging metadata or entry points change.
Check `command -v focus` if a different installation appears to be running.

For native Desktop Names changes, quit it from its menu, then rebuild and deploy:

```sh
cd /path/to/desktop-names
bash build.sh
python3 scripts/desktop-names.py setup
python3 scripts/desktop-names.py launch
```

Setup reuses the existing signing identity and stages/verifies the replacement.
Do not delete signing metadata or rotate certificates to work around an update
failure. A signed build can also use `DESKTOP_NAMES_SIGN_IDENTITY` with the
existing certificate name/fingerprint. Names and preferences are retained.

## This development workspace

The current local checkouts and Wellprept project are:

| Item | Path |
| --- | --- |
| Maintained Focus CLI | `/Users/aialec/Documents/workspace/wks_aitech/work-cli` |
| Desktop Names source | `/Users/aialec/Documents/ChatGPT/workspace_idea_001/desktop-names` |
| Wellprept root | `/Users/aialec/Documents/workspace/wks_Wellprept/foundation symlinks` |
| Existing project config | `/Users/aialec/.config/work/projects/wellprept-foundation.toml` |

Wellprept uses `Foundation.code-workspace`, `AGENT.MD`, VS Code profile
`Wellprept`, Chrome directory `Profile 2`, and the existing
`work-wellprept-foundation` tmux session name. Its four service entries are `api`,
`profile`, `dashboard`, and `admin`; the frontend commands use the project's
`run-web-app` wrapper. Edit the existing configuration rather than initializing it
again. Normal use is `focus wellprept-foundation` and
`focus stop wellprept-foundation`.

As of 2026-10-04, the live stop test passed. The updated window-move path and
separate Terminal creation still need their own live launch test. See
[verification status](HOW_IT_WORKS.md#verification-status).
