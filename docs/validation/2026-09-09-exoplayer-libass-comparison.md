# 2026-09-09：ExoPlayer + libass 对比实验

后续在另一份实际播放文件中发现的 FLAC seek 超时及修复，见 [2026-09-10 验证记录](2026-09-10-flac-seek-recovery.md)。本文 APK 哈希与性能数据保留为当时实验的结果。

## 对比基线与方法

- 原改动已提交到 `main`：`***`，提交说明“优化实时拖动预览并整理跨平台架构文档”。没有推送。
- 实验分支：`codex/exoplayer-libass-comparison`；实现边界见 [ADR 0017](../adr/0017-exoplayer-libass-comparison.md)。
- 设备：23049RAD8C，Android 15，arm64；同一台设备覆盖安装，保留应用数据。
- 私人动画：1920×1080 HEVC Main10，约 23.976 fps，FLAC，时长 1,481,021 毫秒（约 24 分 41 秒）。片源经已授权 SAF 缓存读取，URI 不进入仓库。
- 基线 APK SHA-256：`57d9cad34a70703c8c041a2fb91f13f605d8ebb81e0a6f5f0cea465b6da92a47`。

`PersonalScrubBenchmarkTest` 使用 ViewModel 的正式 preview 入口，连续 2 秒从时长 10% 扫至 90%，再用 2 秒反向扫回，最后松手到中点。与旧版本使用完全相同的测试 APK 和片源。另测固定 500–520 秒区间，避免只测极端扫描而漏掉普通拖动。

最终测量对旧实现直接采样 ImageView 当前绑定的预览 Bitmap，对新实现通过 PixelCopy 读取主 Surface 生产缓冲。最初的窗口 PixelCopy 会包含控件重绘，其计数不用于最终帧率对比。图像指纹变化仍只表示有新像素，不等于目标时间已呈现。旧实现报告位图对应的预览状态时间；ExoPlayer 报告最近的解码器释放 PTS，两者都有采样时序误差，不能作为光学呈现时间。自有 90 秒条码素材通过 `ContinuousScrubDeviceTest` 注入真实 MotionEvent，从 Surface 像素解码时间码，检查正反向拖动、松手精确帧及后续 +5 秒播放。

## 实验中发现并处理的问题

1. 原 480 像素 SDR 位图预览损失拖动时的清晰度。早期大跨度正向窗口采样中，虽然覆盖层像素变化达到 31.5 次/秒，预览状态时间相对目标的中位误差仍接近 100 秒。窗口采样又会混入控件重绘，该指标不能解释为视频持续跟上手指，最终比较已改用真实 Bitmap 像素。
2. ExoPlayer 改为直接输出原视频，实际检查到该动画的 Surface 缓冲为 1920×1080。
3. ASS 轨道“选中”不代表绘制完成。暂停时字幕数据晚于 READY 到达，时钟不再变化；增加同时间重绘后，真正的 libass 图层有字形像素，关闭后变为全透明。
4. 原视频缓冲和字幕显示矩形必须分别管理尺寸，否则字幕偏小、偏移。增加底部居中位置及白色字形像素断言。
5. 外挂字幕重建 Media3 TrackGroup，原先保存的音轨 override 会失效；显式字幕关闭也可能被迟到的自动选择覆盖。现在在新组出现后重新绑定，并使显式选择取消待执行的自动选择。
6. Activity 重建原先稳定触发 Qualcomm 解码器 `rendering to non-initialized(obsolete) surface`；销毁 Surface 前同步解除输出后，该重播/重建组合测试通过。
7. 只用最近关键帧会让普通拖动在长 GOP 素材上长时间显示同一帧：首次真实手势只有正向 5、反向 3 帧/秒。改成精确定位后约为 14.5/14.5，但全片扫描变慢。因此最终实现使用拖动速度选择精确帧或最近关键帧；松手始终精确定位。原性能断言保持正向 10、反向 15 帧/秒，并移到功能检查之后，以便失败时仍保留松手与后续播放证据。

## 最终动画对比

以下使用相同最终测试 APK，先覆盖安装已提交的原 APK，再安装最终实验 APK。每个方向采样 2 秒；更新率包含窗口起始帧，不能解释为持续播放 fps。单次测量不代表所有片源或设备。

