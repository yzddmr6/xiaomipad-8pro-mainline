# Shared Adreno 830 Mesa inputs

`source.json` pins upstream Mesa and the ordered patches used by
both the desktop deb and the gpu-2404 builder. `piano-fixes.patch` contains the A830
revision-1 speedbin match and a shared-context publication fix. Contexts must
not enter the shared resource list until their state tracker is initialized;
failed creation leaves an empty list link for normal cleanup.

`shared-context-stress.c` uses real EGL/GLES calls: four threads create and
destroy shared contexts while a fifth allocates and deletes buffer storage.
Compile against EGL/GLES and pthreads, then select the candidate libraries:

```sh
LD_LIBRARY_PATH=/opt/piano/mesa-26.2.6/lib \
LIBGL_DRIVERS_PATH=/opt/piano/mesa-26.2.6/lib/dri \
  ./shared-context-stress Adreno
```

The renderer argument is mandatory. Do not disable threaded contexts for this
test. On the tested tablet, the original package crashed with SIGSEGV; the
fixed build completed 4,000 context lifetimes and over two million buffer
lifetimes. This is a concurrency regression, not complete GPU or suspend
coverage. A new process must load the updated libraries; an existing desktop
session retains its old mapped libraries until logout or reboot.

The installed package needs no environment: it registers its prefix with the
dynamic linker in `/etc/ld.so.conf.d/00-piano-mesa.conf`, ahead of the
distribution directories, so every process resolves Mesa's libraries to this
build. The variables above only select a candidate build that is not installed.

Build only the desktop package with `tools/build-device-debs.py --mesa-only
--mesa-build BUILD --output OUTPUT --version VERSION`. The builder validates
the source lock and every payload file. The snap provider must be rebuilt
separately; changing the desktop deb does not update an installed snap.

Remove the local shared-context change when the pinned upstream contains an
equivalent fix and the regression passes. No upstream submission is implied.

Mesa 26.2.6 already includes the gen8 TEX_LINE_OFFSET fix, so no separate
backport is applied. `freedreno-explicit-sync.patch` is the unmodified upstream
commit 1f9c4d529f4cfb332f5abfabbd0918f5d5f6cb4e by Sahitya Kandru:
only an incoming fence selects explicit synchronization; requesting an outgoing
fence alone must not suppress implicit synchronization.

`freedreno-thread-safe-tex-seqno.patch` retains the OnePlus project's texture
sequence allocator fix, including its original attribution. A830 revision-1
speedbin matching and the shared-context publication fix remain necessary.

Both desktop and gpu-2404 use `tools/build-mesa.py`, `--optimization=2` and
`b_ndebug=true`. Configuration and target compile commands are checked before
compilation. The desktop uses a private prefix, non-GLVND Mesa libraries and
Turnip; gpu-2404 retains its GLVND provider layout. Both include softpipe.
`tools/build-mesa.sh` is only a compatibility wrapper around this recipe.

`piano-mesa` supplies the common desktop binaries. The OnePlus integration uses
an `erhai-mesa` transitional package depending on it; there is no second Mesa
binary build. Once all supported upgrade paths have migrated and desktop
metapackages depend directly on `piano-mesa`, the transitional package can be
retired. The source lock, patches and recipe are maintained here; downstream
imports record this repository's commit and file hashes.

`turnip-a8xx-runtime-unroll.patch` adds bounded runtime loop unrolling and read scheduling for A8xx compute shaders; remove it when the pinned upstream includes equivalent changes.
`turnip-ssbo-wide-reads.patch` uses exact-width aligned read-only SSBO accesses with matching descriptor strides; remove it when the pinned upstream includes equivalent changes.

`turnip-a8xx-word-reuse.patch` reuses immutable word loads across byte-wise compute loop iterations on A8xx. This is a local MIT-licensed change; remove it when the pinned upstream includes equivalent changes.
