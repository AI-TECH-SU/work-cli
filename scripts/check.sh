#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/work-cli-check.XXXXXX")"

cleanup() {
  rm -rf -- "$temporary_root"
}
trap cleanup EXIT

if [[ -n "${PYTHON:-}" ]]; then
  python_command="$PYTHON"
elif [[ -x "$repo_root/.venv/bin/python" ]]; then
  python_command="$repo_root/.venv/bin/python"
else
  python_command="python3"
fi
if ! command -v "$python_command" >/dev/null 2>&1; then
  printf 'error: Python command not found: %s\n' "$python_command" >&2
  exit 1
fi

cd "$repo_root"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$python_command" -m unittest discover -s tests -v

PYTHONPYCACHEPREFIX="$temporary_root/pycache" \
  "$python_command" -m compileall -q -f src tests scripts

git diff --check
git diff --cached --check

PYTHON="$python_command" "$repo_root/scripts/smoke-test.sh"

printf 'All development checks passed.\n'
