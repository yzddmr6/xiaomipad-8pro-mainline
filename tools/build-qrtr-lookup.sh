#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build only the upstream one-shot lookup utility, without installing a daemon.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ${1:-} == --help ]]; then
    echo 'Usage: QRTR_SOURCE=... OUT_DIR=... [CROSS_COMPILE=aarch64-linux-gnu-] build-qrtr-lookup.sh'
    echo 'Uses meson/ninja in PATH; creates a static ARM64 lookup tool in OUT_DIR only.'
    exit 0
fi
[[ $# == 0 ]] || exit 2
src=${QRTR_SOURCE:-}
out=${OUT_DIR:?set an isolated output directory}
cross=${CROSS_COMPILE:-aarch64-linux-gnu-}
mkdir -p "$out"
out=$(realpath "$out")
exec 9>"$out/.build.lock"
flock -n 9 || { echo 'QRTR output is in use' >&2; exit 1; }
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$repo/device/qrtr/source.json")
if [[ -n $src ]]; then
    src=$(realpath "$src")
    [[ $(git -C "$src" rev-parse HEAD) == "$expected" && -z $(git -C "$src" status --porcelain) ]] || {
        echo 'QRTR source must be clean at the pinned commit' >&2; exit 1;
    }
else
    python3 "$repo/tools/fetch-deps.py" --export-vendor qrtr --output "$out/vendor"
    src="$out/vendor"
fi
cross_spec=$(mktemp "$out/arm64.XXXXXX")
trap 'rm -f -- "$cross_spec"' EXIT
python3 - "$cross_spec" "$cross" <<'PY'
from pathlib import Path
import sys
cross = sys.argv[2]
text = "[binaries]\n" + f"c = ['ccache', {cross+'gcc'!r}]\nar = {cross+'ar'!r}\nstrip = {cross+'strip'!r}\n"
text += "[host_machine]\nsystem = 'linux'\ncpu_family = 'aarch64'\ncpu = 'armv8-a'\nendian = 'little'\n"
text += "[properties]\nneeds_exe_wrapper = true\n[built-in options]\nc_link_args = ['-static']\n"
Path(sys.argv[1]).write_text(text)
PY
identity=$({
    cat "$repo/third_party/qrtr/source.json"
    cat "$repo/device/qrtr/source.json" "$cross_spec" "$0"
    "${cross}gcc" --version
    "${cross}ld" --version
    sha256sum "$("${cross}gcc" -print-file-name=libc.a)"
    meson --version; ninja --version
} | sha256sum | cut -d' ' -f1)
if [[ -f $out/input.sha256 ]]; then
    [[ $(cat "$out/input.sha256") == "$identity" ]] || { echo 'QRTR build inputs changed; use a new OUT_DIR' >&2; exit 1; }
else
    [[ ! -e $out/build ]] || { echo 'Unidentified build in OUT_DIR' >&2; exit 1; }
    printf '%s\n' "$identity" >"$out/input.sha256"
fi
if ! cmp -s "$cross_spec" "$out/arm64.ini"; then
    mv "$cross_spec" "$out/arm64.ini"
fi
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_date_epoch"])' "$repo/third_party/qrtr/source.json")
unset CFLAGS CPPFLAGS LDFLAGS
if [[ ! -f $out/build/build.ninja ]]; then
    meson setup "$out/build" "$src" --cross-file "$out/arm64.ini" \
        --buildtype=release --wrap-mode=nofallback -Ddefault_library=static -Db_pie=false
fi
ninja -C "$out/build" -j"$(nproc)" src/qrtr-lookup
mkdir -p "$out/bin"
"${cross}strip" --strip-unneeded -o "$out/bin/qrtr-lookup" "$out/build/src/qrtr-lookup"
"${cross}readelf" -h "$out/bin/qrtr-lookup" >"$out/elf-header.txt"
"${cross}readelf" -l "$out/bin/qrtr-lookup" >"$out/elf-programs.txt"
grep -q 'Machine:.*AArch64' "$out/elf-header.txt"
if grep -qE 'INTERP|DYNAMIC' "$out/elf-programs.txt"; then
    echo 'QRTR lookup must be a static executable' >&2; exit 1
fi
python3 - "$repo" "$out" "$cross" <<'PY'
import hashlib, json, pathlib, subprocess, sys
repo, out = map(pathlib.Path, sys.argv[1:3]); cross = sys.argv[3]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
binary = out / 'bin/qrtr-lookup'
record = {'schema': 1, 'state': 'BUILT_NOT_DEVICE_TESTED',
          'source': json.loads((repo / 'device/qrtr/source.json').read_text()),
          'architecture': 'aarch64', 'static': True, 'plugins': False,
          'file': 'bin/qrtr-lookup', 'bytes': binary.stat().st_size, 'sha256': sha(binary),
          'compiler': subprocess.check_output([cross+'gcc', '--version'], text=True).splitlines()[0],
          'libc_archive_sha256': sha(pathlib.Path(subprocess.check_output([cross+'gcc', '-print-file-name=libc.a'], text=True).strip())),
          'builder_sha256': sha(repo / 'tools/build-qrtr-lookup.sh'),
          'scope': 'One-shot local nameservice query; no daemon, charger command or DSP service-health claim'}
(out / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'file': str(binary), 'bytes': record['bytes'], 'sha256': record['sha256']}))
PY
