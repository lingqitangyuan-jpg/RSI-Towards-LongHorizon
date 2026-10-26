#!/usr/bin/env bash
# Source this file for the accepted port-22 setup; only the current shell changes.
export RSI_STORAGE_ROOT="/nfs-data/sdf/lyf/rsi-long-horizon"
export CONDA_ENVS_PATH="$RSI_STORAGE_ROOT/envs"
export CONDA_PKGS_DIRS="$RSI_STORAGE_ROOT/cache/conda/pkgs"
export PIP_CACHE_DIR="$RSI_STORAGE_ROOT/cache/pip"
export XDG_CACHE_HOME="$RSI_STORAGE_ROOT/cache/xdg"
export HF_HOME="$RSI_STORAGE_ROOT/cache/huggingface"
export UV_CACHE_DIR="$RSI_STORAGE_ROOT/cache/uv"
export TORCH_HOME="$RSI_STORAGE_ROOT/cache/torch"
export TRITON_CACHE_DIR="$RSI_STORAGE_ROOT/cache/triton"
export CUDA_CACHE_PATH="$RSI_STORAGE_ROOT/cache/cuda"
export TMPDIR="$RSI_STORAGE_ROOT/tmp"
export PYTHONNOUSERSITE=1
export CUDA_DEVICE_ORDER=PCI_BUS_ID
mkdir -p "$TORCH_HOME" "$TRITON_CACHE_DIR" "$CUDA_CACHE_PATH" || return 1
if [[ ! -x "$RSI_STORAGE_ROOT/envs/rsi-py312/bin/python" ]]; then
    printf '%s\n' "Environment not found: $RSI_STORAGE_ROOT/envs/rsi-py312" >&2
    return 1
fi
source /opt/miniforge3/etc/profile.d/conda.sh || return 1
conda activate "$RSI_STORAGE_ROOT/envs/rsi-py312" || return 1
# Select an available physical GPU explicitly for each run, for example:
# CUDA_VISIBLE_DEVICES=0 python your_script.py
# For the smoke test, pass --gpu <GPU UUID>; it selects and checks that GPU.
