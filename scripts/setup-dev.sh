#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
python_command="${PYTHON:-python3}"
venv_dir="$repo_root/.venv"

if ! command -v "$python_command" >/dev/null 2>&1; then
  printf 'error: Python command not found: %s\n' "$python_command" >&2
  exit 1
fi

if ! "$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  printf 'error: Python 3.11 or newer is required\n' >&2
  exit 1
fi

if [[ ! -x "$venv_dir/bin/python" ]]; then
  "$python_command" -m venv "$venv_dir"
  printf 'created %s\n' "$venv_dir"
fi

"$venv_dir/bin/python" -m pip install --editable "$repo_root"

printf '\nDevelopment environment is ready.\n'
printf 'Activate it with: source %q\n' "$venv_dir/bin/activate"
printf 'Run checks with:  %q\n' "$repo_root/scripts/check.sh"
