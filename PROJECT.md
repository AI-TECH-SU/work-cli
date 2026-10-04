# Project context

## Purpose

Build a local-first macOS terminal application that restores a named software
project's working context with one command: `focus <project>`.

## Product constraints

- Keep configuration local, transparent, and auditable.
- Use dependency-free Python at runtime and TOML configuration.
- Automate every safe, programmable v1 setup and restoration step.
- Do not add cloud sync, AI, telemetry, a background daemon, or broad macOS
  permissions.
- Use our Desktop Names app to create, name, and switch project desktops.
  Do not use yabai. Desktop Names owns names and navigation; Focus owns project
  UUID bindings. Reuse a valid assigned UUID or an unambiguous project-name
  match; otherwise create and name a project desktop. Automatically replace a
  deleted desktop and retain the replacement UUID across partial launch failures.
  Never implicitly rename the caller's unrelated desktop.
- By default, reuse project windows already open elsewhere by moving them to the project
  desktop. Open missing windows in the existing application, including VS Code,
  Chrome, Terminal, and future application integrations. Preserve the selected
  profile and settings. Separate app instances or isolated user-data directories
  require an explicit configuration choice, not a window-placement workaround.
- Each app configuration controls `launch_mode` (window or instance) and
  `reuse_existing` independently. Reuse, when enabled, takes priority; launch mode
  applies when a new window is needed. With reuse disabled, leave old windows in
  place, identify the new one, and fail if the app reuses an old window instead.
- Model applications as `[[apps]]` resources with optional declarative TOML
  extension specifications. Ship VS Code and Chrome as specifications interpreted
  by the same engine as user extensions. Keep app defaults, options, launch,
  detection, and matching rules in those specifications; do not dispatch to
  app-specific Python handlers. Translate legacy sections into the same resource
  model. Keep Desktop Names lifecycle and tmux service views in the core.
- Allow direct bundle identity, launch arguments, and matching rules for apps
  without a reusable extension. Adopt their existing windows only from saved
  identity or explicit rules; do not infer ownership from an app bundle. Verify
  launch results and report unsupported app behavior.
- When separate service windows are configured, default to one new Terminal window per
  service in the existing Terminal app. Separate Terminal instances require explicit
  `launch_mode = "instance"` and can restore saved windows. Verify visible placement separately
  from tmux process/client state.

## V1 resources

- bundled VS Code extension: workspace/folder, profile, and context document
- tmux session with optional project-rooted windows
- bundled Chrome extension: profile and configured URLs
- local application extension specifications
- direct macOS application resources, documents/URLs, and launch/reuse rules
- project `PROJECT.md`
- optional development startup command

## Core behavior

With project desktops enabled, `focus <project>` only switches when that desktop
already exists. First launch creates and names a desktop, moves matching existing
windows, and opens missing resources. `--force-gui` explicitly repairs a partial
desktop. Keep the original VS Code workspace and profile; do not copy workspace
descriptors for window placement.

`focus stop <project>` closes windows exclusively on that desktop, stops its
tmux services, and removes the now-empty desktop and assignment. Never terminate
a shared app process or close its other desktops' windows. Honor save/close
prompts and retain the desktop on failure. Without a dedicated project desktop,
stop only the project's services. `focus restart` keeps its desktop and windows.
