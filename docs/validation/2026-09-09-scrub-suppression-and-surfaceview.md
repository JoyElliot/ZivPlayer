# 2026-09-09：拖动暂停、大跨度预览与 SurfaceView 验证

[文档导航](../README.md) · [设计决定](../adr/0016-live-seek-preview-and-exact-commit.md) · [前一轮验证](2026-09-09-live-seek-preview.md)

## 本次改动

继续使用 libmpv，未替换原生库、JNI 或字幕引擎。拖动开始时由服务暂时暂停主播放，保留用户的播放意图；独立预览解码等暂停确认后才启动。松手精确定位完成后，原来播放的继续播放，原来暂停的保持暂停。

主视频从 TextureView 改为 SurfaceView，保留固定原生缓冲尺寸和 FIT / FILL / STRETCH 几何。SurfaceHolder 管理 Surface 生命周期；异步重绘、解绑及旧回调都有代际校验。预览覆盖层和普通控制层保留在视频上方。

前景解码的过时取消窗口按拖动速度在 250–1,000 ms 内调整：快速扫动及时跟随新目标，较慢浏览给长 GOP 更多解码时间；反转和松手仍立即取消。反向预取遇到新目标立即让出线程。固定 3 秒媒体距离曾使 40 倍扫动每约 75 ms 重启解码，是本轮大跨度停滞的主要原因。

## 设备与测量方法

- Android 15，`23049RAD8C`，本次运行 arm64-v8a。
- 原创帧编码素材：90 秒、1280 × 720、24 fps、H.264/AAC、4 秒 GOP。
- 用户文件沿用前轮的 1080p Main10 HEVC / FLAC 视频，通过已有 SAF 授权读取。
- 对照 APK SHA-256：`3705006a90c05d18435d3d166e695175ff8d02302e76ebe11e9e25d98cbd997b`。
- 最终本地与手机安装包 SHA-256 一致：`57d9cad34a70703c8c041a2fb91f13f605d8ebb81e0a6f5f0cea465b6da92a47`。

帧编码素材使用真实触摸事件与 Choreographer。拖动覆盖层通过窗口 PixelCopy 读取帧号；SurfaceView 主画面直接读取生产缓冲。屏幕截图另行核对字幕、覆盖关系、暂停旋转和三种显示模式。生产缓冲读取不是屏幕光学测量，控制器的乐观位置不作为出帧证据。

测试在开始拖动前等待测试控制器和应用控制器都收到播放状态、窗口获得焦点。此前有一次控制器状态未同步的宽扫没有进入拖动，已标为无效采样，未放入最终对照。

## 最终对照

以下均从正常播放开始，持续按住，前向 2 秒、反向 2 秒。数值为不同可见帧的更新次数/秒，包含每段首帧；快扫经过不同媒体时刻，不代表把源视频的正常播放帧率提高。

| 帧编码素材 | 旧版正向 / 反向 | 最终版正向 / 反向 |
| --- | ---: | ---: |
| 约 20 → 40 → 20 秒 | 21 / 10.5 | 41.5 / 20 |
| 约 5 → 85 → 5 秒 | 9.5 / 2.5 | 38.5 / 14 |

旧版短区间因反向低于 15 次/秒的门槛而未通过性能断言，其原始失败日志保留。最终两种跨度均通过临时暂停、原播放意图、松手精确定位及后续 +5 秒续播检查。进入前播放期间等待会使实际起点略晚于表中整数；原始报告保存 `gestureOriginMs` 和 `committedFrameMs`。

独立考察临时暂停、尚未切换 SurfaceView 的候选包曾测得 41.5 / 44.5；切换后、尚未调整快扫调度的候选包为 44 / 50。运行间存在波动，最终调度优先兼顾大跨度和长 GOP；不能把这些单次差异全归因于 SurfaceView，也没有测量它的功耗收益。

用户 HEVC 文件通过主界面的 ViewModel 预览入口，以约 10 倍速度浏览 500–520 秒并反向返回。窗口像素变化计数如下；动画的静止镜头可能重复像素，所以这不是硬件解码帧率。真实触摸链路由上面的帧编码素材单独覆盖。

| HEVC 主界面预览 | 正向像素更新/秒 | 反向像素更新/秒 | 精确提交并恢复播放 |
| --- | ---: | ---: | ---: |
| 同步后的旧版对照 | 15 | 9.5 | 262 ms |
| 最终版第 1 次 | 32.5 | 31.5 | 345 ms |
| 最终版第 2 次 | 33.5 | 35 | 220 ms |

两轮最终 HEVC 测试都确认拖动时 `playWhenReady=true`、`isPlaying=false`、suppression 为 SCRUBBING，提交后位置继续推进。固定 250 ms 的中间候选包曾出现 5.5 / 1 的低更新率；该候选已被速度自适应窗口取代，保留失败表现作为调整依据。精确定位仍受 GOP 和设备负载影响，没有稳定的零等待承诺。

