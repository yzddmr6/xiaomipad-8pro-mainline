# RAM 调试包

`piano-ram-debug` 只用于开发者显式构造的 RAM 启动，不进入根镜像、升级包集或默认依赖。
`tools/build-device-debs.py --ram-debug-only --version VERSION --output DIR` 单独构建它。
包只安装调试素材和一个不自动选择的 dracut 模块，不安装或启用系统服务。
在构建环境显式使用 `dracut --add piano-ram-debug` 才会加入控制台；启动还须指定 `piano.debug=1`。
该控制台没有认证，只能用于受控、串号绑定、限时返回的 USB RAM 实验。

原生显示 RAM 工具须显式传 `build-native-display-initrd.py --ram-debug`。
它把所需服务和脚本放进 initrd，再通过 `/run` 完成临时切根交接，不依赖正式根中的调试服务。
持久系统采集用 `piano-devmode` 的 SSH；`piano-native-display-capture.py` 只生成采集脚本，不提供传输通道。