| 范围 / 指标 | libmpv 基线的位图预览 | ExoPlayer + libass 实验 |
| --- | ---: | ---: |
| 预览视频分辨率 | 约 480×270 SDR Bitmap | 原视频 1920×1080 Surface |
| 500–520 秒正向更新率 | 41.5 次/秒 | 8.5 次/秒 |
| 520–500 秒反向更新率 | 34.5 次/秒 | 9.0 次/秒 |
| 上述正向 / 反向最长更新间隔 | 138 / 260 ms | 384 / 365 ms |
| 短程松手至续播 | 266 ms | 288 ms |
| 全片 10%→90% 正向更新率 | 38.0 次/秒 | 18.0 次/秒 |
| 全片 90%→10% 反向更新率 | 0.5 次/秒（仅起始帧） | 22.5 次/秒 |
| 全片正向 / 反向最长更新间隔 | 168 / 2,000 ms | 172 / 84 ms |
| 全片松手至续播 | 367 ms | 323 ms |

极端正向扫描中，预览状态/释放 PTS 相对采样目标的中位差值为基线约 87.7 秒、实验约 54.8 秒；该手势每个真实秒跨越约 592 个视频秒，54.8 秒的视频位置差对应约 93 毫秒的扫描时间。它不是松手后的误差，也不是直接测得的端到端显示延迟。基线反向只有一个样本，中位误差缺乏统计意义。部分初始样本仍是旧帧，因此最大误差不作为稳定跟随能力指标。

结论：本次全片反向扫描的 2 秒冻结已在实验版中消失，拖动视频也不再降为 480 像素位图。代价是普通范围拖动的更新率下降；不能据此判定 ExoPlayer 全面优于现有方案。下一步是否采用应由实际观看和更多片源结果决定。

实验 APK SHA-256：`6be4d168db66aa89ebb120f0c647a2e3c29e7493611ff482f85c53b3af48668f`。

原始数据：`libmpv-final-short.json`、`libmpv-final-wide.json`、`exo-final-short.json`、`exo-final-wide.json`。这四项设备用例均返回 `OK (1 test)`，均检查拖动时保留 `playWhenReady`、实际暂停及 SCRUBBING 抑制原因，并验证松手后位置继续前进。

## 已验证的字幕和原生边界

- 自有 ASS 测试在暂停 2 秒时检查真实透明图层：33,214 个有效 alpha 像素，关闭字幕后 0；归一化字形中心约 `(0.500, 0.875)`，符合素材的底部居中布局，白色字形像素 1,876 个。
- 暂停定位到 5 秒后，黄色 ASS 字形像素 10,054 个，截图人工检查可见中文、颜色覆盖、描边和双行布局。此结果覆盖该素材及当前字体环境，不代表任意内嵌字体或全部 ASS 特效已验证。
- 进程从 APK 直接映射 native 库，`/proc/self/maps` 不显示单个 `.so` 名称。将可执行映射偏移 `0x1178000`、`0x1460000` 与当时 APK 的 ZIP 条目对应，得到 `libass.so` 和 `libasskt.so`，没有 libmpv 的可执行映射。不能用空的文件名列表声称 native 库未加载。
- `NativePlaybackSmokeTest#sourceNativePlaybackTracksSubtitlesReplayAndSurfaceRecreation` 已覆盖播放、暂停、精确定位、双音轨、内嵌 SRT、外挂 ASS/SRT、字幕关闭、stop/replay、倍率和 Activity/Surface 重建。
- 严格默认 APK gate 仍拒绝包含额外 libass 库的实验 APK；实验 gate 仍拒绝只有原依赖的基线 APK。实验 gate 保留原 staging receipt、20 个 native 文件和 C++ runtime 的字节约束。

本次不等于所有 ASS 特效、内嵌字体 MIME 变体、HDR 显示、任意编解码格式或正式发布已通过。原始日志、JSON、截图保存在忽略目录 `native/out/exoplayer-libass-20260909/`。

## 最终回归与数据保留

| 验证 | 结果与范围 |
| --- | --- |
| Debug APK / AndroidTest APK 构建 | 通过；最终安装 APK 与上文 SHA-256 一致 |
| JVM 测试 | 173 项通过：核心运行时 42、libmpv/Android 适配 71、播放服务 50、应用 10 |
| Lint | 应用、播放服务和原生适配模块通过；最终模块报告无 StaticFieldLeak 警告 |
| APK gate | 28 个锁定 native 库、原 C++ runtime 字节、16 KiB、16+19 JNI 方法通过 |
| `SeekPreviewDeviceTest` | 1 项通过：精确帧、拖动、滑条、取消、过期请求、暂停/失焦、控制器断连、外部 seek、提交时重建 Surface |
| `PlaybackInteractionTest` | 4 项通过：请求竞争、队列/循环/暂停跳转、临时倍率、队列编辑与释放 |
| Native smoke + ASS 像素 | 2 项通过：多轨、外挂字幕、重播、Surface 重建、中文 ASS、暂停绘制及关闭清屏 |
| 条码真实手势 | 播放中和暂停中各 1 项通过，仍使用原正向 ≥10 / 反向 ≥15 fps 门槛 |
| 私人动画对比 | 最终实验版短程/全片各 1 项通过，基线相同两项也通过；性能差异见上表 |

