#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
deps_dir="${SPOT_PYTHON_DEPS_DIR:-${repo_root}/.python-deps}"
requirements="${repo_root}/python/examples/get_robot_state/requirements.txt"

mkdir -p "${deps_dir}"
python3 -m pip install --upgrade --target "${deps_dir}" -r "${requirements}"

echo "Installed Spot SDK robot-state dependencies into ${deps_dir}"
