# ExoPlayer 迁移：dev.exo2 主机验证记录


这是 versionCode 13 的历史主机收据。后续修复与真机结果见 [dev.exo4 真机回归](2026-11-04-exoplayer-device.md)。

- 日期：2026-11-04
- 分支：`codex/exo-migration`；基线：`1db64fc`；本记录对应本地未提交实现。
- 版本：`0.1.0-dev.exo2`，versionCode `13`，Media3 `1.11.1`。
- 决策：[ADR 0024](../adr/0024-exoplayer-playback.md)。
- 边界：未使用 ADB、安装、启动或操作手机；以下结果不代表真实播放、字幕视觉、HDR/Dolby Vision、功耗或长时间稳定性已验收。

## 实现范围

Exo 单项后端接入既有 core 队列与 Media3 会话适配层。保留历史/续播、轨道选择、定位合并、临时倍速、音频焦点、迷你播放器、全屏、旋转和 PiP 的控制路径。稳定且与窗口对齐的全屏/PiP 选择 SurfaceView；动画与圆角展示保留 TextureView，同一时刻连接一个视频输出。

字幕通过独立 CueGroup/SubtitleView 输出，保留基础样式和导入字体。音频偏移 ±10 秒、内嵌/外挂字幕偏移 ±60 秒已接入。新增解码器初始化回退、无缝帧率匹配、跳过静音、优先音轨/字幕语言设置，手动与单媒体记住的轨道优先。旧轨道指纹兼容已知 mpv/Exo 编码名称差异。高级 ASS 特效、字体附件及 mpv 专属视频处理仍有迁移边界，旧设置保持存储兼容。

独立静态审查发现并修复了 READY 状态定位到片尾后进度采样停止、Surface A→B→A 迟到首帧误命中新目标、自适应轨道选择锁定单个码率、旧 codec 指纹无法恢复及旧诊断/设备测试预期等问题。这些静态核验不代替设备验证。

同步功能的独立审查另修复了负音频偏移的加载互锁、小幅负偏移的 Media3 buffer 参数校验、正音频偏移下 encoder priming 泄漏、AudioTrack stall 恢复时过严的消费断言，以及延后字幕拖长片尾的问题。字幕独立源只缓存所选轨道；有额外解封装/I/O，但无第二个视频解码器。音频非零偏移使用 PCM 输出并暂停跳过静音，默认零偏移保留原生音频路径。

## 主机结果

Windows Temurin JDK 21.0.11、Android SDK API 37 / Build Tools 37.0.0、Gradle 9.8.2。最终验证启用依赖锁与 SHA-256 校验，使用离线缓存，未放宽校验策略。

| 模块 | JVM 单测 | 结果 |
| --- | ---: | --- |
| core:media-api | 10 | 通过 |
| core:player-runtime | 34 | 通过 |
| platform:exoplayer-android | 21 | 通过 |
| platform:playback-android | 51 | 通过 |
| apps:android | 56 | 通过 |
| data:media-android | 23 | 通过 |
| 合计 | 195 | 0 失败 / 0 跳过 |

- `:apps:android:lintDebug` 通过：0 error、10 warning；Preview 的 lintVital 也通过。
- `:apps:android:assemblePreview` 通过（R8 与资源压缩、debug 签名、非 debuggable）。
- `:apps:android:assembleDebugAndroidTest` 与 `:platform:exoplayer-android:assembleDebugAndroidTest` 通过；仅编译/打包，未执行设备测试。
- instrumentation 新增音频倍速/暂停与 seek 生命周期、字幕 PTS/绝对格式偏移、PEEK 阶段片尾裁剪用例；已编译，待设备执行。
- `git diff --check`、APK 校验脚本语法检查通过。

本机日志保留在忽略目录 `build/exo-features/final-validation.log`。主要 Gradle 任务：

```text
:core:media-api:test :core:player-runtime:test
:data:media-android:testDebugUnitTest :feature:settings:testDebugUnitTest
:platform:exoplayer-android:testDebugUnitTest :platform:playback-android:testDebugUnitTest
:apps:android:testDebugUnitTest :apps:android:lintDebug :apps:android:assemblePreview
:apps:android:assembleDebugAndroidTest :platform:exoplayer-android:assembleDebugAndroidTest
```

## APK 核验

- 路径：`apps/android/build/outputs/apk/preview/android-preview.apk`
- 大小：5,260,177 bytes。
- SHA-256：`681d156090bf7e243266fb64a521de678f9050ebdf647ba5ed1cd0d6863800e5`
- [机器可读报告](2026-11-04-exoplayer-apk.json)：匹配 Exo 1.11.1 诊断标记、应用/服务入口、目录版本与锁文件；未包含旧 mpv 包名或原生库。
- 两个 ABI 共 4 个原生库，仅 AndroidX graphics-path 与 DataStore helper；字节与锁定 AAR 一致，ELF LOAD 和 APK ZIP 数据均满足 16 KiB 对齐。
- `releaseInput=false`、`devicePlaybackEvidence=not-verified`，不是发布或设备验收收据。

在 WSL 使用 Windows Gradle 缓存执行：

```sh
GRADLE_USER_HOME=/mnt/d/DevTools/BuildCache/Gradle/user-home python3 tools/verify-android-artifact.py apps/android/build/outputs/apk/preview/android-preview.apk --report build/exo-features/apk-verification.json
```

## 待设备验收

现有 opt-in 连续播放、个人格式矩阵、持久化恢复和暂停旋转测试已适配当前输出类型；直接输出通过 PixelCopy 读取真实视频帧，不能把保留 TextureView 的旧帧当作成功。新增 Android instrumentation 用例覆盖自适应组、外挂字幕 ID、Surface 句柄身份与字幕预检。

后续应执行真实视频/音频与字幕样本、暂停定位、动态加字幕、迷你/全屏/旋转/PiP 交接及 Activity 重建，检查黑帧、旧帧、裁剪、控件覆盖和播放连续性。同步专项包括带 gapless priming 的 AAC/MP3、不同位深 PCM、音频 ±10 秒与字幕 ±60 秒、片头/片尾、暂停 seek、临时倍速与恢复、字幕切换及高码率小缓存；不可 seek 的 provider、I/O 重试和极低内存仍需样本验收。HDR/杜比必须同时核验解码器与实际显示输出；诊断页 PQ/HLG 是片源元数据。功耗需独立受控 A/B 实验。当前无设备运行结果。