最终条码素材的正向/反向为播放中 13.5/15 fps、暂停中 12.5/15 fps；暂停松手精确回到 20,000 毫秒，播放意图分别正确保留。两项随后 +5 秒定位均采集到 36 个不同视频帧并继续播放。该素材是自有 AVC 条码视频，不能把其帧率套用到上面的 HEVC 动画。此前纯精确定位试验出现过反向 14.5 fps 未达到门槛，原始失败日志保留；最终通过不表示所有重复运行都保证相同帧率。

`SeekPreviewDeviceTest` 最初把“ImageView 可见”作为预览存在的必要条件，因新路径直接输出 Surface 而失败。最终仅将生命周期判断改为覆盖层或 SCRUBBING 状态，视频帧断言仍读取真正的 Surface 像素，没有删除精确帧检查。

测试后停止测试播放并冷启动应用；SQLite 完整性检查通过。与测试前快照比较，49 条个人播放记录及检查点、个人选轨和外挂字幕关联、3 个目录、254 项目录索引、5 个原有资源及其文件、用户设置均保持不变。仅 AndroidX `files/profileInstalled` 随覆盖安装发生变化，属于运行时安装标记。结果见 `data-preservation.json`。私人片源临时缓存已经删除，设备已安装最终实验版。

## 构建 JDK 内存预算

根据后续要求，将本项目的 Java 构建开销目标调整为约 2 GiB。调整前，空闲 Gradle 和 Kotlin 守护进程合计 Working Set 约 3,110 MiB，两者都使用 `-Xmx4g`；该数值为当时快照，不是旧构建峰值。

最终配置为 Gradle 堆 896 MiB、Metaspace 上限 768 MiB、代码缓存 128 MiB、G1 GC、JVM 可用处理器数 4、单 worker、关闭项目并行，守护进程空闲 10 分钟退出。根项目和独立的 `build-logic` 均设置 `kotlin.compiler.execution.strategy=in-process`；只设置根项目不会覆盖 included build，第一轮仍有额外 Kotlin 守护进程，峰值达 2,745.94 MiB，因此未采用该中间配置。

停止旧守护进程后，使用最终配置执行：

```powershell
.\gradlew.bat --offline --console=plain --rerun-tasks --no-build-cache `
  :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest
```

214 个任务全部实际执行并通过，Gradle 报告耗时 1 分 21 秒。每 500 ms 采样本机 `java/javaw` 进程合计内存，Working Set 峰值 **2,142.83 MiB（约 2.09 GiB）**，Private Bytes 峰值 **2,232.56 MiB（约 2.18 GiB）**。结束后仅余 Gradle 守护进程，没有 Kotlin 守护进程。原始证据为忽略目录中的 `jdk-2g-build.log`、`jdk-2g-memory-samples.json` 和 `jdk-2g-memory-summary.json`。

这个峰值只覆盖上述 APK 和测试 APK 的强制重编译，不是操作系统级硬限额，也不表示独立 Test/Lint JVM 在任何任务组合下都保证该峰值。手机仍使用已完成上文真机验证的 `exoplayer-final.apk`，SHA-256 仍为 `6be4d168db66aa89ebb120f0c647a2e3c29e7493611ff482f85c53b3af48668f`；内存验证未再次覆盖安装手机。

## 复现与回退

构建与校验：

```powershell
.\gradlew.bat --offline :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk --exoplayer-experiment
```

测试使用新生成、未注册的媒体 ID，先后检查它不在个人最近记录中。私人片源临时配置为应用缓存 `seek-benchmark-source.json`，测试结束移除；不要把个人 URI、数据导出或测试截图加入 Git。

原 APK 已保存于忽略目录，覆盖安装可立即回退播放实现，保留应用数据：

```powershell
adb install -r native/out/exoplayer-libass-20260909/libmpv-baseline.apk
```

要同时回退源码，应先保存或提交实验分支的未提交修改，再切回 `main`；不要使用 `reset --hard` 或清理整个工作区。`-PplaybackEngine=libmpv` 仅选择本实验分支的后端，APK 仍包含实验依赖，不等同于原基线 APK。
