#!/bin/sh
# SPDX-License-Identifier: MIT
# Build the KernelSU-Next module zip with fixed timestamps and file order.
# usage: build.sh OUTDIR
set -eu
HERE=$(cd "${0%/*}" && pwd)
OUT=${1:?usage: build.sh OUTDIR}
VERSION=$(sed -n 's/^version=//p' "$HERE/module/module.prop")
FILES="module.prop customize.sh action.sh piano-switch.sh switch-common.sh LICENSE"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' 0
cp "$HERE/module/module.prop" "$HERE/module/customize.sh" "$HERE/module/action.sh" \
	"$HERE/module/piano-switch.sh" "$STAGE/"
cp "$HERE/../../boot/switch-common.sh" "$STAGE/switch-common.sh"
cp "$HERE/../../../LICENSE" "$STAGE/LICENSE"
chmod 0644 "$STAGE"/*
export TZ=UTC0
for f in $FILES; do touch -d '2026-01-01 00:00:00' "$STAGE/$f"; done
mkdir -p "$OUT"
ZIP=$OUT/piano-switch-ksu-$VERSION.zip
rm -f "$ZIP"
# shellcheck disable=SC2086
(cd "$STAGE" && zip -X -q "$ZIP.tmp" $FILES)
mv "$ZIP.tmp" "$ZIP"
(cd "$OUT" && sha256sum "${ZIP##*/}" >"${ZIP##*/}.sha256")
cat "$ZIP.sha256"
