#!/bin/sh
# Run inside a clean Ubuntu resolute build directory with the locked source archives.
set -eu
recipe=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 - "$recipe" <<'PY'
import hashlib, json, sys
from pathlib import Path
recipe = Path(sys.argv[1])
lock = json.loads((recipe / 'source.json').read_text())
for directory, entries in [(Path.cwd(), lock['files']), (recipe, lock['patches'])]:
    for item in entries:
        p = directory / item['file']
        if hashlib.sha256(p.read_bytes()).hexdigest() != item['sha256']:
            sys.exit(f'Input differs from the source lock: {p}')
PY
dpkg-source -x mutter_50.1-0ubuntu2.5.dsc mutter
cd mutter
patch=native-release-power-save-onscreens-in-dispose.patch
cp "$recipe/$patch" debian/patches/
printf '\n%s\n' "$patch" >>debian/patches/series
{ cat "$recipe/changelog-entry"; echo; cat debian/changelog; } >debian/changelog.new
mv debian/changelog.new debian/changelog
dpkg-buildpackage -b -us -uc
