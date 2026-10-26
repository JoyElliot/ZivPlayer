# 三核心功耗测试与历史记录归档

归档分支：`codex/archive-threecore-power-20261026`。

本分支保留 libmpv、ExoPlayer、libVLC 播放实验的最终源码、测试工具和历史证据，供回溯与复核。主开发分支 `main` 继续在独立 worktree 中开发；本次归档没有合并实验代码到 `main`。

## 分支与源码来源

- 原实验分支：`codex/libvlc4-comparison`，起点 `***`。
- 更早的 Exo/libass 实验：`***`，属于当前分支祖先。
- 归档开始时 `main`：`***`。
- 归档保存了实验目录此前未提交的播放实现、依赖锁、原生构建策略、设备测试、功耗 runner、恢复守护及统计脚本，按模块分别提交。
- 本分支源码是实验结束后的工作树快照。每一夜的实际仪器身份以该夜的冻结脚本、源码差异、APK SHA 和 cohort 为准，不能把工作树中的后续修改倒推为早期仪器。

原有实验分支和历史提交继续保留。归档不是一次新的功能、构建或手机验收。

## 最终有限计划的覆盖

最终矩阵已完成 **12/12 项两两对比、24/24 个完整四腿块、96 条完整块正式腿**。覆盖计数来自以下三个独立会话：

| 会话 | 完整比较项 | 完整四腿块 | 完整块正式腿 | 说明 |
| --- | ---: | ---: | ---: | --- |
| [2026-10-23](results/power-threecore-20261023/completion-summary.json) | 3 | 6 | 24 | 另有不完整块，未计入均值 |
| [2026-10-25](results/power-threecore-20261025/completion-summary.json) | 6 | 12 | 48 | 用户选定检查点后停止并恢复；失败 AVC 原档保留 |
| [2026-10-26](results/power-threecore-20261026/completion-summary.json) | 3 | 6 | 24 | 完成 AVC、HEVC、VP9 的 mpv/VLC 剩余比较 |
| 合计，仅覆盖计数 | 12 | 24 | 96 | 不跨夜合并功耗 |

2026-10-24 会话在启动阶段中止并恢复，没有正式测量。更早的 9 月、10/18、10/19、10/22 数据作为独立历史保留，不加进上述最终矩阵的功耗或重复数。

10/26 会话最后 runner 于台北时间 03:46:35 完成，03:47:47 手机恢复终验通过，随后删除该会话自动巡检。该结束状态不构成重新启动旧脚本的指令。

## 报告入口

- [三核心直接比较总览](results/power-threecore-summary-20261026/summary.html)：按编码与两两核心列出 W、mW 差值、分层和小样本边界。
- [总览结构化数据](results/power-threecore-summary-20261026/pairwise-comparisons.json)。
- [10/23 中文报告](results/power-threecore-20261023/morning-report.html)。
- [10/25 中文报告](results/power-threecore-20261025/morning-report.html)。
- [10/26 中文报告与恢复结果](results/power-threecore-20261026/morning-report.html)。
- [归档清单](manifest.json)：逐报告公开脱敏副本 SHA、压缩包 SHA、包内成员 SHA 和本机留存原始归档的索引。
- [本机原始设备归档索引](retained-device-evidence.jsonl.gz)：相对路径、字节数和 SHA-256；对应大文件仍在 `native/out/`。

`results/` 中报告保留原目录层级和研究结果，设备身份及个人恢复信息已脱敏。报告中部分主机绝对路径和原始文件链接仍指向本机证据；它们不是远端文件地址。压缩包内路径和 `manifest.json` 可用于查找其归档副本。

归档专用 `.gitattributes` 禁止换行转换；清单 SHA 对应公开脱敏副本，不代表本机原始证据字节。可公开的现存报告依赖已补齐；有意留本机或在原始快照中已不存在的相对链接，记录于 `manifest.json` 的 `localOnlyReportLinks`。

## 统计与解释边界

