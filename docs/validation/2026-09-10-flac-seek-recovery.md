# 2026-09-10：ExoPlayer FLAC 跳转后持续加载修复

## 现场与复现

设备为 23049RAD8C、Android 15。用户播放一个 HEVC Main10、1920×1080、FLAC 立体声 48 kHz 的 MKV 时，跳转后持续加载，返回首页再进入全屏显示：

> The playback backend could not finish the seek before the deadline.

原实验 APK 的 SHA-256 为 `6be4d168db66aa89ebb120f0c647a2e3c29e7493611ff482f85c53b3af48668f`。现场 MediaSession 为 ERROR，位置 22,193 ms、缓冲位置 40,999 ms。核心在后端 seek 方法返回后继续等待 `SeekCompleted`，10 秒内未收到匹配事件即进入 RESET 错误；重新创建全屏 Surface 只会再次显示这个错误。

本次报错文件与 [9 月 9 日基准实验](2026-09-09-exoplayer-libass-comparison.md)的片源不同。首次使用旧缓存的检查未复现；随后核对当前截图文件名、数据库来源和 22,193 ms 检查点，确认本次文件后复现了同一超时。片源 URI、文件名、数据库导出和截图只保存在忽略目录，不写入仓库。

## 证据与原因

普通 EXACT seek 从 0 到 22,193 ms 已足以稳定触发故障，并不需要连续拖动或重新全屏：

- 视频目标帧 PTS 为 22,231 ms，`pendingSeekHasFrame=true`；视频和 ASS renderer 均 ready。
- ExoPlayer 持续处于 BUFFERING，已经缓冲到约 48–50 秒且停止继续加载。
- 唯一未就绪的已启用 renderer 为 `MediaCodecAudioRenderer`，codec 是 `c2.android.flac.decoder`。
- 诊断计数为 342 个输入、335 个已处理输出，但 AudioSink 的 `submittedPcmBytes` 和 `writtenPcmBytes` 都为 0。这些输出计数不等于存在有效 PCM。

因此本次阻塞发生在 FLAC 解码器刷新后的音频输出路径。没有放宽视频帧判断、延长超时或把 BUFFERING 当作跳转成功。最初的“同位置 seek 被省略”假设不作为本次根因。

## 修改

[ExoRenderersFactory](../../platform/libmpv-android/src/main/kotlin/io/github/joyelliot/zivplayer/platform/exoplayer/ExoRenderersFactory.kt)保留默认 renderer 顺序、AudioSink、codec factory、解码器选择及 fallback。对 `c2.android.flac.decoder`，通过 `shouldReleaseCodecInsteadOfFlushing()` 在需要刷新时释放并重新初始化 codec，使初始化数据重新送入解码器；其他 codec 沿用父类策略。

这是 Media3 1.11 的受保护扩展点：[MediaCodecRenderer](https://github.com/androidx/media/blob/1.11.0/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/mediacodec/MediaCodecRenderer.java#L955-L1014)先调用该 hook，释放后执行重新初始化；[MediaCodecAudioRenderer](https://github.com/androidx/media/blob/1.11.0/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/audio/MediaCodecAudioRenderer.java#L718-L730)仍执行原有 AudioSink flush 和状态清理。生产实现没有反射上游私有状态。

## 验证结果

同一设备、同一实际报错文件，原 APK 的普通 seek 多次失败，修复后通过。最终安装 APK SHA-256 已从手机验证为：

`a079fadb28ac3439d5f9df6dc853381ffffa1c76a89fc2b7f56817582d7c8f3f`

| 检查 | 结果 |
| --- | --- |
| Debug APK / AndroidTest APK 构建 | 通过；沿用约 2 GiB 的项目 JVM 预算 |
| 原生适配模块 Lint | 通过 |
| 实验 APK gate | 28 个锁定 native 库、原 C++ runtime、16 KiB 对齐、16+19 JNI 方法通过 |
| 新增 `ExoSeekCompletionDeviceTest` | 通过：普通跳转、预览停稳后提交同一位置、暂停定位、暂停同位置提交、返回首页再全屏、续播 |
| 最终音频证据 | renderer ready，AudioSink 写入 349,440 字节 PCM；测试对此作显式断言 |
| `NativePlaybackSmokeTest` | 3 项通过：多轨、字幕、重播、Surface 重建，真实瞬时音频焦点及拖动期间焦点丢失 |
| ASS 像素与 `SeekPreviewDeviceTest` | 各 1 项通过：字幕绘制/关闭、真实视频帧定位和拖动 |
| 当前文件全片大跨度拖动 | 1 项通过：约 18 / 22 次更新每秒，反向最长间隔约 91 ms，松手至续播约 397 ms |

以上为 7 项最终设备用例。新增暂停提交用例最初过早读取了 MediaController 的乐观状态，在服务处理完提交前断言抑制已解除；最终改为同时等待后端 READY、在途 seek 清除、精确位置/帧和控制器抑制解除，没有减少这些完成条件。原始失败日志保留。

大跨度更新率来自单次 2 秒方向扫描的像素变化，不能当作持续播放 fps 或所有素材的保证。修复会增加该 FLAC codec 的重新初始化成本；当前文件及既有测试片源通过，不代表全部 FLAC 文件和 Android 版本已验证。用户休息期间未要求其手动操作，主观听感与手感未作人工确认。

## 数据与产物

测试使用未注册的临时媒体 ID。前后 SQLite 完整性检查通过，49 条个人记录及检查点、3 个目录、254 项索引、5 个资源及文件、选轨/外挂字幕关联和设置均保持不变；仅 AndroidX `files/profileInstalled` 安装标记变化。私人片源临时配置已删除。

忽略目录 `native/out/exoplayer-seek-error-20260909/` 保留原始日志、JSON、数据核对结果、`exoplayer-flac-fixed.apk` 和最终测试 APK。原始错误证据为 `screen.png`、`media-session.txt`；关键前后证据为 `repro-audio-probe.json`、`final-flac-regression.json`、`final-current-wide.json`、`data-preservation.json`。原 libmpv 回退包仍位于 `native/out/exoplayer-libass-20260909/libmpv-baseline.apk`。

这些修改保留在实验分支工作区，没有新增提交或推送。
