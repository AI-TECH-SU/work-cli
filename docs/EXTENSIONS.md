# Extension specifications

An extension is a TOML specification that declares how a provider behaves. The
project selects it and supplies its options. Focus interprets bundled and local
specifications through the same generic capabilities: `application` for GUI
windows and `session` for service processes and terminal views. VS Code, Chrome,
and tmux are bundled specifications, not special core resource types.

For example, this project uses three extensions:

```toml
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
root = "api"
command = "npm run dev"

[[apps]]
name = "editor"
extension = "vscode"
launch_mode = "window"
reuse_existing = true

[apps.options]
workspace = "Foundation.code-workspace"
profile = "Wellprept"
context = "AGENT.MD"

[[apps]]
name = "browser"
extension = "chrome"

[apps.options]
profile = "Profile 2"
urls = ["http://localhost:3000"]
```

`services`, `editor`, and `browser` are user-chosen resource names. `tmux`,
`vscode`, and `chrome` select specification files. The keys in `[apps.options]`
and its child tables are inputs declared by those specifications. A different extension can declare different
inputs without a Focus code change.

The resource controls `enabled`, `launch_mode`, and `reuse_existing`; their
defaults are `true`, `"window"`, and `true`. Reuse takes priority. A launch mode
only applies when a window needs to be opened. Resource-level `match_title` and
`match_document` rules can further restrict the extension's matching behavior.
Those explicit rules require Desktop Names window inventory even when desktop
management is disabled; a status probe cannot satisfy them.

The core owns project desktops, verified window movement, saved window identity,
locking, and cleanup. Capabilities provide reusable algorithms; specifications
supply provider-specific commands, formats, ownership rules, input schemas, and
dependency metadata. Selecting an extension does not dispatch to a Python handler
for that provider. A project without a session resource neither creates a tmux
session nor requires tmux installed.

## Bundled and local specifications

The bundled specifications are ordinary package files:

- [`tmux.toml`](../src/workspace_manager/extension_specs/tmux.toml) — session capability
- [`vscode.toml`](../src/workspace_manager/extension_specs/vscode.toml)
- [`chrome.toml`](../src/workspace_manager/extension_specs/chrome.toml)

Add your own specification at
`~/.config/focus/extensions/<extension-id>.toml`, then reference its ID in a
project's `extension` field. The ID must match the filename, start with a letter
or digit, and contain only letters, digits, `_`, or `-`.

Focus uses the same config-directory selection as project files: an existing
`~/.config/work` is reused when `~/.config/focus` does not exist, and
`XDG_CONFIG_HOME` can replace `~/.config`. Thus an existing Work installation may
use `~/.config/work/extensions/`. A local file cannot silently replace a bundled
ID; a collision is an error. Give a customized specification a different ID.

Specifications are local files. Focus does not fetch or install extensions or
their target applications. Their declared launch/status commands run as the
current user. Inspect the commands before using a specification from elsewhere.

## A complete local example

Save this as `extensions/project-textedit.toml` under the selected configuration
directory:

```toml
version = 1
id = "project-textedit"
bundle_id = "com.apple.TextEdit"
require_match = true

[command]
name = "/usr/bin/open"

[inputs.document]
type = "path"
required = true
must_exist = true
path_kind = "file"

[launch.window]
argv = ["{command}", "-b", "com.apple.TextEdit", "{document}"]

[launch.instance]
argv = ["{command}", "-n", "-b", "com.apple.TextEdit", "{document}"]

[match]
kind = "glob"
document = "{document}"
```

Select it in a project:

```toml
[[apps]]
name = "notes"
extension = "project-textedit"

[apps.options]
document = "PROJECT.md"
```

The document must exist. This example requires Desktop Names, including with
desktop management disabled, because it has no status probe. It also requires
TextEdit to expose the document path in its window inventory. Inspect that
inventory before using the matcher:

```sh
/absolute/path/to/desktop-names/scripts/desktop-names.py windows com.apple.TextEdit
```

The macOS `open` request follows the target application's behavior; it cannot
guarantee a new window or independent app data. Focus verifies the resulting
window and reports a failure if the app only activates an old one or matching
is ambiguous. This example has not been live-tested against TextEdit.

## Application specification reference

A GUI specification uses `kind = "application"` (the default). Unknown fields,
unknown project options, invalid types, and unsupported template tokens are
rejected. Both launch modes must be declared. Session specifications use the
separate contract below.

### Identity and lifecycle fields

