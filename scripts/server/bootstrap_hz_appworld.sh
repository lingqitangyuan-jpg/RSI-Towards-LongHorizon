#!/usr/bin/env bash
# Prepare a Harness-Zero/AppWorld checkout on a Linux server.
#
# This script deliberately stops after dependency installation, AppWorld build
# context preparation, and local unit tests. It does not download private task
# data, submit scheduler jobs, or start a model rollout.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: bootstrap_hz_appworld.sh

Required environment variables:
  RSI_PROJECT_ROOT       Project checkout (used for path validation)
  RSI_HZ_ROOT            Harness-Zero checkout
  RSI_APPWORLD_DATA_ROOT Official AppWorld data root

The AppWorld data root must contain api_docs/, base_dbs/, datasets/, tasks/,
and version.txt. Harness-Zero's build.py expects these names below its own
data/ directory; missing entries are linked to the external data root without
copying or modifying the source data.
EOF
}

die() {
  printf 'bootstrap_hz_appworld: error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf 'bootstrap_hz_appworld: %s\n' "$*"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if (( $# != 0 )); then
  usage >&2
  die "unexpected argument: $1"
fi

: "${RSI_PROJECT_ROOT:?RSI_PROJECT_ROOT must point to the project checkout}"
: "${RSI_HZ_ROOT:?RSI_HZ_ROOT must point to the Harness-Zero checkout}"
: "${RSI_APPWORLD_DATA_ROOT:?RSI_APPWORLD_DATA_ROOT must point to official AppWorld data}"

command -v uv >/dev/null 2>&1 || die "uv is required on the server"
command -v python >/dev/null 2>&1 || die "python is required on the server"

project_root="$(cd -- "$RSI_PROJECT_ROOT" 2>/dev/null && pwd -P)" \
  || die "project root is not a directory: $RSI_PROJECT_ROOT"
hz_root="$(cd -- "$RSI_HZ_ROOT" 2>/dev/null && pwd -P)" \
  || die "Harness-Zero root is not a directory: $RSI_HZ_ROOT"
appworld_data_root="$(cd -- "$RSI_APPWORLD_DATA_ROOT" 2>/dev/null && pwd -P)" \
  || die "AppWorld data root is not a directory: $RSI_APPWORLD_DATA_ROOT"

[[ -d "$project_root" ]] || die "project root does not exist: $project_root"
[[ -f "$hz_root/pyproject.toml" ]] || die "missing Harness-Zero pyproject.toml under $hz_root"
[[ -f "$hz_root/uv.lock" ]] || die "missing Harness-Zero uv.lock under $hz_root"
[[ -f "$hz_root/envs/appworld/build.py" ]] || die "missing envs/appworld/build.py under $hz_root"

for required in api_docs base_dbs datasets tasks version.txt; do
  [[ -e "$appworld_data_root/$required" ]] \
    || die "AppWorld data is missing $required under $appworld_data_root"
done

hz_data="$hz_root/data"
mkdir -p "$hz_data"

# Harness-Zero 0.21's build.py resolves data relative to the repository. Keep
# the official data outside the checkout and expose only explicit symlinks.
link_data_entry() {
  local name="$1"
  local source="$appworld_data_root/$name"
  local destination="$hz_data/$name"
  local source_real destination_real

  source_real="$(realpath "$source")"
  if [[ -e "$destination" || -L "$destination" ]]; then
    destination_real="$(realpath "$destination" 2>/dev/null || true)"
    [[ "$destination_real" == "$source_real" ]] \
      || die "$destination already exists and does not point to $source"
    log "using existing data link: $destination -> $source_real"
    return
  fi

  ln -s "$source" "$destination"
  log "linked AppWorld data: $destination -> $source_real"
}

for required in api_docs base_dbs datasets tasks version.txt; do
  link_data_entry "$required"
done

cd "$hz_root"
export RSI_PROJECT_ROOT="$project_root"
export RSI_HZ_ROOT="$hz_root"
export RSI_APPWORLD_DATA_ROOT="$appworld_data_root"

log "installing locked Harness-Zero dependencies"
uv sync --locked --group dev

log "preparing the AppWorld Docker build context"
python envs/appworld/build.py --prepare-only

log "running Harness-Zero and AppWorld unit tests"
if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$hz_root/src:$PYTHONPATH"
else
  export PYTHONPATH="$hz_root/src"
fi
uv run pytest -q tests envs/appworld/tests

log "bootstrap checks completed; no rollout or scheduler job was started"
