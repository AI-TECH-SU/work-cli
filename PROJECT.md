# Project context

## Purpose

Build a local-first macOS terminal application that restores a named software
project's working context with one command: `work <project>`.

## Product constraints

- Keep configuration local, transparent, and auditable.
- Use dependency-free Python at runtime and TOML configuration.
- Automate every safe, programmable v1 setup and restoration step.
- Do not add cloud sync, AI, telemetry, a background daemon, or broad macOS
  permissions.
- The user selects the target Mission Control desktop before invoking `work`.
  Automatic Space creation and switching is a future optional capability.

## V1 resources

- VS Code workspace/folder and profile
- tmux session
- Chrome profile and configured URLs
- project `PROJECT.md`
- optional development startup command

## Core behavior

The tmux session is the canonical boot marker. `work` must still reconcile each
detectable resource independently so invoking it again repairs a partial
workspace instead of duplicating a healthy one.
