#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
python_command="${PYTHON:-python3}"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/work-cli-smoke.XXXXXX")"

cleanup() {
  rm -rf -- "$temporary_root"
}
trap cleanup EXIT

if ! command -v "$python_command" >/dev/null 2>&1; then
  printf 'error: Python command not found: %s\n' "$python_command" >&2
  exit 1
fi

export XDG_CONFIG_HOME="$temporary_root/config"
export XDG_STATE_HOME="$temporary_root/state"
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"

fake_command="$temporary_root/bin/work-fake-command"
mkdir -p "$(dirname "$fake_command")" "$XDG_CONFIG_HOME/work"
printf '#!/bin/sh\nexit 1\n' > "$fake_command"
chmod +x "$fake_command"

fake_command_toml="$("$python_command" -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$fake_command")"
{
  printf 'version = 1\n\n[commands]\n'
  printf 'tmux = %s\n' "$fake_command_toml"
  printf 'code = %s\n' "$fake_command_toml"
  printf 'chrome = %s\n' "$fake_command_toml"
} > "$XDG_CONFIG_HOME/work/config.toml"

run_work() {
  "$python_command" -m workspace_manager "$@"
}

run_work init work-cli-smoke \
  --root "$repo_root" \
  --non-interactive

project_config="$XDG_CONFIG_HOME/work/projects/work-cli-smoke.toml"
[[ -f "$project_config" ]]

list_output="$(run_work list)"
[[ "$list_output" == "work-cli-smoke" ]]

dry_run_output="$(run_work work-cli-smoke --dry-run)"
[[ "$dry_run_output" == *"new-session"* ]]
[[ "$dry_run_output" == *"--new-window"* ]]

status_output="$(run_work status work-cli-smoke --json)"
"$python_command" -c \
  'import json, sys; status = json.loads(sys.argv[1]); assert status["project"] == "work-cli-smoke"; assert status["booted"] is False' \
  "$status_output"

printf 'Smoke test passed with isolated config and dry-run commands.\n'