| Field | Meaning |
| --- | --- |
| `version` | Required; `1` |
| `id` | Required; matches the specification filename |
| `kind` | `application` (default) or `session`; selects a reusable capability, not a provider handler |
| `bundle_id` | Required macOS bundle identity used for window inventory |
| `detached` | Whether to start the launch command without waiting for its process to exit; default `false` |
| `require_match` | Require a newly appeared window to satisfy the specification matcher; default `false` |
| `adopt_single_on_desktop` | Allow adoption of the sole app window already on the target project desktop when no stronger match exists; default `false` |
| `restore_on_fresh_boot` | Without desktop management, request launch on a fresh service-session boot, or the first launch of an app-only resource configuration in this macOS boot, even if its probe reports presence; default `false` |
| `reopen_on_restore` | Without desktop management, whether to launch when the status probe reports absence during restoration; default `true` |
| `reopen_on_restore_input` | Name of a declared boolean input that supplies the preceding policy per project |

Use the adoption option only when the project desktop itself is an acceptable
ownership signal. It is enabled by the bundled Chrome specification, but disabled
when multiple active resources use that bundle or explicit resource matchers are
configured. Default matching does not adopt windows merely because they share an
app bundle. Resources cannot claim another active resource's saved window.

### Executable discovery

```toml
[command]
name = "my-editor"
candidates = ["/Applications/My Editor.app/Contents/MacOS/my-editor"]
```

`name` is looked up through `PATH`; an absolute executable path is also accepted.
If it is unavailable, existing files in `candidates` are checked in order. A
specification may instead set `key` to a known global command setting, such as
`code` or `chrome`, to honor the user's existing `[commands]` override. The
bundled specifications use those keys. `key` or `name` is required.

The resolved executable is available as `{command}` and its basename as
`{command_name}`. Launch and status arrays can also name another executable
directly, as the bundled Chrome status probe does with `/bin/ps`.

### Input schema

Each `[inputs.<name>]` table describes one permitted `[apps.options]` value:

| Field | Meaning |
| --- | --- |
| `type` | Required: `string`, `path`, `strings`, `paths`, or `boolean` |
| `default` | Optional value of that type; string defaults may contain `{root}` |
| `required` | Reject a missing input without a default; defaults to `false` |
| `must_exist` | Validate a `path` or every `paths` item before launch; defaults to `false` |
| `path_kind` | For path/paths with `must_exist = true`, require `file`, `directory`, or `any` (default) |
| `schemes` | For string/strings URL inputs, a nonempty allowed-scheme list such as `["http", "https"]` |

Path inputs expand `~`, resolve relative paths against the project root, and
normalize filesystem paths. String inputs are literal values. Optional inputs
without defaults are absent; use conditional argument groups before referring
to them in a command. List inputs use `strings` or `paths` and expand with
`spread`, not a scalar placeholder. Input names cannot use the reserved template
context names below.

The bundled VS Code options are `workspace` (defaults to project root), optional
`profile`, and optional `context`. Workspace and context paths must exist. The
Chrome options are `profile` (defaults to `"Default"`), `urls` (defaults to an
empty list; HTTP/HTTPS only), and `reopen_on_restore` (defaults to `true`).

### Launch arrays and templates

`[launch.window]` and `[launch.instance]` each require an `argv` array. Entries
have three forms:

```toml
argv = [
  "{command}",
  "--new-window",
  { if = "profile", argv = ["--profile", "{profile}"] },
  { spread = "documents" },
]
```

- A string produces exactly one argument after token substitution. Spaces inside
  an option value stay in that argument.
- `{ if = "input", argv = [...] }` includes its arguments when the input has a
  nonempty/true value.
- `{ spread = "input" }` produces one argument per item of a `strings` or `paths`
  input.

Supported tokens are declared scalar inputs and these reserved values:

| Token | Value |
| --- | --- |
| `{root}` | Absolute project root |
| `{project}` | Project name |
| `{instance_dir}` | Persistent state directory dedicated to this extension resource |
| `{command}` | Resolved executable from `[command]` |
| `{command_name}` | Basename of that executable |

Focus does not invoke a shell or evaluate expressions. There is no environment
variable substitution, attribute/index access, conversion, or format syntax.
Use doubled braces (`{{` and `}}`) for literal braces in a template. Values
supplied by the project are not themselves templates. Commands run with the
project root as their working directory; relative executable paths containing
`/` resolve against that root.

