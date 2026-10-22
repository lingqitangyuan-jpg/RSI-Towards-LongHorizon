#!/usr/bin/env bash
# Validate prepared AppWorld images with Harness-Zero's oracle fixtures.
# This invokes no model provider and submits no scheduler job.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: run_appworld_validation.sh

Environment variables:
  RSI_PROJECT_ROOT          Project checkout (optional when RSI_HZ_ROOT is set)
  RSI_HZ_ROOT               Harness-Zero checkout (default: <project>/sources/repo/Harness_zero/harness-zero-main)
  RSI_APPWORLD_IMAGE_TAG    Docker image tag (default: v2)
EOF
}

die() {
  printf 'run_appworld_validation: error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf 'run_appworld_validation: %s\n' "$*"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if (( $# != 0 )); then
  usage >&2
  die "unexpected argument: $1"
fi

command -v uv >/dev/null 2>&1 || die "uv is required on the server"
command -v docker >/dev/null 2>&1 || die "docker is required for AppWorld validation"

project_root="${RSI_PROJECT_ROOT:-$(pwd)}"
if [[ -z "${RSI_HZ_ROOT:-}" ]]; then
  RSI_HZ_ROOT="$project_root/sources/repo/Harness_zero/harness-zero-main"
fi

project_root="$(cd -- "$project_root" 2>/dev/null && pwd -P)" \
  || die "project root is not a directory: ${RSI_PROJECT_ROOT:-$project_root}"
hz_root="$(cd -- "$RSI_HZ_ROOT" 2>/dev/null && pwd -P)" \
  || die "Harness-Zero root is not a directory: $RSI_HZ_ROOT"
[[ -f "$hz_root/envs/appworld/validate.py" ]] \
  || die "missing envs/appworld/validate.py under $hz_root"
[[ -f "$hz_root/uv.lock" ]] || die "missing Harness-Zero uv.lock under $hz_root"

image_tag="${RSI_APPWORLD_IMAGE_TAG:-v2}"
[[ "$image_tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] \
  || die "invalid RSI_APPWORLD_IMAGE_TAG: $image_tag"

cd "$hz_root"
export RSI_PROJECT_ROOT="$project_root"
export RSI_HZ_ROOT="$hz_root"
if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$hz_root/src:$PYTHONPATH"
else
  export PYTHONPATH="$hz_root/src"
fi

log "running AppWorld image and oracle fixture validation (tag: $image_tag)"
uv run python envs/appworld/validate.py --image-tag "$image_tag"
log "validation completed; no model rollout or scheduler job was started"