- 完整 ABBA/BAAB 四腿块才是重复单位；1 Hz 采样点不是独立重复。
- 只在同会话、APK、fixture、协议、cohort、实际解码器、电量类别、起始 10% 电量层及 1°C 温度层内合并。
- 最终 24 块中有 15 块满足单块同层要求。严格比较表的 12 条分层记录对应 11 个不同核心/编码对；HEVC Exo/VLC 的两个完整块均跨层，没有严格同层估计。
- AVC mpv/VLC 的两个同层块差异方向相反；AVC Exo/VLC 的两个不同层结果也反向。不能用简单平均认定稳定胜负。
- 各核心的跨块原始描述均值未校正温度或电量，不替代严格配对结果；预检和不完整块不填入正式均值。
- 测量为受控条件下的整机功耗，包括常亮屏幕与受控后台的影响；不等于播放器进程独占功耗或日常续航，也不做猜测扣除。
- 功耗、温度、CPU、视频掉帧、音频计数和主观画质分别解释。HEVC10 通过不等于 HDR 或杜比视界验收；frame hash 不证明主观画质。
- 历史跳变、包状态漂移与依赖升级之间的因果关系未得到证明，原失败和修复证据保留，不作功耗修正。

## 历史说明

以下文档保留当时的协议、失败、纠正与验收边界：

- [Exo/libass 实验](../../validation/2026-09-09-exoplayer-libass-comparison.md)。
- [9/10 功耗对比](../../validation/2026-09-10-playback-power-comparison.md)、[9/12 功耗对比](../../validation/2026-09-12-playback-power-comparison.md)。
- [9/13 三核心记录](../../validation/2026-09-13-playback-power-comparison.md)及其[证据范围纠正](../../validation/2026-09-13-power-evidence-scope-correction.md)。
- [画面与字幕修复记录](../../validation/2026-09-13-video-clarity-fix.md)、[三引擎独立审查](../../validation/2026-09-20-three-engine-audit.md)。
- [9/21 夜间测量](../../validation/2026-09-21-hardware-power-night.md)、[9/22 续测](../../validation/2026-09-22-power-continuation.md)。
- [9/26 综合记录](../../validation/2026-09-26-power-data-synthesis.md)与[重复性记录](../../validation/2026-09-26-power-repeatability.md)。
- [9/27 收尾](../../validation/2026-09-27-power-completion.md)、[9/28 重试](../../validation/2026-09-28-power-retry.md)、[9/29 结束](../../validation/2026-09-29-power-finish.md)。
- [10/17 依赖与功能验证](../../validation/2026-10-17-dependency-upgrade.md)：功能验证与功耗结论分开。

## Git 内与本机留存的范围

Git 中保存：

1. 已有源码、测试、控制器、恢复守护、依赖和构建身份快照。
2. 报告、分层统计、限制说明、恢复终验摘要和逐阶段审计。
3. `bundles/*.tar.gz` 内的逐腿分析、旧格式遥测 JSON/JSONL、阶段计划与审计、冻结测量工具及冻结源码差异。预检、失败与重试仍保留原标签，不自动视为正式合格数据。
4. 对大设备归档的相对路径、大小和 SHA-256 索引。

以下原件仍留本机，没有加入 Git：手机用户数据与原 APK 备份、安装包、截图/录屏、测试视频、大型原始设备 tar、编译缓存。恢复手机依赖的原始备份不会因本次 Git 归档被移动或删除。`bundles` 只含定量记录与冻结工具，不能替代整套本机原始证据。

本机完整证据位于 `F:\Git\ZivPlayer\native\out`（WSL：`/mnt/f/git/zivplayer/native/out`）。删除或清理该目录前应另行迁移这些本机留存原件。

## 离线核对

本归档的只读校验工具为 [`tools/archive-threecore-history.py`](../../../tools/archive-threecore-history.py)。它只校验公开副本；原始数据导出功能已禁用，避免将本机未脱敏数据重新发布。

使用 `manifest.json` 中的 SHA 校验报告与压缩包；解压到临时目录后逐成员核对。原始 tar 使用 `retained-device-evidence.jsonl.gz` 中的 SHA 与本机文件比对。

历史 runner、guard 和 prepare 保留流程与截止逻辑，设备连接配置及受控应用标识已替换为占位符，不能直接操作设备。需要重新实验时，应新建会话、基线和身份，并重新审查截止协议；不复用旧会话的 pending 或恢复状态。

## 隐私与证据边界

设备连接标识、SSID 和无分析价值的会话身份字段已移除；有审计价值的应用身份使用稳定匿名编号，个人恢复状态用 `***` 表示。网络流量观测、功耗、温度、电量、CPU、掉帧、实际解码器、APK/fixture/protocol/cohort 和比较结论保留。清单摘要覆盖脱敏后的公开文件；本机原始证据保留在忽略目录，不从公开内容反推个人设置。冻结仪器原始 SHA 仅用于历史身份说明，公开脚本副本已脱敏，不再逐字等于原仪器。