`{instance_dir}` is normally
`<state-home>/extensions/<extension>/<project>/<resource>`. Translating a legacy
VS Code or Chrome section preserves its existing `<state-home>/vscode/<project>`
or `<state-home>/chrome/<project>` path. The engine supplies the directory name;
the command must use it through the application's own isolation flag. Merely
choosing instance mode does not guarantee separate settings or sign-in data.

### Window matching

The optional `[match]` table declares one matcher primitive:

| `kind` | Fields and behavior |
| --- | --- |
| `none` | No semantic matcher; reuse only a saved owned window unless explicit resource rules are configured |
| `glob` | `title` and/or `document` templates rendered as case-sensitive glob patterns; both must match when both are set |
| `document_values` | `input` names a `strings` input; the window's exposed document must equal one of its values; an empty list supplies no semantic matcher |
| `workspace` | `input` names a `path` input; match the workspace/folder title and an exposed document within its configured folders |

For `workspace`, also declare `file_suffix`, `title_suffix`, `title_separator`,
`folders_key`, and `folder_path_key`. Optional `jsonc = true` allows comments and
trailing commas in the workspace file. The bundled VS Code specification is the
complete example of this format.

Glob patterns support `*`, `?`, and character groups. Wildcard-only patterns are
rejected. File document URLs are decoded and normalized before glob matching;
absolute document patterns are normalized too. Other URI values remain URIs.
An extension's usable matcher and any project-level title/document rules must
both pass. Saved window bindings include the specification and resolved options, so
changes cause the resource to be identified again.

On a managed desktop, the common runtime verifies window identity and movement.
It rejects ambiguous candidates; disabling reuse requires a newly appeared
window. This behavior is the same for bundled and local extensions.

### Read-only status probes

An extension can declare `[status.window]` and/or `[status.instance]` for the
corresponding mode:

```toml
[status.window]
argv = ["{command}", "--status"]
any_contains = ["{root}"]
ignore_case = true
```

These commands must be read-only: inspection and restoration may run them. Each
requires an `argv` array and exactly one nonempty predicate array:

- `any_contains`: true if any rendered string occurs in combined stdout/stderr.
- `line_all`: true if every rendered string occurs in the same output line.

Nonzero command exit means false. Matching is case-sensitive unless
`ignore_case = true`. A mode without a probe falls back to Desktop Names window
inventory. Explicit resource title/document rules also use inventory instead of
the probe. Otherwise, with desktop management disabled, a probe permits reconciliation
without Desktop Names; with desktop management enabled, restoration still uses
verified window inventory/movement. Probe presence is not proof of a visible
window, healthy content, or correct desktop placement.

For app-only projects, `restore_on_fresh_boot = true` uses a saved first-launch
marker keyed by resource configuration identity and macOS boot. This gives a resource its initial
launch after each reboot without requiring a service session. Subsequent reconciliation honors
`reopen_on_restore = false`; an explicit repair still requests launch. The marker
is written only after a successful launch request, never in dry-run. Unreadable
launch state stops restoration to avoid duplicate requests. Dedicated-desktop
cleanup clears the marker, including when the assigned desktop is already gone;
service-only stop retains it because GUI apps remain open. The marker is launch
bookkeeping, not a running-process or health signal.

## Session specification reference

`kind = "session"` selects the generic service-session capability. The bundled
[`tmux.toml`](../src/workspace_manager/extension_specs/tmux.toml) is its complete
reference. The project uses the same `[[apps]]`, `enabled`, `launch_mode`,
`reuse_existing`, and `[apps.options]` structure as a GUI extension. The capability
manages service lifetime and views; provider command names and arguments live in
the specification.

A session specification requires `version`, `id`, `kind`, `bundle_id`, `command`,
`inputs`, `session`, and `operations`. `bundle_id` identifies the app used to
present service views. Session specifications use operation arrays instead of
GUI `launch`, `match`, and `status` tables.

### Session inputs and mappings

Input schemas use the scalar/path/list types above, plus `records`. A records
input declares `[inputs.<name>.fields.<field>]` schemas and accepts an array of
project tables such as `[[apps.options.windows]]`. Nested records are unsupported.
A string input can declare `choices = [...]` or a regular-expression `pattern`;
values must match the entire pattern. Unknown fields and duplicate service names
are rejected. Path fields resolve against the project root.

The `[session]` table names the inputs serving each role; these names are chosen
by the specification, not hardcoded project keywords:

| Mapping | Input type and purpose |
| --- | --- |
| `identity_input` | `string`; provider session identity; omitted value uses `focus-<project>-<resource>` |
| `units_input` | `records`; fields must be `name` (required string), `root` (required/defaulted path), and optional `command` (string) |
| `presentation_input` | `string`; resolved value is `current`, `windows`, or `none` |
| `startup_command_input` | Optional `string`; additional project-rooted foreground command |
| `startup_unit_input` | `string`; window name for that startup command |
| `startup_check_input` | Optional `string`; project-rooted shell check, successful on exit 0 |

`default_unit` names the initial interactive service when the units list is empty.
`presentation = "none"` starts services without a client. `"current"` attaches the
invoking terminal; `"windows"` requires the view declarations below. At most one
enabled resource can attach the invoking terminal. Give each enabled session
resource a distinct provider session identity. The base session name identifies
the project-owned resource, so do not point it at an unrelated session.

The bundled tmux inputs are `session`, `presentation`, `windows`,
`startup_command`, `startup_window`, and `startup_check`. Defaults provide an
interactive `shell` window and current-terminal presentation. Run service
commands in the foreground: Focus does not track independently daemonized
processes or external containers.

The resource status is ready when `exists` succeeds and its configured startup
is ready. If `startup_check` is set, its shell command must exit 0. Otherwise,
a configured startup unit must appear alive in the unit inventory. No startup
command means no additional startup requirement. This readiness feeds the
resource's `apps` status and aggregate `booted` value; it does not inspect every
service's application health.

### Operations and output formats

Each `[operations.<name>]` table contains an `argv` array with the same literal,
conditional, and spread forms as GUI launch arrays. Commands execute directly;
provider formats such as `#{window_name}` must escape braces as `#{{window_name}}`
in the specification. Values supplied by the project are not reinterpreted as
templates. Service `command` and startup/check options are the deliberate shell
command boundary.

| Required operation | Contract |
| --- | --- |
| `exists` | Read-only; exit 0 when the base session exists |
| `create` | Create the base session with its first unit in `{unit_root}` |
| `list_units` | Read-only; emit one row per unit using the declared inventory schema |
| `create_unit` | Create `{unit}` in `{unit_root}` |
| `retain_unit` | Keep a terminated unit's output available for diagnosis |
| `start_unit` | Start `{unit_command}` or `{shell_command}` in the existing unit |
| `stop` | Stop the base session and its foreground service processes |
| `attach` | Attach the invoking terminal to the base session |

`[session.inventory]` declares `separator`, `columns = ["name", "dead"]` (either
order), and `dead_value`. Other values in the dead column mean running. Malformed
rows are errors. The specification supplies any provider-specific format flags
inside `list_units.argv`.

`[session.attach]` can declare `switch_env`, an environment variable indicating
that the invoking terminal is already inside the provider. That requires a
`switch` operation, used instead of `attach` when the variable is set. Its
`clear_env` list names variables to remove from new Terminal clients. The bundled
tmux specification supplies `TMUX`; the capability does not assume that name.

Session templates add these context tokens to the shared template context:

| Token | Value |
| --- | --- |
| `{app}` | Resource name |
| `{title_prefix}` | `<project> / <resource>`; legacy translated service resources retain `<project>` |
| `{session}` | Resolved provider session identity |
| `{unit}`, `{unit_root}` | Service name and absolute working directory |
| `{unit_command}` | User's foreground command |
| `{shell_command}` | Foreground command prefixed with `exec` |
| `{shell}` | User's configured shell, falling back to `/bin/zsh` |
| `{view}`, `{title}` | Resolved client-view identity and display title |
| `{mode}` | Resource `launch_mode` |
| `{digest}` | Stable service-name digest, available to the view identity template |

### Optional independent client views

A `[session.views]` declaration enables `presentation = "windows"`. It provides:

- `identity` and `title` templates for each service view;
- `inactive_values`, the output strings indicating no client is attached;
- `modes`, the recorded client modes that may be reused;
- `[session.views.inventory]` with `separator` and `columns = ["name", "owner"]`.

It also requires these operation arrays:

| Operations | Purpose |
| --- | --- |
| `view_exists`, `view_owner`, `view_attached`, `view_mode` | Read-only identity, ownership, attachment, and mode inspection |
| `view_create`, `view_set_owner`, `view_select` | Create an independent view, mark its parent session, select its service |
| `view_detach`, `view_attach`, `view_set_mode` | Control the view's client and record its presentation mode |
| `view_list`, `view_stop` | Inventory and remove only views whose owner equals the base session |
| `view_set_title`, `view_enable_title` | Supply the identifiable Terminal window title |