## 生命周期与显示回归

- 核心/API、Android 播放服务和应用共 108 项 JVM 测试通过，其中 6 项新增核心测试覆盖临时抑制、显式 Play/Pause、停止/换媒体和结束事件。
- 21 项真机回归通过：实时定位 1、原生轨道/字幕/重播/重建与音频焦点 3、全屏手势 11、播放器 UI 契约 5、独立解码正确性 1。另通过文件夹播放与真实暂停旋转/显示比例用例 1。
- 后续生命周期回调的小修单独重跑真实焦点打断用例通过；最终自适应调度包重跑短区间、80 秒跨度及两轮 HEVC 主界面基准，4 次通过。
- 构建与 Android Lint 通过，Lint 为 0 errors / 16 warnings，保留 PiP 建议、依赖版本等既有告警。APK 校验通过：20 个审计原生库、4 个锁定 AndroidX 辅助库、16 KiB ZIP 对齐、16 个 DEX JNI 方法。

用户关闭系统“允许多应用同时发声”后，真实瞬时音频焦点丢失/返回的普通播放暂停与恢复用例通过。此前该系统设置开启时的焦点失败日志保留，不计为最终通过结果。拖动中被另一个真实焦点客户端打断时，本地预览与原生回退都恢复拖动前位置并保持暂停；返回焦点不会重启已取消的拖动。另验证已发出的精确提交不会被紧随的生命周期 Pause 抢先取消。

暂停转屏比较视频缓冲像素，并用完整屏幕采样确认居中范围及 FIT / FILL / STRETCH；本轮未做逐帧转屏动画分析。目录测试前置也已修正：先等待异步目录列表，再让 LazyColumn 滚动到尚未组成可见节点的测试目录。

## 数据与产物

数据库完整性为 `ok`；49 条个人记录及进度逐字段相同。3 个媒体库来源、254 条索引、5 个已有资源记录、资源文件、个人轨道/外挂字幕选择与设置均保持。设置文件 SHA-256 为 `9eab1f8d05da0697e0963a4cbc66a622db05018edcd613c2466a1cbde29e3dfb`。

文件级比较唯一不同的是 `files/profileInstalled`。它是 AndroidX ProfileVerifier 的缓存，包含包更新时间等运行信息，随覆盖安装重写；未将此缓存回填为旧值。[AndroidX 源码](https://github.com/androidx/androidx/blob/androidx-main/profileinstaller/profileinstaller/src/main/java/androidx/profileinstaller/ProfileVerifier.java)

用户文件基准每次使用新 UUID 媒体 ID，事先确认没有对应记录，不经过媒体注册器；测试后仍无该记录。临时 `seek-benchmark-source.json` 已从手机缓存移除。USB 常亮值 `***`、屏幕超时 `***` 保持不变；用户主动修改的多应用发声开关未还原。

本地 APK：`apps/android/build/outputs/apk/debug/android-debug.apk`。没有提交或推送 Git。

原始资料在本机忽略目录 `native/out/scrub-surface-20260909/`：

- `baseline-synced-{short,wide,personal}.json/.log`：最终同步方法下的旧版对照。
- `adaptive-short.json`、`adaptive-wide.json`、`adaptive-personal-{1,2}.json` 及同名日志：最终性能结果。
- `final-regression-device.log`、`final-lifecycle-rotation-device.log`、`final-pause-fence-device.log`：回归链路。
- `final-adaptive-lint.log`、`final-apk-verification.txt`、`data-preservation.json`：最终静态、产物和数据核对。

## 复现

先按[开发指南](../development.md)准备素材、原生 staging 并编译安装 APK。短区间与宽扫：

```powershell
adb shell am instrument -w -e seekBenchmark true -e playingDuringDrag true -e class io.github.joyelliot.zivplayer.ContinuousScrubDeviceTest io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
adb shell am instrument -w -e seekBenchmark true -e playingDuringDrag true -e dragStartMs 5000 -e dragSpanMs 80000 -e class io.github.joyelliot.zivplayer.ContinuousScrubDeviceTest io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
```

HEVC 用例为 `PersonalScrubBenchmarkTest`，需 `seekBenchmark=true` 及应用缓存中的临时来源 JSON；URI 必须已有读取授权，勿提交到仓库。旧版对照添加 `requireSuppression=false`，只取消新暂停机制的断言，不跳过像素采样。

音频焦点测试需要系统实际实施独占焦点策略；显示比例用例需要已授权的原创测试目录 `personalFixtureFolder=ZivPlayer-Expansion-20260907`。网络、HDR、多声道、蓝牙路由、长期功耗与其他设备不在本轮验证范围。
