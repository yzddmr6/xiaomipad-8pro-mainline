# Shared Adreno 830 Mesa inputs

`source.json` pins upstream Mesa and the ordered patches used by
both the desktop deb and the gpu-2404 builder. `freedreno-a830v1.patch` imports upstream A830 revision-1 speedbin matching
and exposes the renderer name `Adreno (TM) 830v1`. `piano-fixes.patch` contains
the shared-context publication fix. Contexts must
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

宿主采用 [Ubuntu 回移打包锁](ubuntu/source.json)：Canonical 的 resolute
`26.2.4-1ubuntu1~26.04.1` 打包加上游 26.2.6，按 `source.json` 应用固定补丁集。
`build-host-mesa.py` 保留 Ubuntu arm64 驱动集、GLVND、编译加固与测试。
产物为发行版同名包，安装到 `/usr/lib/aarch64-linux-gnu`；EGL 和 Vulkan
沿用发行版注册目录。`piano-mesa` 仅保留过渡依赖与自动生成的精确版本策略。
标准路径下运行上述压力测试时，直接执行 `./shared-context-stress Adreno`。

`libdrm` 使用锁文件指定的 Ubuntu 原样二进制，不重建、不钉版本。
`tools/fetch-host-mesa.py` 下载并校验全部固定输入；在独立 ARM64 resolute
根中执行 `build-host-mesa.py prepare` 和 `build-host-mesa.py build`。
完整调用见 [BUILD](https://github.com/yzddmr6/xiaomipad-8pro-mainline/blob/main/docs/BUILD.md)。构建根须有
`/etc/mesa-disposable-build-root`，不能在已安装的桌面系统中直接运行构建动作。

`tools/package-mesa.py --mesa-build BUILD --output OUTPUT --version VERSION
--release-id BATCH --ubuntu-inputs INPUTS` 生成过渡包及版本化本地 APT 仓库。
整组同源二进制保留在仓库中，包括后装的开发包和 OpenCL 包；默认桌面仅安装
运行时子集。包名单由 `debian/control`、原生构建选中的二进制及实际产物核对生成，
Mesa 精确版本优先级 1001，其他版本 -1。更新前必须整组重建，退出本机补丁后
发布同时撤下 pin 和精确依赖的过渡包，或让桌面包直接依赖发行版运行库；版本排序本身不绕过精确钉版本。发布检查须比较 Ubuntu 的基线更新。

安装与整版回退使用 [UPGRADE](https://github.com/yzddmr6/xiaomipad-8pro-mainline/blob/main/docs/UPGRADE.md) 中的批次命令。
过渡包撤除自己旧版本拥有的 `/opt` 文件和注册项；不删除其他包或管理员拥有的文件。
Snap provider 使用独立运行时和既有配方，宿主升级不改变它。

Remove the local shared-context change when the pinned upstream contains an
equivalent fix and the regression passes. No upstream submission is implied.

Mesa 26.2.6 already includes the gen8 TEX_LINE_OFFSET fix, so no separate
backport is applied. `freedreno-explicit-sync.patch` is the unmodified upstream
commit 1f9c4d529f4cfb332f5abfabbd0918f5d5f6cb4e by Sahitya Kandru:
only an incoming fence selects explicit synchronization; requesting an outgoing
fence alone must not suppress implicit synchronization.

`freedreno-thread-safe-tex-seqno.patch` retains the OnePlus project's texture
sequence allocator fix, including its original attribution. A830 revision-1
speedbin matching comes from upstream; the local shared-context publication
fix remains necessary.

宿主包使用 Ubuntu 的 `debian/rules`。`build-mesa.py`、`build-mesa.sh` 与
`prepare-mesa-initramfs.py` 的隔离前缀只用于既有诊断配方和 snap 构建，
不再决定宿主产品的安装目录。OnePlus 使用同一组宿主 Mesa 二进制，
通过 `erhai-mesa` 过渡包迁移；来源仓库提交和文件哈希由下游导入记录保存。

`turnip-a8xx-runtime-unroll.patch` adds bounded runtime loop unrolling and read scheduling for A8xx compute shaders; remove it when the pinned upstream includes equivalent changes.
`turnip-ssbo-wide-reads.patch` uses exact-width aligned read-only SSBO accesses with matching descriptor strides; remove it when the pinned upstream includes equivalent changes.

`turnip-a8xx-word-reuse.patch` reuses immutable word loads across byte-wise compute loop iterations on A8xx. This is a local MIT-licensed change; remove it when the pinned upstream includes equivalent changes.
