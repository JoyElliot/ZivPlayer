# ExoPlayer dev.exo4 真机回归

- 日期：2026-11-04；分支 `codex/exo-migration`，基线 `1db64fc`；实现已按功能提交至本地，代码提交 `dcb832a`。
- 最终版本：`0.1.0-dev.exo4` / versionCode `15`，Media3 `1.11.1`。
- 设备：23049RAD8C / marble，Android 15 / API 35；最终回归通过 USB 执行。
- [机器可读收据](2026-11-04-exoplayer-device.json)包含日志/产物/关键源文件哈希、格式诊断和恢复结果。

## 修复与前后对照

1. **Activity 重建时解码器错误 4003。** dev.exo2 在无线和 USB 下均复现；原始异常为 `rendering to non-initialized(obsolete) surface`，解码器 `c2.qti.avc.decoder`。Surface handler 原先排入普通异步命令队列，MediaSession 已确认清除时实际解绑尚未完成，视图先销毁了消费端。现改为主线程当前调用栈完成 attach/detach，服务关闭也在释放会话前解绑；重建后端期间保留新输出请求。最终真实播放重建和暂停重建均通过。
2. **外挂字幕末条中段 seek 后字幕消失。** SRT 末条覆盖 8–11.5 秒，定位到源时间 9.5 秒时，零偏移和 ±1 秒偏移基线均得到空 cues。Media3 1.11.1 默认 lazy 外挂路径未消费准确 cue 结束时长，回退到最后 sample 起点加 10ms，误判 seek 越界。现仅对整文件外挂解析器补有限结束时刻的零时长空样本，保留原 parser 的跨 seek 输出次序、未知结束时刻和内嵌字幕行为。继续使用默认按需读取路径；字幕关闭或未选中时，即使另一外挂 URI 失效也能播放视频。

字幕依据：[DefaultMediaSourceFactory](https://github.com/androidx/media/blob/1.11.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/DefaultMediaSourceFactory.java)、[SubtitleExtractor](https://github.com/androidx/media/blob/1.11.1/libraries/extractor/src/main/java/androidx/media3/extractor/text/SubtitleExtractor.java)、[ProgressiveMediaPeriod](https://github.com/androidx/media/blob/1.11.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/ProgressiveMediaPeriod.java)。最终行为由下述设备回归确认。

另修正三处测试初始化：导航 fixture 补齐播放身份、几何及受控方向；连续播放用例在媒体安装后才暂停，避免自己使请求序列失效；格式矩阵临时使用默认播放选项并在 finally 恢复旧设置，避免把旧 mpv 缩放偏好的提示当作解码失败。没有放宽播放、画面、字幕或导航断言。

## 最终结果

| 验证 | 数量 | 结果 |
| --- | ---: | --- |
| 主机 JVM 单测 | 195 | 全部通过，0 跳过 |
| Android 应用 instrumentation | 65 | 全部通过，0 跳过，270.678 秒 |
| Exo instrumentation | 13 | 全部通过，0 跳过，22.492 秒 |
| Android Lint | — | 0 error、10 warning |
| Debug / AndroidTest / 优化 Preview 构建 | — | 通过 |
| Preview APK 内容、依赖与 16 KiB 对齐校验 | — | 通过 |

最终原始记录在忽略目录 `build/exo-device-20261104/`：`app-final-usb.txt`、`exo-final-lazy-usb.txt`、`host-validation.log`。早期失败日志和原始引擎异常也保留于同目录，不作为最终成功收据。

65 项应用用例包含 UI/交互契约、真实服务播放、轨道切换、外挂 ASS/SRT 导入、停播重播、Activity/Surface 重建、系统音频焦点和九格式矩阵。13 项 Exo 用例同时包含适配器断言与真实 extractor/MediaCodec/AudioTrack 播放，不能把全部 78 项都称为端到端画质或音画同步测量。

连续播放用例从当前视频输出取帧：TextureView 读纹理，SurfaceView 用 PixelCopy。迷你、全屏、折叠、手动 PiP、自动 PiP 各采到 5 个不同像素哈希；展示交接期间 `isPlaying=false && playWhenReady=true` 中断为 0。覆盖旋转、反向拖动、暂停拖动未松手预览、最终精确定位及暂停后 Activity 重建。此次首个拖动预览 182ms、松手到最终位置 179ms，仅是单次功能测试观测，不是性能基准。

音频实际播放用例覆盖 -10 秒、-50ms、+500ms 偏移及暂停、定位、2×/1×切换；字幕覆盖内嵌和外挂的零/正/负偏移、暂停定位、首次定位到末条中段，以及 +60 秒字幕不拖长 12 秒视频片尾。PCM 数值变换另有适配器/单测覆盖；没有外部采集扬声器和屏幕时序。

## 格式矩阵

使用手机上已登记并仍可读取的测试目录；每项均验证实际推进、暂停、定位及诊断。

| 样本 | 结果 / 观测 |
| --- | --- |
| H.264 1080p / 50Mbps + AAC | 通过，`c2.qti.avc.decoder` |
| HEVC Main10 1080p + FLAC | 通过，`c2.qti.hevc.decoder` |
| AV1 1080p + Opus | 通过，`c2.android.av1-dav1d.decoder`，属于软件解码 |
| HEVC 2160p + AAC | 通过，`c2.qti.hevc.decoder` |
| HEVC PQ / HLG Main10 | 两项通过；诊断分别识别 BT.2020/PQ 与 BT.2020/HLG |
| Opus / FLAC / PCM 音频 | 三项通过，无视频轨道 |

PQ/HLG 是片源元数据及解码证据，不能证明显示面板的实际 HDR 输出。未测试 Dolby Vision 样本、物理音画偏差、完整 libass 高级特效、长时间压力与功耗。本轮未恢复已停止的功耗实验。

## 安装与数据恢复

最终优化 Preview 已覆盖安装，冷启动通过，应用随后停止。完整 instrumentation 使用同一源码的 Debug 变体；Preview 仅额外核验安装和启动。

- APK：`apps/android/build/outputs/apk/preview/android-preview.apk`，5,260,177 bytes。
- SHA-256：`a47c46e88e4869b13bcf78317ff717b20bf93e97cc67a8e6629918d42c010346`。
- [APK 校验报告](2026-11-04-exoplayer-exo4-apk.json)：4 个锁定 AndroidX 原生辅助库；不包含 mpv。其 `devicePlaybackEvidence` 字段属于独立打包工具，不自动签发真机收据；设备结果见本记录。
- 测试前备份的数据库、DataStore、受管播放资源共 18 个条目已恢复，逐项文件字节、UID/GID、mode 与原备份一致；恢复数据库 `integrity_check=ok`。
- 系统媒体音量已恢复为 0；旧 `power-fixtures` / `power-results` 未操作，安全设置未修改。

本记录的测试在提交前执行；功能拆分提交未改动已验证的代码，源文件哈希保持一致。当前仅完成本地提交，未推送或发布。
