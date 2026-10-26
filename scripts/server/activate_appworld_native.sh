#!/usr/bin/env bash
# Source in a fresh shell. This environment is for trusted CPU checks only.
_rsi_native_prefix="/nfs-data/sdf/lyf/rsi-long-horizon/envs/appworld-native-py312"
if [[ ! -x "$_rsi_native_prefix/bin/python" ]]; then
    printf '%s\n' 'AppWorld native environment is not installed yet.' >&2
    unset _rsi_native_prefix
    return 1
fi
export RSI_STORAGE_ROOT="/nfs-data/sdf/lyf/rsi-long-horizon"
export RSI_PROJECT_ROOT="$RSI_STORAGE_ROOT/code/RSI-Towards-LongHorizon"
export APPWORLD_ROOT="$RSI_STORAGE_ROOT/appworld-native"
export APPWORLD_CACHE="$RSI_STORAGE_ROOT/cache/appworld-native"
export IPYTHONDIR="$APPWORLD_CACHE/ipython"
export PIP_CACHE_DIR="$RSI_STORAGE_ROOT/cache/pip"
export UV_CACHE_DIR="$RSI_STORAGE_ROOT/cache/uv"
export XDG_CACHE_HOME="$RSI_STORAGE_ROOT/cache/xdg"
export TMPDIR="$RSI_STORAGE_ROOT/tmp"
export PYTHONNOUSERSITE=1
export CUDA_VISIBLE_DEVICES=""
source "$_rsi_native_prefix/bin/activate" || return 1
unset _rsi_native_prefix
# Use a separate shell and the original activate.sh for CUDA/model work.
