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

completion_dir=""
if command -v brew >/dev/null 2>&1; then
  completion_dir="$(brew --prefix)/share/zsh/site-functions"
elif [[ -d /usr/local/share/zsh/site-functions ]]; then
  completion_dir="/usr/local/share/zsh/site-functions"
fi

if [[ -n "$completion_dir" && -w "$(dirname "$completion_dir")" ]]; then
  mkdir -p "$completion_dir"
  for completion in _focus _work; do
    ln -sfn "$repo_root/completions/$completion" "$completion_dir/$completion"
    printf 'installed zsh completion: %s\n' "$completion_dir/$completion"
  done
else
  printf 'info: zsh completion was not installed; no writable site-functions directory found\n'
fi

printf '\nDevelopment environment is ready.\n'
printf 'Activate it with: source %q\n' "$venv_dir/bin/activate"
printf 'Run checks with:  %q\n' "$repo_root/scripts/check.sh"
