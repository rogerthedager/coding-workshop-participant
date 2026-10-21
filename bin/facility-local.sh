#!/usr/bin/env bash
set -euo pipefail
service_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../backend/facility-service" && pwd)"
python_path="${FACILITY_PYTHON:-$service_root/.venv/bin/python}"
if [[ ! -x "$python_path" ]]; then
  echo 'Create backend/facility-service/.venv first, or set FACILITY_PYTHON.' >&2
  exit 1
fi
exec "$python_path" "$service_root/scripts/local.py" "$@"
