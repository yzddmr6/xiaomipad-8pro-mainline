#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build upstream TinyALSA's mixer utility for the minimal ARM64 RAM diagnostic.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ${1:-} == --help ]]; then
    echo 'Usage: TINYALSA_SOURCE=... OUT_DIR=... [CROSS_COMPILE=aarch64-linux-gnu-] build-tinymix.sh'
    echo 'Builds only upstream tinymix and its static library; no host installation or device access.'
    exit 0
fi
[[ $# == 0 ]] || exit 2
src=${TINYALSA_SOURCE:-}
out=${OUT_DIR:?set an isolated build output directory}
cross=${CROSS_COMPILE:-aarch64-linux-gnu-}
mkdir -p "$out"
out=$(realpath "$out")
exec 9>"$out/.build.lock"
flock -n 9 || { echo 'tinymix output already in use' >&2; exit 1; }
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' \
    "$repo/device/audio/tinyalsa/source.json")
if [[ -n $src ]]; then
    src=$(realpath "$src")
    [[ $(git -C "$src" rev-parse HEAD) == "$expected" && -z $(git -C "$src" status --porcelain) ]] || {
        echo 'TinyALSA source must be clean at the pinned commit' >&2; exit 1;
    }
else
    python3 "$repo/tools/fetch-deps.py" --export-vendor tinyalsa --output "$out/vendor"
    src="$out/vendor"
fi
[[ $(${cross}gcc -dumpmachine) == aarch64*-linux-gnu ]] || { echo 'Need an ARM64 Linux compiler' >&2; exit 1; }
identity=$({
    cat "$repo/third_party/tinyalsa/source.json"
    cat "$repo/device/audio/tinyalsa/source.json" "$0"
    "${cross}gcc" --version
    "${cross}ld" --version
    sha256sum "$("${cross}gcc" -print-file-name=libc.a)"
} | sha256sum | cut -d' ' -f1)
if [[ -f $out/input.sha256 ]]; then
    [[ $(cat "$out/input.sha256") == "$identity" ]] || { echo 'Build inputs changed; use a new OUT_DIR' >&2; exit 1; }
else
    [[ ! -e $out/source ]] || { echo 'Unidentified source in OUT_DIR' >&2; exit 1; }
    mkdir "$out/source"
    if [[ -n ${TINYALSA_SOURCE:-} ]]; then
        git -C "$src" archive "$expected" | tar -xf - -C "$out/source"
    else
        cp -a "$src/." "$out/source/"
    fi
    printf '%s\n' "$identity" >"$out/input.sha256"
fi
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_date_epoch"])' "$repo/third_party/tinyalsa/source.json")
# The upstream Makefiles leave TINYALSA_USES_PLUGINS undefined: hardware API only.
# Command-line LDFLAGS override the utils Makefile's default dynamic PIE link.
export CFLAGS="-O2 -ffile-prefix-map=$out/source=."
export CPPFLAGS=
make -j"$(nproc)" -C "$out/source/src" CROSS_COMPILE="$cross" CC="ccache ${cross}gcc" ARFLAGS=rcD libtinyalsa.a
make -j"$(nproc)" -C "$out/source/utils" CROSS_COMPILE="$cross" CC="ccache ${cross}gcc" \
    LDFLAGS='-static -L../src' tinymix
mkdir -p "$out/bin"
"${cross}strip" --strip-unneeded -o "$out/bin/tinymix" "$out/source/utils/tinymix"
python3 - "$repo" "$out" "$cross" <<'PY'
import hashlib, json, pathlib, struct, subprocess, sys
repo, out = map(pathlib.Path, sys.argv[1:3]); cross = sys.argv[3]
binary = out / 'bin/tinymix'; data = binary.read_bytes()
assert data[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', data, 18)[0] == 183
phoff = struct.unpack_from('<Q', data, 32)[0]
phsize, phnum = struct.unpack_from('<HH', data, 54)
assert all(struct.unpack_from('<I', data, phoff + i * phsize)[0] not in (2, 3) for i in range(phnum)), 'Dynamic executable'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
lock = json.loads((repo / 'device/audio/tinyalsa/source.json').read_text())
record = {'schema': 1, 'state': 'BUILT_NOT_DEVICE_TESTED', 'source': lock,
          'architecture': 'aarch64', 'static': True, 'plugins': False,
          'file': 'bin/tinymix', 'bytes': len(data), 'sha256': sha(binary),
          'compiler': subprocess.check_output([cross+'gcc', '--version'], text=True).splitlines()[0],
          'libc_archive_sha256': sha(pathlib.Path(subprocess.check_output([cross+'gcc', '-print-file-name=libc.a'], text=True).strip())),
          'builder_sha256': sha(repo / 'tools/build-tinymix.sh'),
          'scope': 'Local static diagnostic build; no host installation, tablet access or hardware claim'}
(out / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'file': str(binary), 'bytes': len(data), 'sha256': record['sha256']}))
PY
