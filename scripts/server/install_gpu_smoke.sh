#!/usr/bin/env bash
# Install only from a transferred, checksummed wheelhouse; no network or models.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/activate.sh"
cd "$RSI_STORAGE_ROOT/wheelhouse"
sha256sum --check SHA256SUMS
python -m pip install --no-index --no-compile --find-links . \
    -r "$script_dir/gpu_smoke_requirements.txt"
python -m pip check
python -m pip freeze --all > "$RSI_STORAGE_ROOT/reports/pip-freeze.txt"
conda list --explicit > "$RSI_STORAGE_ROOT/reports/conda-explicit-installed.txt"
