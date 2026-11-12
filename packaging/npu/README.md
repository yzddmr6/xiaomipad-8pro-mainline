# 本机 NPU 运行库

安装 `qcom-npu-runtime` 和 `qcom-npu-demo-model` 后，运行 `npu-demo` 即可离线演示。
`npu-demo --prompt '请简述光合作用。'` 可换问题；`--backend cpu|hybrid|npu` 显式选择。
默认 `auto` 在非安全 CDSP 通道可用且预填充至少512 token时，让 NPU 处理提示词、CPU 逐字生成；短提示词用 CPU，避免单次命令加载两份权重的开销。512是保守阈值，不是所有模型的最佳点。
`--check` 只核对前提，不能证明 NPU 推理通过。

混合演示使用两个模型上下文，通过 llama.cpp 的公开状态接口交接一次 KV 缓存，
不会重算整段提示词；报告单独列出迁移时间。双份权重增加内存占用。
演示限单序列、4096 token 上下文、512 token 输出；该策略不强加给其他应用。

`sources.json` 固定源码、SDK、模型和构建依赖的版本、哈希。FastRPC 从开源源码构建为
glibc 库，使用主线驱动接口，只安装 CDSP 库，不启用 root-PD 守护进程。
DSP 计算库也从 llama.cpp 源码构建；Hexagon SDK/编译器仅用于构建，不随包分发。

原厂 DSP shell 与 C++ 库随机型专用 NPU 固件包一起安装，不需要安装时提取。
来源、分区内路径、大小与哈希见 `firmware/npu/source.json`。这些文件属于 Qualcomm
及相关权利方，未取得再分发许可，权利方提出要求时移除。需同时安装通用运行库、
演示模型和本机型 NPU 固件包；固件包缺失时依赖检查会拒绝安装。

当前本地桌面用户通过 uaccess 获得 `/dev/fastrpc-cdsp` 和 `/dev/dma_heap/system` 权限；
无桌面用户须属于标准 render 组。不扩大安全 CDSP 节点和 ADSP 权限。
配套内核必须启用板级 CDSP/FastRPC，带所需模块，根与早期 initramfs 都带对应固件。

构建命令：`python3 tools/build-npu.py --cache CACHE --output NEW_OUTPUT --download`。
去掉 `--download` 即严格离线构建。压缩输入约1.2 GB，构建预留约7 GB；
默认镜像须同时收录三个生成的包。主机依赖列在构建工具的帮助信息中。

---

# Local NPU runtime

`npu-demo` runs the bundled Qwen3 example. `npu-demo --prompt 'Explain photosynthesis briefly.'`
asks a different question. `--backend cpu`, `--backend hybrid`, and `--backend npu` select the
processor explicitly. Auto mode uses NPU prompt processing and CPU generation when the
non-secure compute channel is accessible and prefill has at least 512 tokens; short prompts
use CPU to avoid loading a second model. This is a conservative single-shot policy, not a
universal optimum. `--check` checks prerequisites only.

The hybrid demonstration uses two model contexts and transfers their KV state through
llama.cpp's public state API once. It does not run the prompt twice. Transfer time is reported
separately. Two weight copies increase memory use. The demo supports a single sequence,
a 4096-token context and up to 512 output tokens. It is not a policy for all applications.

The runtime is built from the commits and hashes in sources.json. FastRPC is built for glibc
with the upstream Linux driver interface. Only its CDSP libraries are installed; no root-PD
daemon is enabled. The DSP computation library is built from llama.cpp source with Hexagon SDK.
The SDK/compiler are build tools and are not shipped in these packages. One build-only CMake patch
removes embedded compiler/linker command notes before the upstream SWIV checksum is generated.

Install qcom-npu-runtime, qcom-npu-demo-model and the device-specific NPU firmware package
for an offline demonstration. Stock DSP shells and C++ libraries are shipped in firmware/npu
and installed directly; no on-device extraction is needed. Their stock release, partition paths,
sizes and hashes are recorded in firmware/npu/source.json. No redistribution permission has
been obtained; files will be removed upon request from the rights holder. The runtime depends
on the board firmware provider, so installation refuses a missing firmware package.

Active local desktop users receive uaccess on /dev/fastrpc-cdsp and /dev/dma_heap/system.
Headless users need membership of the standard render group. The secure CDSP node and ADSP
permissions are not expanded. The kernel must enable the board's CDSP and FastRPC nodes,
include their modules, and have the matching CDSP firmware in both root and early initramfs.

Build with `python3 tools/build-npu.py --cache CACHE --output NEW_OUTPUT --download`.
Omit --download for a strictly offline build. Inputs total about 1.2 GB compressed; allow
about 7 GB for the build. All three generated packages are needed in the default image.
