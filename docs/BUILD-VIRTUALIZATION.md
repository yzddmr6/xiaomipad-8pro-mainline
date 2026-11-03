# Building the optional VM packages

The build is an unprivileged, offline cross-build after input preparation. It does not use a device or install host packages. `tools/crosvm.lock.json` pins the crosvm/minijail commits, the unchanged 30-patch iceland-gvm queue, the separate shutdown-log patch, Cargo.lock, target sysroot packages, host compiler packages, Rust 1.88.0 and resulting ELF hashes.

Prepare the exact host package versions listed under host_packages and a Rust 1.88.0 toolchain containing the aarch64-unknown-linux-gnu standard library. Use an existing Cargo registry cache matching Cargo.lock, or the public registry cache included in the corresponding-source archive. The cache contains source crates and registry metadata, never credentials. Populate the download cache using:

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
```

Build into an empty directory:

```sh
nice -n 10 python3 tools/build-crosvm.py \
  --inputs inputs --cargo-cache cargo-cache \
  --rust-toolchain /path/to/1.88.0-x86_64-unknown-linux-gnu \
  --output build
```

The script applies patches in locked order without fuzz, extracts a private arm64 sysroot and host libclang, copies the registry into its own Cargo home, and builds gunyah,net with no default features. It uses --frozen --offline, SOURCE_DATE_EPOCH, stable path remapping and all available CPU threads at nice 10. GPU/VNC and dynamic GuestAccept are not enabled. No system Cargo configuration or shared target directory is modified. Build output includes crosvm; a mismatch against the lock fails.

Package using the existing device-package entry point:

```sh
python3 tools/build-device-debs.py --virtualization-only \
  --crosvm-build build --version "$RELEASE_VERSION" --output packages
python3 tools/bundle-virtualization-source.py \
  --build build --inputs inputs --output piano-virtualization-source.tar.zst
```

The output contains piano-vm and piano-crosvm arm64 Debian packages plus SHA256SUMS. The ordinary device support and kernel packages must be built at the same release version with their virtualization integration. Do not install the two optional packages against an older device integration. Include the corresponding-source archive with binary distribution, preserving all notices; it includes the original source archives, dependency source crates, patches, lock files and build scripts.

The network helper is installed root-owned at a fixed path. Device sandbox policy is enabled by default and packaged for the installed crosvm path; the VMM remains an ordinary-user process.

Pass `--virtualization-packages packages --virtualization-source piano-virtualization-source.tar.zst` to `tools/build-release.py`. The matching device-support version must already be in the root image. `tools/package-release-assets.py` carries these files in `packages.zip` under `optional/virtualization/`; ordinary upgrades continue to install only `debs/`. The VM packages are not forced into the default root image.
