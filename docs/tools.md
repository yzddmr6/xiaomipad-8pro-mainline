# AOSP boot 工具

运行 `sh tools/fetch-aosp-mkbootimg.sh`，将 AOSP mkbootimg 固定检出至 tools/local/aosp-mkbootimg。
源码 commit 为 954bc3ead5e679005fddf3484d247f2557b3c2c9，
关联 android-16.0.0_r4；入口脚本额外使用固定 SHA-256 核对。

固定版本和验证方式源自 liuqin。Gitiles 动态 tar.gz 的归档字节可能变化，
因此使用 Git 对象身份校验，不依赖动态归档的 hash。
检出有修改时拒绝改变它，避免覆盖开发者修改。

主机检查覆盖首次 Git 获取、内容校验、两个 Python CLI 帮助入口及缓存复用，
不代表 piano 的 boot 格式、AVB、DTB 或 ABL 已通过设备验证。

原始脚本的 MIT 声明见 [NOTICE](../NOTICE) 和 [原许可证](../LICENSES/liuqin-MIT.txt)。

