#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Compatibility entry point; all profiles use the same Python recipe.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ${1:-} == --help ]]; then
    echo 'Usage: MESA_SOURCE_ARCHIVE=... CROSS_FILE=... TARGET_ROOT=... OUT_DIR=... [JOBS=N] build-mesa.sh'
    exit 0
fi
[[ $# == 0 ]] || exit 2
exec python3 "$repo/tools/build-mesa.py" --profile desktop \
    --source-archive "${MESA_SOURCE_ARCHIVE:?set the official archive pinned in mesa/source.json}" \
    --cross-file "${CROSS_FILE:?set the ARM64 Meson cross file}" \
    --target-root "${TARGET_ROOT:?set the matching ARM64 SDK root}" \
    --output "${OUT_DIR:?set a fresh output directory}" --jobs "${JOBS:-$(nproc)}"