Ownership keys, provider flags, output formats, and title-control commands belong
in these arrays. The runtime compares the returned owner with `{session}` and
refuses to reuse an unrelated view. `reuse_existing = false` creates an additional
owned view of the same services; it does not duplicate the service processes.
The common Terminal presenter opens and verifies the configured view. It supports
the existing window/instance policies and permission checks; changing the session
provider does not remove those macOS requirements.

### Dependency metadata

Session specifications may declare:

```toml
[install]
manager = "homebrew"
package = "tmux"
```

This is repair metadata for the provider executable, not an install hook.
`focus doctor` checks enabled resources only. `focus doctor --fix` prints the
exact install command and asks before running it; `--yes` is explicit approval
for noninteractive repair. Normal focus and specification loading never install
packages. An app-only project does not require an installed tmux executable.

### Another session provider

A local provider can use different input names and commands. For example,
`extensions/team-session.toml` below defines the contract of an existing,
user-supplied `team-session` executable. This is an authoring example, not a
bundled executable or a claim that a package with this interface is installed:

```toml
version = 1
id = "team-session"
kind = "session"
bundle_id = "com.apple.Terminal"

[command]
name = "team-session"

[inputs.group]
type = "string"
required = true

[inputs.display]
type = "string"
default = "none"
choices = ["current", "none"]

[inputs.jobs]
type = "records"
default = []
[inputs.jobs.fields.name]
type = "string"
required = true
[inputs.jobs.fields.root]
type = "path"
default = "{root}"
[inputs.jobs.fields.command]
type = "string"

[inputs.start]
type = "string"
[inputs.start_name]
type = "string"
default = "dev"
[inputs.check]
type = "string"

[session]
identity_input = "group"
units_input = "jobs"
presentation_input = "display"
startup_command_input = "start"
startup_unit_input = "start_name"
startup_check_input = "check"
default_unit = "shell"

[session.inventory]
separator = "|"
columns = ["name", "dead"]
dead_value = "stopped"

[operations.exists]
argv = ["{command}", "exists", "{session}"]
[operations.create]
argv = ["{command}", "create", "{session}", "{unit}", "{unit_root}"]
[operations.list_units]
argv = ["{command}", "list-jobs", "{session}", "--format", "name|state"]
[operations.create_unit]
argv = ["{command}", "add-job", "{session}", "{unit}", "{unit_root}"]
[operations.retain_unit]
argv = ["{command}", "retain-output", "{session}", "{unit}"]
[operations.start_unit]
argv = ["{command}", "run-job", "{session}", "{unit}", "{shell_command}"]
[operations.stop]
argv = ["{command}", "stop", "{session}"]
[operations.attach]
argv = ["{command}", "attach", "{session}"]
```

Select it without changing Focus's Python code:

```toml
[[apps]]
name = "background-services"
extension = "team-session"

[apps.options]
group = "my-project"
display = "none"

[[apps.options.jobs]]
name = "api"
root = "api"
command = "npm run dev"
```

The executable must implement the declared contracts, including foreground
process ownership and the inventory format. This minimal specification omits
independent views, so `display = "windows"` is unsupported. Use the bundled tmux
specification as the reference when adding that capability.

## Compatibility and validation

Existing `[vscode]`, `[chrome]`, `[context]`, `[tmux]`, and `[startup]` sections
are translated to the bundled extensions. When a project declares an `apps` list, omitted legacy
sections do not add default applications or service sessions. Explicit legacy
sections still translate; enabling both forms for the same extension is an error. Files without
an `apps` key retain their existing defaults. `focus init` emits the new resource
form; existing project files are not rewritten automatically. Valid legacy window
bindings are upgraded without reopening the recorded windows.

New configuration uses one resource list for GUI apps and services. To omit tmux,
remove its entry or set `enabled = false`; do not leave an explicit legacy
`[tmux]` or `[startup]` section that still requests it. An empty `apps = []` list
requests no application or session resources.

Plain `[[apps]]` entries with `bundle_id` remain supported for one-off use. They
use literal `open`, `window_command`, and `instance_command` configuration rather
than specification templates. See [Setup](SETUP.md#add-other-applications).

After saving a specification and project options, inspect the resulting plan:

```sh
focus doctor
focus my-project --dry-run
```

These checks validate configuration and executable availability without proving
GUI behavior. Normal focus on an existing project desktop still only switches;
use `--force-gui` for an intentional repair. The GUI/session extension refactors and additional
provider examples still need live acceptance tests. See
[verification status](HOW_IT_WORKS.md#verification-status).
