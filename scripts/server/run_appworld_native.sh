#!/usr/bin/env bash
# Repeat only trusted native acceptance; never install or unpack data here.
set -Eeuo pipefail
: "${RSI_STORAGE_ROOT:=/nfs-data/sdf/lyf/rsi-long-horizon}"
: "${RSI_PROJECT_ROOT:=$RSI_STORAGE_ROOT/code/RSI-Towards-LongHorizon}"
: "${RSI_NATIVE_RUN_ID:?set a new unique run id}"
[[ "$RSI_NATIVE_RUN_ID" =~ ^[A-Za-z0-9_-]+$ ]] || exit 2
report="$RSI_STORAGE_ROOT/reports/$RSI_NATIVE_RUN_ID"
mkdir -p "$RSI_STORAGE_ROOT/reports"
for suffix in status.json acceptance.json packages.txt; do
    [[ ! -e "$report.$suffix" ]] || { echo 'Run id already used' >&2; exit 2; }
done
source "$RSI_PROJECT_ROOT/scripts/server/activate_appworld_native.sh"
phase="native_environment_acceptance"
record_exit() {
    local code=$?
    trap - EXIT
    python - "$report.status.json" "$phase" "$code" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json,sys
result={"schema_version":"appworld-native-job/v1","last_phase":sys.argv[2],
        "exit_code":int(sys.argv[3]),
        "finished_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_calls":0,"gpu_used":False}
with Path(sys.argv[1]).open('x') as f:
    json.dump(result,f,indent=2)
    f.write('\n')
PY
    exit "$code"
}
trap record_exit EXIT
python "$RSI_PROJECT_ROOT/scripts/server/accept_appworld_native.py" run \
    --config "$RSI_PROJECT_ROOT/configs/server/appworld_native_p0.toml" \
    --run-id "$RSI_NATIVE_RUN_ID" --output "$report.acceptance.json"
phase="complete"
