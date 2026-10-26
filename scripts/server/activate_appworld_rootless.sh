#!/usr/bin/env bash
# Same AppWorld Python packages, different execution backend and cache.
source /nfs-data/sdf/lyf/rsi-long-horizon/scripts/activate_appworld_native.sh || return 1
export APPWORLD_CACHE="$RSI_STORAGE_ROOT/cache/appworld-rootless"
export IPYTHONDIR="$APPWORLD_CACHE/ipython"
export RSI_EXECUTION_BACKEND="bubblewrap-pipe"
export CUDA_VISIBLE_DEVICES=""
