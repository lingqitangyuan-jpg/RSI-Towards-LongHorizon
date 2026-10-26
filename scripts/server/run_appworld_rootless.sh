#!/usr/bin/env bash
# Engineering acceptance only; does not call any model or use a GPU.
set -Eeuo pipefail
RSI_STORAGE_ROOT="/nfs-data/sdf/lyf/rsi-long-horizon"
RSI_PROJECT_ROOT="$RSI_STORAGE_ROOT/code/RSI-Towards-LongHorizon"
test -n "$RSI_ROOTLESS_RUN_ID"
[[ "$RSI_ROOTLESS_RUN_ID" =~ ^[A-Za-z0-9_-]+$ ]] || exit 2
report="$RSI_STORAGE_ROOT/reports/$RSI_ROOTLESS_RUN_ID.status.json"
[[ ! -e "$report" ]] || { echo 'Run id already used' >&2; exit 2; }
source "$RSI_PROJECT_ROOT/scripts/server/activate_appworld_rootless.sh"
phase="rootless_acceptance"
record_exit() {
    local code=$?
    trap - EXIT
    python - "$report" "$phase" "$code" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json,sys
report={"schema_version":"appworld-rootless-job/v1","exit_code":int(sys.argv[3]),
        "last_phase":sys.argv[2],"finished_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_calls":0,"gpu_used":False,"docker_used":False}
with Path(sys.argv[1]).open('x') as f:
    json.dump(report,f,indent=2)
    f.write('\n')
PY
    exit "$code"
}
trap record_exit EXIT
python "$RSI_PROJECT_ROOT/scripts/server/accept_appworld_rootless.py" \
    --config "$RSI_PROJECT_ROOT/configs/server/appworld_rootless_p0.toml" \
    --run-id "$RSI_ROOTLESS_RUN_ID"
phase="complete"
