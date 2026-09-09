# 2026-09-09：连续拖动预览与定位后续播验证

[文档导航](../README.md) · [设计决定](../adr/0016-live-seek-preview-and-exact-commit.md)

## 验证对象与方法

- Android 15 真机 `23049RAD8C`，本次运行 `arm64-v8a`。
- 原创帧编码视频：90 秒、1280 × 720、24 fps、H.264/AAC、4 秒 GOP。
- 用户指定视频：`[DMG&VCB-Studio] OSHI NO KO [02][Ma10p_1080p][x265_flac]`。实测轨道为 1920 × 1080 Main10 HEVC、23.976 fps、48 kHz / 24-bit / 双声道 FLAC；mpv 使用 MediaCodec 硬件解码。
- 最终设备安装包与本地 APK 的 SHA-256 一致：`3705006a90c05d18435d3d166e695175ff8d02302e76ebe11e9e25d98cbd997b`。

帧编码视频通过窗口 `PixelCopy` 读取最终合成画面中的 12 位帧编号，包括独立预览覆盖层。连续帧率测试使用真实 Choreographer，不使用 Compose Test 的受控帧时钟，也不把控制器进度或预览状态更新次数算作画面帧率。

用户视频通过不连接播放服务的私有 `LibmpvBackend` 测量 `SurfaceTexture` 更新。它能反映主视频缓冲的更新间隔，但不是屏幕光学测量；动画中的静止镜头也不能用像素差分替代解码帧数。

## 连续拖动

仅请求原生关键帧的上一版在 4 秒 GOP 素材上只显示 20、24、28、32、36、40、44 秒画面。新版使用独立视频解码和缓存，主画面能显示 GOP 之间的中间帧。

保持手指按下，以约 10 倍速度从 20 秒拖向 40 秒，再反向拖回 20 秒，每个方向持续约 2 秒：

| 窗口中实际可见的不同画面 | 正向 | 反向 |
| --- | ---: | ---: |
| 不同画面数（包含各段首帧） | 87 | 90 |
| 可见更新率 | 43.5 fps | 45 fps |
| 相邻新画面间隔中位数 | 18.6 ms | 19.7 ms |
| 最大间隔（含首次建立预览） | 255.6 ms | 43.6 ms |

松手后主视频精确显示 20,000 毫秒，覆盖层已撤去。这里的帧率是快速浏览不同时刻的画面，不是把视频正常播放从 24 fps 提升到 45 fps。

用户 HEVC 视频的独立解码基准使用 `c2.qti.hevc.decoder`，每次向前移动 200 毫秒媒体时间，取帧间隔至少 50 毫秒：首次取帧 **550.4 ms**，随后前向取帧中位数 **25.5 ms**；反向缓存命中中位数 **0.42 ms**，本次跨缓存区间的最大开销 **194.5 ms**。这组数值是解码/缓存耗时，不等同于该视频的屏幕预览帧率。

## 跳转后的连续播放

侧边双击已改为一次精确 seek，避免先关键帧再精确定位造成两次重置。对满足单/双声道及采样率条件的本地文件，优先使用 OpenSL ES 输出；其他来源和多声道保留原 AudioTrack 优先顺序。

在用户视频的三个单次精确定位位置，比较目标画面附近的首个新缓冲与下一缓冲：

| 精确目标 | 新版请求到目标附近首帧 | 首帧到下一帧 |
| --- | ---: | ---: |
| 60 秒 | 约 75 ms | 50.6 ms |
| 180 秒 | 约 463 ms | 51.8 ms |
| 310 秒 | 约 130 ms | 50.5 ms |

旧 AudioTrack 对照测量在目标画面后的间隔约 **158–160 ms**；OpenSL ES 的单独 A/B 和最终默认配置复测约 **42–52 ms**。最终 1 秒稳定播放采样为 24 次更新，诊断中的丢帧计数为 0。原创帧编码视频另行验证正在播放时的 +5 秒请求：最终版本目标帧约 175 ms 出现，下一不同帧相隔约 103 ms，之后保持播放；上一候选包同项为 240 ms / 63 ms，存在运行波动。

长 GOP 中的精确目标仍需从参考帧解码，180 秒位置仍接近半秒。结果证明本设备首帧后的额外停顿缩短，不能据此宣称所有定位都无等待，或已经达到 MX Player 在所有视频上的性能。

## 回归结果

