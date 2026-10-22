#!/usr/bin/env bash
# Run facility-service migrations, first-admin seed or demo data inside AWS.
#
# The database only accepts connections from inside the VPC, and Terraform does not
# create a separate migration Lambda. So this script briefly switches the deployed
# API Lambda to migration_handler.handler, invokes it once, then restores the
# original handler and environment (the seed password never stays on the function).
#
# Usage:
#   Migrate + first admin:
#     SEED_ADMIN_EMAIL=you@acme.inc SEED_ADMIN_PASSWORD='...' bash bin/facility-aws-migrate.sh
#   Demo data, 10+ records per feature (all demo accounts get DEMO_PASSWORD):
#     OPERATION=showcase DEMO_PASSWORD='...' [INCIDENTS=120] bash bin/facility-aws-migrate.sh
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$repo_root/ENVIRONMENT.config" ]] && source "$repo_root/ENVIRONMENT.config"
export AWS_REGION="${AWS_REGION:-us-east-2}" AWS_PAGER=""

fn="${FACILITY_FUNCTION:-${TF_VAR_aws_project:-coding-workshop}-facility-service-${PARTICIPANT_ID:?PARTICIPANT_ID missing; run bin/setup-participant.sh}}"
operation="${OPERATION:-migrate}"
case "$operation" in
  migrate)
    : "${SEED_ADMIN_EMAIL:?Set SEED_ADMIN_EMAIL (must end with @acme.inc)}"
    : "${SEED_ADMIN_PASSWORD:?Set SEED_ADMIN_PASSWORD (10+ chars, letters and digits)}"
    payload='{"operation":"migrate"}'
    ;;
  showcase)
    : "${DEMO_PASSWORD:?Set DEMO_PASSWORD (10+ chars, letters and digits, not DemoPass123!)}"
    payload="{\"operation\":\"showcase\",\"incidents\":${INCIDENTS:-120}}"
    ;;
  *) echo "ERROR: OPERATION must be migrate or showcase" >&2; exit 1 ;;
esac

work="$(mktemp -d)"
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT

echo "INFO: Reading current configuration of $fn"
aws lambda get-function-configuration --function-name "$fn" >"$work/original.json"
original_handler="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["Handler"])' "$work/original.json")"
original_memory="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["MemorySize"])' "$work/original.json")"

# Environment files: original (to restore) and original + seed variables (for the run).
OPERATION="$operation" python3 - "$work" <<'PY'
import json, os, sys
work = sys.argv[1]
env = json.load(open(f"{work}/original.json")).get("Environment", {}).get("Variables", {})
json.dump({"Variables": env}, open(f"{work}/env-restore.json", "w"))
extra = {k: os.environ[k] for k in ("SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD", "DEMO_PASSWORD")
         if os.environ.get(k)}
if os.environ.get("OPERATION") == "showcase":
    extra["FACILITY_ALLOW_DEMO_SEED"] = "true"
seeded = dict(env, **extra)
json.dump({"Variables": seeded}, open(f"{work}/env-seed.json", "w"))
PY

restore() {
  echo "INFO: Restoring handler '$original_handler', memory ${original_memory}MB and original environment"
  aws lambda wait function-updated --function-name "$fn" || true
  aws lambda update-function-configuration --function-name "$fn" \
    --handler "$original_handler" --memory-size "$original_memory" \
    --environment "file://$work/env-restore.json" >/dev/null
  aws lambda wait function-updated --function-name "$fn"
  rm -rf "$work"
}
trap restore EXIT

echo "INFO: Switching to migration handler"
aws lambda update-function-configuration --function-name "$fn" \
  --handler migration_handler.handler --memory-size 512 \
  --environment "file://$work/env-seed.json" >/dev/null
aws lambda wait function-updated --function-name "$fn"

echo "INFO: Running $operation (first call may take ~30s while Aurora wakes up)"
aws lambda invoke --function-name "$fn" --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 310 --payload "$payload" "$work/result.json" >"$work/meta.json"

cat "$work/result.json"; echo
if grep -q FunctionError "$work/meta.json"; then
  echo "ERROR: $operation failed (see result above and CloudWatch logs for $fn)" >&2
  exit 1
fi
echo "INFO: $operation succeeded"
