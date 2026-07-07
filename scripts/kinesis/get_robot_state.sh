#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="${SPOT_ENV_FILE:-${repo_root}/.spot/robot.env}"
command_name="${1:-state}"

case "${command_name}" in
  state|hardware|metrics|joints|frame_tree) ;;
  -h|--help)
    echo "Usage: $0 [state|hardware|metrics|joints|frame_tree]"
    echo
    echo "Loads credentials from SPOT_ENV_FILE or ${repo_root}/.spot/robot.env."
    exit 0
    ;;
  *)
    echo "Unknown robot-state command: ${command_name}" >&2
    echo "Expected one of: state, hardware, metrics, joints, frame_tree" >&2
    exit 2
    ;;
esac

if [[ ! -f "${env_file}" ]]; then
  echo "Missing Spot environment file: ${env_file}" >&2
  echo "Create it from ${repo_root}/.spot/robot.env.example." >&2
  exit 2
fi

set -a
source "${env_file}"
set +a

if [[ -z "${SPOT_HOSTNAME:-}" ]]; then
  echo "SPOT_HOSTNAME is not set in ${env_file}" >&2
  exit 2
fi

if [[ -z "${BOSDYN_CLIENT_USERNAME:-}" || -z "${BOSDYN_CLIENT_PASSWORD:-}" ]]; then
  echo "BOSDYN_CLIENT_USERNAME and BOSDYN_CLIENT_PASSWORD must be set in ${env_file}" >&2
  exit 2
fi

cd "${repo_root}/python/examples/get_robot_state"
if [[ -d "${repo_root}/.python-deps" ]]; then
  export PYTHONPATH="${repo_root}/.python-deps${PYTHONPATH:+:${PYTHONPATH}}"
fi
exec "${PYTHON:-python3}" get_robot_state.py "${SPOT_HOSTNAME}" "${command_name}"