- **184 项 JVM 测试通过**：player-api 6、player-runtime 36、libmpv-android 71、playback-android 50、feature/player 11、Android 应用 10。
- 涉及的 Android Lint、debug APK 与测试 APK 编译通过。
- APK 校验通过：20 个审计原生库、4 个锁定 AndroidX 辅助库、16 KiB ZIP 对齐、16 个 DEX JNI 方法；没有修改锁定原生库或 JNI 签名。
- **19 项常规真机用例通过**：实时 seek 1、全屏手势 11、播放器 UI 契约 5、轨道/字幕/重播/Surface 重建 1、独立预览解码正确性 1。
- **3 项专项真机测量通过**：真实屏幕连续拖动、用户视频解码缓存、用户视频定位后的 SurfaceTexture 更新。

覆盖取消后不发布旧画面、GPU 像素与解码时间戳一致、首次启动、反向缓存、片尾最后实帧、横滑、时间轴、松手精确定位、暂停/播放意图、旧 token/媒体身份、外部 seek、停止重播、断连、失焦取消以及连续 +10 秒。带关联 ID 的提交确认只在对应最终 seek 完成后返回，旧代际完成不能释放新请求。最终补充的断言验证原生回退预览被外部 seek 终止后可以重新开始，以及精确提交期间 TextureView 销毁/重建仍能撤去覆盖层并显示目标帧；修正后重跑这两组交互、连续帧率及原生 Surface 回归通过。

音频焦点用例沿用此前会话约定跳过。听感、蓝牙/多声道路由、完整网络播放、HDR 和跨设备矩阵没有在本轮验证。

## 预览边界

独立预览为长边 480 像素的 SDR RGBA8 图像，缓存最多 80 帧；精确提交后显示主播放器的原画质与字幕。HDR、BT.2020、旋转、非方形像素、软件解码偏好、非本地来源以及独立解码错误会回退到原生关键帧预览，因此不能承诺这些情形具有相同帧率。

快速拖动时画面允许落后于目标时间。首次创建解码器、越过缓存区间和长 GOP 仍会产生等待。

## 数据核对

数据库完整性为 `ok`，49 条个人记录全部保留。48 条与本轮开始快照逐字段相同；指定视频的记录在 **15:57:33（UTC+8）**、首次自动基准调用之前，已更新到 **6:32.976**。本轮保留此较新的记录，没有用 15:57:06 快照中的旧进度覆盖它。严格快照比较中的这一项差异以及时间分析分别保存在两个数据报告中。

*** 个媒体库文件夹、*** 条索引、原有 *** 个资源记录和资源文件、个人轨道/外挂字幕选择均保留，设置文件字节相同。USB 常亮值 `***` 和屏幕超时 `***` 保持不变。覆盖安装没有清除应用数据；自动交互仅使用单独识别的测试媒体，用户视频性能测量使用私有后端。

## 复现入口

```powershell
.\tools\create-device-fixtures.ps1
.\tools\create-seek-fixture.ps1
.\gradlew.bat :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest
adb install -r apps/android/build/outputs/apk/debug/android-debug.apk
adb install -r apps/android/build/outputs/apk/androidTest/debug/android-debug-androidTest.apk
adb shell am instrument -w -e class io.github.joyelliot.zivplayer.SeekPreviewDeviceTest io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
adb shell am instrument -w -e seekBenchmark true -e class io.github.joyelliot.zivplayer.ContinuousScrubDeviceTest io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
```

用户视频的 `SeekPresentationBenchmarkTest` / `SeekCachedCodecBenchmarkTest` 需要 `seekBenchmark=true`，以及应用缓存中的 `seek-benchmark-source.json`，其 `source_uri` 必须是应用已有读取权限的本地视频。未提供时跳过；文件 URI 不写入仓库。本轮结束已移除设备上此临时源文件。

本机忽略目录 `native/out/seek-continuous-20260909/` 保留 `continuous-ui-final.json`、`presentation-before.json`、`presentation-default-after.json`、`cached-codec-final.json`、`final-checks.log`、`final-device-regression.log`、`final-extractor-tests.log`、`surface-fence-device.log`、`apk-verification.json`、`data-preservation.json` 和 `data-preservation-analysis.json`。上一轮仅关键帧实现的数据在 `native/out/seek-preview-20260909/`，不能用其采样声称连续预览已达高帧率。
