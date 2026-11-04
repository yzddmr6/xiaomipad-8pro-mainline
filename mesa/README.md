# Piano Mesa inputs

`source.json` pins upstream Mesa and the ordered patches used by
both the desktop deb and the gpu-2404 builder. `piano-fixes.patch` contains the A830
revision-1 speedbin match and a shared-context publication fix. Contexts must
not enter the shared resource list until their state tracker is initialized;
failed creation leaves an empty list link for normal cleanup.

`shared-context-stress.c` uses real EGL/GLES calls: four threads create and
destroy shared contexts while a fifth allocates and deletes buffer storage.
Compile against EGL/GLES and pthreads, then select the candidate libraries:

```sh
LD_LIBRARY_PATH=/opt/piano/mesa-26.2.0/lib \
LIBGL_DRIVERS_PATH=/opt/piano/mesa-26.2.0/lib/dri \
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

The additional `turnip-gen8-tex-line-offset.patch` is the unmodified upstream
commit 7ea5e51c8a301a15202e28fef114e4c4055bc329 by Rob Clark (2026-08-31,
MIT), [MR !44151](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/44151),
also backported as f9a4bfdadbc9f2959b9b7a82c519c7f5a69db6c5. It encodes
gen8 TEX_LINE_OFFSET in bits in the common descriptor helper, including
Turnip's NV12 buffer-to-image upload. Both builders apply it after
`piano-fixes.patch` and validate both hashes. Remove this backport when the
pinned upstream includes it; package builds alone do not prove GPU rendering.

`turnip-a8xx-runtime-unroll.patch` adds bounded runtime loop unrolling and read scheduling for A8xx compute shaders; remove it when the pinned upstream includes equivalent changes.
`turnip-ssbo-wide-reads.patch` uses exact-width aligned read-only SSBO accesses with matching descriptor strides; remove it when the pinned upstream includes equivalent changes.

`turnip-a8xx-word-reuse.patch` reuses immutable word loads across byte-wise compute loop iterations on A8xx. This is a local MIT-licensed change; remove it when the pinned upstream includes equivalent changes.
