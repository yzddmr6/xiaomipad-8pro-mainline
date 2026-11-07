# Pinned build dependencies

Build inputs are read from the existing local cache, then an owned copy, then
an immutable upstream URL. Every downloaded file is checked against its locked
SHA-256. A checksum failure stops immediately, including a corrupt cache entry;
no different version or floating branch is substituted.

`DEPS_ARCHIVE` selects a local directory containing the filenames in
`deps/sources.json`. `DEPS_MIRRORS` selects a directory containing the named
bare Git repositories. `DEPS_CACHE` selects the Git object cache.
`DEPS_OFFLINE=1` prohibits network access; `DEPS_OWN_ONLY=1` prohibits upstream
fallback. The two settings can be combined. Explicit source paths remain
subject to the component's existing commit/hash checks.

Examples:

```sh
DEPS_ARCHIVE=/path/to/archive DEPS_MIRRORS=/path/to/mirrors \
  DEPS_OFFLINE=1 DEPS_OWN_ONLY=1 \
  python3 tools/build-iris-vaapi-deb.py --fetch-only --output out/iris-inputs
python3 tools/fetch-deps.py --name iris-vaapi --output cache/deps
python3 tools/check-deps.py --local
python3 tools/test-deps.py
```

`sources.json` records archive identities and exact Git commits. The original
component locks remain authoritative for versions, patches and build options.
Institutional inputs are version/hash pinned and need no owned public archive.
Their optional local archive cache is checked before the fixed upstream URL.
Institutional package sources use dated snapshots or fixed release/object URLs;
APT verifies the signed snapshot metadata. Source archives in `third_party/`
retain their upstream licenses, copyright notices and provenance.

The monthly workflow checks URL availability and pinned owned Git refs. It
reports missing objects separately from transport errors. HEAD success does
not prove content integrity or build success; fetching always checks the hash.
A full system image still needs the locked toolchain, distribution packages and
any explicitly required private stock inputs. This mechanism does not package
personal calibration or Windows installation media.
