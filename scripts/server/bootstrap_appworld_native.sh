#!/usr/bin/env bash
# Trusted engineering acceptance only. No model requests, GPU or Docker use.
set -Eeuo pipefail
: "${RSI_STORAGE_ROOT:=/nfs-data/sdf/lyf/rsi-long-horizon}"
: "${RSI_PROJECT_ROOT:=$RSI_STORAGE_ROOT/code/RSI-Towards-LongHorizon}"
: "${RSI_NATIVE_STAGE:?point to the verified offline Linux wheelhouse directory}"
: "${RSI_NATIVE_RUN_ID:?set a unique run id}"
[[ "$RSI_NATIVE_RUN_ID" =~ ^[A-Za-z0-9_-]+$ ]] || exit 2
root="$RSI_STORAGE_ROOT"
prefix="$root/envs/appworld-native-py312"
config="$RSI_PROJECT_ROOT/configs/server/appworld_native_p0.toml"
report="$root/reports/$RSI_NATIVE_RUN_ID"
mkdir -p "$root/reports"
[[ ! -e "$report.status.json" ]] || { echo 'Run id already used' >&2; exit 2; }
phase="initializing"
record_exit() {
  local code=$?
  trap - EXIT
  "$root/envs/rsi-py312/bin/python" - "$report.status.json" "$phase" "$code" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json,sys
result={"schema_version":"appworld-native-job/v1","last_phase":sys.argv[2],
        "exit_code":int(sys.argv[3]),"finished_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_calls":0,"gpu_used":False}
with Path(sys.argv[1]).open('x') as f: json.dump(result,f,indent=2); f.write('\n')
PY
  exit "$code"
}
trap record_exit EXIT
source "$root/scripts/activate.sh"
export CUDA_VISIBLE_DEVICES=""
export APPWORLD_ROOT="$root/appworld-native"
export APPWORLD_CACHE="$root/cache/appworld-native"
export IPYTHONDIR="$APPWORLD_CACHE/ipython"
export UV_PYTHON_DOWNLOADS=never
phase="verify_offline_artifacts"
(cd "$RSI_NATIVE_STAGE" && sha256sum -c SHA256SUMS)
phase="create_private_environment"
if [[ ! -x "$prefix/bin/python" ]]; then
  [[ ! -e "$prefix" ]] || { echo 'Incomplete prefix exists; inspect before retry' >&2; exit 2; }
  uv venv --python "$root/envs/rsi-py312/bin/python" --no-python-downloads "$prefix"
fi
phase="install_offline_dependencies"
uv pip install --python "$prefix/bin/python" --no-index \
  --find-links "$RSI_NATIVE_STAGE/wheelhouse" --require-hashes \
  -r "$RSI_NATIVE_STAGE/requirements-linux.lock"
uv pip check --python "$prefix/bin/python"
uv pip freeze --python "$prefix/bin/python" > "$report.packages.txt"
phase="install_official_apps"
"$prefix/bin/appworld" install
phase="prepare_one_task_data"
"$prefix/bin/python" "$RSI_PROJECT_ROOT/scripts/server/accept_appworld_native.py" prepare-data \
  --config "$config" --bundle "$root/incoming/data-0.1.0.bundle" \
  --output "$report.data.json"
phase="native_environment_acceptance"
"$prefix/bin/python" "$RSI_PROJECT_ROOT/scripts/server/accept_appworld_native.py" run \
  --config "$config" --run-id "$RSI_NATIVE_RUN_ID" --output "$report.acceptance.json"
phase="complete"
