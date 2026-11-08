<!-- 请按 CONTRIBUTING.md 逐项填写；不适用的写“不适用”和理由，提交前删掉这段提示。
Fill in every section (CONTRIBUTING.en.md); write "N/A" with a reason where a section does not apply, and delete this comment. -->

## 问题与改动 / Problem and change

<!-- 解决什么问题（链接 issue），改了什么，用户能看到什么变化 / What problem (link the issue), what changed, what users will see -->

## 范围与基线 / Scope and base

- 主题（一个 PR 一个）/ Topic (one per PR):
- 目标分支与基于的提交 / Target branch and base commit:
- 依赖的其他 PR 与合入顺序 / Dependencies and merge order:
- [ ] 已基于本项目仓库的最新目标分支，检查过完整的最终差异 / Rebased on this project's latest target branch; complete final diff reviewed

## 来源与许可 / Sources and licensing

<!-- 导入的上游提交或文件、原作者、许可、本地修改，NOTICE/SPDX 是否更新；没有导入内容写“无” / Imported commits or files, original authors, licences, local changes, NOTICE/SPDX updates; "none" if nothing was imported -->

## AI 协助 / AI assistance

<!-- 实际使用的工具和模型（如 Claude Code + Claude Opus 5.5）及用在哪些部分，与提交里的 `Co-Authored-By:` 尾注一致；没用写“无” / Tools and models actually used (for example Claude Code with Claude Opus 5.5) and what for, matching the `Co-Authored-By:` trailers of the commits; "none" if none -->

## 验证 / Validation

- 设备 / Device: 型号、屏型（CSOT/BOE）、内存与存储、原厂系统版本 / model, panel, memory and storage, stock build
- 被测版本 / Tested build: 内核版本与提交、集成提交或镜像（可贴 `piano-check --markdown`）/ kernel release and commit, integration commit or image
- 方式 / Method: 离线 / RAM 启动 / 已装系统冷启动 / 升级 — offline / RAM boot / installed cold boot / upgrade
- 命令与结果、脱敏日志 / Commands, results, sanitised logs:
- [ ] 冷启动 / Cold boot
- [ ] `dmesg` 无新增 Oops、BUG、WARNING、hung task / No new Oops, BUG, WARNING or hung task in `dmesg`
- [ ] 挂起与唤醒（墙上时间）/ Suspend and resume (wall clock)
- [ ] 显示与触控生命周期 / Display and touch lifecycle
- [ ] 改动涉及的子系统；可选功能缺失或失败时无关功能不受影响 / The changed subsystem; unrelated functions unaffected when an optional feature is absent or fails

## 没测到或有限制的 / Untested or limited

## 文档 / Documentation

<!-- 中英文是否一起更新，或为什么不需要 / Chinese and English updated together, or why no documentation change is needed -->
