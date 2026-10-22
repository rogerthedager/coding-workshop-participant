#!/usr/bin/env bash
# Run the Selenium end-to-end tests (e2e/) in headless Firefox.
# Starts a private stack (fresh database + API :8029 + frontend :3029) and removes it after.
# Extra arguments go to pytest, e.g.:  bash bin/e2e-test.sh -k signup -v
set -euo pipefail
e2e="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../e2e" && pwd)"
if [[ ! -x "$e2e/.venv/bin/python" ]]; then
  python3 -m venv "$e2e/.venv"
  "$e2e/.venv/bin/pip" install -q -r "$e2e/requirements.txt"
fi
cd "$e2e"
exec "$e2e/.venv/bin/python" -m pytest "$@"
