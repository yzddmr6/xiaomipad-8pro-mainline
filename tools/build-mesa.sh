#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build an isolated Mesa candidate from a pinned upstream source and sysroot.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ${1:-} == --help ]]; then
	echo 'Usage: MESA_SOURCE=... CROSS_FILE=... OUT_DIR=... [JOBS=4] build-mesa.sh'
	echo 'Requires meson/ninja and their native generators in PATH; installs to OUT_DIR/stage only.'
	exit 0
fi
[[ $# == 0 ]] || exit 2
src=$(realpath "${MESA_SOURCE:?set MESA_SOURCE to the pinned Mesa checkout}")
cross=$(realpath "${CROSS_FILE:?set CROSS_FILE to the ARM64 Meson cross file}")
out=${OUT_DIR:?set a fresh output directory}
mkdir -p "$out"
out=$(realpath "$out")
exec 9>"$out/.build.lock"
flock -n 9 || { echo 'Mesa output is already in use' >&2; exit 1; }
lock_text=$(python3 - "$repo/mesa/source.json" <<'PY'
import hashlib, json, pathlib, sys
path = pathlib.Path(sys.argv[1])
d = json.loads(path.read_text())
patches = [{'path': d['patch'], 'sha256': d['patch_sha256']}, *d.get('additional_patches', [])]
for patch in patches:
    if hashlib.sha256((path.parent.parent / patch['path']).read_bytes()).hexdigest() != patch['sha256']:
        sys.exit('Mesa patch SHA-256 mismatch: ' + patch['path'])
print(d['commit']); print(d['patch']); print(d['prefix'])
for patch in patches[1:]:
    print(patch['path'])
PY
)
readarray -t lock <<<"$lock_text"
[[ $(git -C "$src" rev-parse HEAD) == "${lock[0]}" ]] || { echo 'Mesa HEAD mismatch' >&2; exit 1; }
patches=("$repo/${lock[1]}")
for name in "${lock[@]:3}"; do patches+=("$repo/$name"); done
identity=$(cat "$repo/mesa/source.json" "${patches[@]}" "$cross" "$0" | sha256sum | cut -d' ' -f1)
if [[ -f $out/input.sha256 ]]; then
	[[ $(cat "$out/input.sha256") == "$identity" ]] || { echo 'Mesa inputs changed; use a new OUT_DIR' >&2; exit 1; }
else
	[[ ! -e $out/source && ! -e $out/build ]] || { echo 'Output contains an unidentified build' >&2; exit 1; }
	mkdir "$out/source"
	git -C "$src" archive "${lock[0]}" | tar -xf - -C "$out/source"
	for patch in "${patches[@]}"; do
		(cd "$out/source" && patch -p1 --batch --fuzz=0 -i "$patch")
	done
	printf '%s\n' "$identity" >"$out/input.sha256"
fi
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git -C "$src" show -s --format=%ct "${lock[0]}")
if [[ ! -f $out/build/build.ninja ]]; then
	# -O2 as in tools/build-mesa.py: GCC 13 -O3 miscompiles the GLSL compiler on ARM64.
	meson setup "$out/build" "$out/source" --cross-file "$cross" \
		--prefix="${lock[2]}" --libdir=lib --buildtype=release -Doptimization=2 --wrap-mode=nofallback \
		-Dplatforms=x11,wayland -Dgallium-drivers=freedreno,softpipe \
		-Dvulkan-drivers=freedreno -Dfreedreno-kmds=msm -Dllvm=disabled \
		-Dglx=dri -Degl=enabled -Dgbm=enabled -Dgles1=disabled -Dgles2=enabled \
		-Dglvnd=disabled -Dgallium-va=disabled -Dvideo-codecs= \
		-Dlibunwind=disabled -Dlmsensors=disabled -Dbuild-tests=false
fi
ninja -C "$out/build" -j"${JOBS:-4}"
DESTDIR="$out/stage" meson install -C "$out/build" --no-rebuild
python3 - "$repo" "$out" "$cross" <<'PY'
import hashlib, json, pathlib, subprocess, sys
repo, out, cross = map(pathlib.Path, sys.argv[1:])
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
d = json.loads((repo / 'mesa/source.json').read_text())
d.update(state='OFFLINE_BUILT_NOT_HARDWARE_TESTED', source_date_epoch=__import__('os').environ['SOURCE_DATE_EPOCH'],
         patch_sha256=sha(repo / d['patch']), cross_file_sha256=sha(cross),
         tools={x: subprocess.check_output([x, '--version'], text=True).strip() for x in ('meson', 'ninja')},
         files={str(p.relative_to(out / 'stage')): sha(p) for p in sorted((out / 'stage').rglob('*'))
                if p.is_file() and not p.is_symlink()})
(out / 'manifest.json').write_text(json.dumps(d, indent=2) + '\n')
PY
