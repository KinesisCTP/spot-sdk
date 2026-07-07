#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -d "${repo_root}/.python-deps" ]]; then
  export PYTHONPATH="${repo_root}/.python-deps${PYTHONPATH:+:${PYTHONPATH}}"
fi

exec "${PYTHON:-python3}" "${repo_root}/scripts/kinesis/graphnav_recording.py" "$@"
