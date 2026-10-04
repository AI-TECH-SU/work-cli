# Application extensions

An application extension is a TOML specification that tells Focus how to launch
and identify an app. The project file selects the extension and supplies its
options. The same engine interprets the bundled `vscode` and `chrome`
specifications and local specifications for other apps.

For example, this project uses two extensions:

```toml
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

`editor` and `browser` are user-chosen resource names. `vscode` and `chrome` select
specification files. `workspace`, `profile`, `context`, and `urls` are inputs
declared by those specifications. A different extension can declare different
inputs without a Focus code change.

The resource controls `enabled`, `launch_mode`, and `reuse_existing`; their
defaults are `true`, `"window"`, and `true`. Reuse takes priority. A launch mode
only applies when a window needs to be opened. Resource-level `match_title` and
`match_document` rules can further restrict the extension's matching behavior.
Those explicit rules require Desktop Names window inventory even when desktop
management is disabled; a status probe cannot satisfy them.

The core owns project desktops, verified window movement, saved window identity,
locking, and cleanup. tmux and its Terminal service views remain core resources.
Extensions do not replace that lifecycle or add Python handlers to it.

## Bundled and local specifications

The bundled specifications are ordinary package files:

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

## Specification reference

Unknown fields, unknown project options, invalid types, and unsupported template
tokens are rejected. Both launch modes must be declared.

### Identity and lifecycle fields

| Field | Meaning |
| --- | --- |
| `version` | Required; `1` |
| `id` | Required; matches the specification filename |
| `bundle_id` | Required macOS bundle identity used for window inventory |
| `detached` | Whether to start the launch command without waiting for its process to exit; default `false` |
| `require_match` | Require a newly appeared window to satisfy the specification matcher; default `false` |
| `adopt_single_on_desktop` | Allow adoption of the sole app window already on the target project desktop when no stronger match exists; default `false` |
| `restore_on_fresh_boot` | Without desktop management, request a launch on a fresh tmux boot even if the status probe reports presence; default `false` |
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

## Compatibility and validation

Existing `[vscode]`, `[chrome]`, and `[context]` sections are translated to the
bundled extensions. When a project declares an `apps` list, omitted legacy
sections do not add default applications. Explicit legacy sections still
translate; enabling both forms for the same extension is an error. Files without
an `apps` key retain their existing defaults. `focus init` emits the new resource
form; existing project files are not rewritten automatically. Valid legacy window
bindings are upgraded without reopening the recorded windows.

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
use `--force-gui` for an intentional repair. The extension refactor and additional
app examples still need live GUI acceptance tests. See
[verification status](HOW_IT_WORKS.md#verification-status).
