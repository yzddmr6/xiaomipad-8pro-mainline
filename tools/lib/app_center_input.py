# SPDX-License-Identifier: MIT
"""Stage a hash-pinned Canonical App Center archive for the desktop package."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stage_app_center(directory):
    manifest = directory / 'inputs.json'
    pin = json.loads(manifest.read_text())
    url = pin.pop('download_url')
    stem = f"{pin['name']}_{pin['revision']}"
    filename = stem + '.snap'
    assertion = directory / (stem + '.assert')
    if digest(assertion) != pin['sha256'][assertion.name]:
        raise ValueError('App Center assertion checksum mismatch')
    expected = pin['sha256'][filename]
    default_cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'tablet-build-inputs'
    cache = Path(os.environ.get('TABLET_BUILD_INPUT_CACHE', default_cache)) / expected
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / filename
    if not archive.is_file():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as out:
            temporary = Path(out.name)
        try:
            with urllib.request.urlopen(url, timeout=120) as source, temporary.open('wb') as out:
                shutil.copyfileobj(source, out)
            if digest(temporary) != expected:
                raise ValueError('Downloaded App Center checksum mismatch')
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    target = directory / filename
    shutil.copyfile(archive, target)
    if digest(target) != expected:
        raise ValueError(f'Invalid cached App Center archive: {archive}')
    target.chmod(0o644)
    # The runtime installer only consumes the signed input identity.
    manifest.write_text(json.dumps(pin, indent=2) + '\n')
