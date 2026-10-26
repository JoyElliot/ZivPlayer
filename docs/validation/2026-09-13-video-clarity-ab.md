# 视频清晰度真机 A/B：输出尺寸与 VLC 硬解输出

日期：2026-09-13。分支：`codex/libvlc4-comparison`，基于 `***`。

## 结论

这台设备上已确认应用的固定输出尺寸会损失细节。相同 1080p 静态视频、相同内核和解码请求，只把 native 缓冲从 2400×1350 改成 1920×1080，mpv 的细线和小字恢复，VLC 的横向细节也恢复。每个 native 条件在交换顺序后重复一次，对应条件的整张截图逐字节相同。

VLC 另有独立问题：1920×1080 输出下，自动硬解路径仍在原始 GLES 输出中出现纵向重采样和细线拍频；相同主 APK、相同视频、相同输出尺寸，关闭 VLC 硬解后该现象消失。范围可缩小到硬解输出、SurfaceTexture、GLES 采样之间，但还没有运行时 crop/transform matrix，不能认定具体依赖代码、驱动或某一行计算为根因。

本次完成诊断和测试工具，没有修改默认 `PlayerVideoSurface` 的尺寸策略，也没有升级依赖。正式通用修复还需处理 4K、非方形像素、旋转、FIT/FILL/STRETCH、暂停重绘和 PiP。

## 设备与控制

- 设备：23049RAD8C，Android 15 / API 35，物理屏幕 1080×2400。
- 横屏全屏：2400×1080；实际视频 View 为 1920×1080，屏幕矩形 `(240, 0)-(2160, 1080)`。
- 主包的三个内核共用同一 Activity、MediaSession 和真实 `PlayerVideoSurface`。每腿从新进程启动，通过进程内 instrumentation override 选择内核和缓冲尺寸。
- 固定 `PlaybackOptions()`：AUTOMATIC、BALANCED、FIT、无着色器、无反交错；额外软件对照只改变 `decoder=SOFTWARE`。字幕素材为空，播放器静音、1 倍速、不循环。
- 全部成功捕获在暂停的 2000 ms 位置，每腿保存三组 PixelCopy 和 UiAutomation 截图，另存真实内核诊断、SurfaceFlinger、media.codec、进程 logcat。
- 主测试 9 腿；完整色彩/SAR 标记对照及软硬解对照 6 腿，共 15 腿成功。另一次 `vlc-tagged-source-01` 在进入前台阶段被用户操作打断，无有效帧，保留失败归档并排除；使用新标签重试成功。

## 素材与产物身份

本机证据根：`native/out/clarity-ab-20260913/`。由原创灰度图生成视频，含 1/2/3/4/6/8 像素横竖线对、小字号文字、灰阶和斜边。宿主编码器：FFmpeg `8.1.2-full_build-www.gyan.dev`。

| 文件 | SHA-256 |
| --- | --- |
| `fixture/clarity-target.mp4` | `543b41f83eef2765f8ec0e572ab101548fa47791b277a39e553c66d6851ac7f1` |
| `fixture/clarity-target-tagged.mp4` | `2e8afcefd66a50e556b87cde88176e4a9cb48fccde71c99222149bd5b2d7d26e` |
| `clarity-main.apk` | `ad17e183ff5c52cb7689cc9ffed282d1b5b78fc229d093e902b6b69c4a381bfe` |
| `clarity-test.apk`（初始） | `9b0f8e534106a688b944a42d3857412ab24a3498438fe65341b2d7a8e3934f0e` |
| `clarity-test-v2.apk`（增加 SHA/尺寸断言与解码参数） | `62c5bce3bf6942e9d7c0d5e9b9841b7e36fd22b8f6ef07fff0e0d1d8dbc7fd5c` |

第一份视频为 H.264 High、1920×1080、yuv420p、24 fps、12 秒、BT.709 matrix、limited range。色彩 primaries/transfer 和 SAR 未显式写入，因此另用全 I 帧、SAR 1:1、BT.709 primaries/transfer/matrix 的 tagged 版本做控制，现象不变。

输入图每帧相同，但有损编码不保证每个解码帧逐字节相同。保存的 `decoded-frames.md5` 显示初始视频共 288 帧、5 个解码哈希，其中帧 48–263（2 秒起）相同；tagged 版本除首帧外的 287 帧相同。参考 PNG 使用 ffmpeg 解码 2 秒帧。不要把最初 manifest 中 `staticAllFrames` 的命名当作所有压缩帧一致的证明；生成器现已改为 `staticInputImage` 并说明边界。

## 尺寸 A/B 结果

来源：`analysis.json`、各腿 `capture.json`、原始 PNG、`surfaceflinger.txt`，以及 `text-comparison.png`。

| 条件 | View / 最终显示 | Surface 输出 | 1 px 竖线归一化调制度 | 斜边 10–90% 中位宽度 |
| --- | --- | --- | ---: | ---: |
| mpv 默认 | 1920×1080 | 2400×1350 | 0.5540 | 1.178 px |
| mpv 原尺寸 | 1920×1080 | 1920×1080 | 0.9947 | 0.803 px |
| VLC 默认、自动解码 | 1920×1080 | 2400×1350 | 0.4504 | 1.433 px |
| VLC 原尺寸、自动解码 | 1920×1080 | 1920×1080 | 1.0000 | 0.800 px |

这里的竖线测量横向采样能力。数字只描述本测试图指定 ROI，不能解释为整部视频的“画质百分比”。调制度为线对亮暗差，先除以同一捕获中 48/208 平坦灰块的亮暗差，再用解码参考归一化，降低不同管线亮度映射的影响。斜边宽度是局部像素插值描述，不是光学 MTF 测量。

`mpv-default/source-01/02`、`vlc-default/source-01/02` 的对应重复截图逐字节相同；各腿三次原始图和三次屏幕图也稳定。源尺寸的 native RGBA 输出，其 PixelCopy 与屏幕视频裁剪的 RGB 像素相同。默认尺寸的实际 SurfaceFlinger 缓冲为 2400×1350，再映射到 1920×1080，吻合多一次缩放的机制。

## VLC 剩余的纵向异常

使用 tagged 视频，在相同 1920×1080 缓冲、相同主 APK 下进行自动/软件请求对照：

| 条件 | 结果 |
| --- | --- |
| `vlc-tagged-source-02`，自动 | 横向细节恢复，纵向细线仍出现位置漂移/拍频；raw 与屏幕 RGB 相同 |
| `vlc-tagged-automatic-source-03`，同 v2 测试包自动对照 | 与上项整张截图逐字节相同 |
| `vlc-tagged-software-source-01/02`，关闭硬解 | 纵向异常消失，两次截图逐字节相同；1 px 横线调制度约 1.0026 |
| `vlc-tagged-software-default-01`，关闭硬解但保留 2400×1350 缓冲 | 仍有尺寸缩放损失；1 px 横线调制度约 0.4536 |

自动腿的进程日志有 `c2.qti.avc.decoder`；软件请求腿无对应 CCodec/QTI 初始化日志。VLC Java 诊断不提供实际解码器名称，所以不捏造 FFmpeg/软件解码器名称。能确认的是代码执行 `setHWDecoderEnabled(false)` 的对照现象。

VLC 源码只读检查显示硬解使用 `ANDROID_OPAQUE`、SurfaceTexture 和 GLES importer 的矩阵/坐标链；没有找到足以证明某处裁剪公式错误的静态证据。运行时 MediaCodec 输出 crop、SurfaceTexture transform 和设备驱动之间仍需进一步隔离。

分析 JSON 中自动 VLC 的 H1/H2 **带符号**指标为负，是原始像素坐标/相位对应失效的信号，不是“负清晰度”，不能用它直接给内核排名。

## Exo 截图的独立限制

Exo 原始 PixelCopy 的横竖细线完整；但 UiAutomation 屏幕截图出现额外纵向采样。SurfaceFlinger 显示解码 allocation 为 1920×1088、content crop 为 1920×1080，同时记录 `scaleY=0.9926`，与 `1080/1088` 一致。完整 BT.709/SAR 标记对照仍复现。

HWC 记录的 source crop 与最终显示区域又符合 1080 行内容，因此目前不能区分物理显示合成与 UiAutomation 读取实现。截图不是光学屏幕证据，不能根据这次 Exo 截图对三个内核做绝对画质排名，也不能否定用户觉得 Exo 清晰的观察。

PixelCopy 的目标 Bitmap 为 `SurfaceHolder.surfaceFrame` 尺寸；这读取有效 Surface 内容，并不等于拿到了带 padding 的整个 GraphicBuffer。现有常规 `copyNativeSurface()` 使用 View 尺寸，会触发测试端缩放，本次没有复用它来量化 native 默认缓冲。

## 工具与验收

- `tools/create-clarity-fixture.py`：生成固定图、编码视频、参考帧及 ROI。
- `ClarityCaptureTest`：复用真实主界面和播放服务，按内核/尺寸捕获；仅有显式 `clarityEngine` 时执行。
- `tools/run-clarity-capture.py`：每腿新进程，先核对已暂存的本地/远端素材 SHA，再传期望 SHA 给设备；保存独立日志、tar、各成员哈希。它要求先暂存素材，不负责部署 APK。
- `tools/analyze-clarity-captures.py`：只裁剪原始屏幕像素，不缩放后再计算指标；图片拼图才使用最近邻放大。
- v2 测试增加缓冲策略、焦点/屏幕位置和有效定位范围断言。它专用于这台 2400×1080 横屏设备，不宣称是跨设备测试。截图控件是否遮挡仍经过原图检查与 ROI 核验，`OK (1 test)` 只代表捕获流程成功。
- 初次主包和测试包构建成功（47 秒），v2 测试包构建成功（29 秒）。主 APK 通过独立 VLC 实验 gate：34 个原生库、157 个 DEX JNI 描述符、原 C++ 字节与 16 KiB ZIP/ELF 对齐。没有重编译/升级解码依赖。

最小复现（先构建、安装测试包，并把同名素材放到 app 私有 `files/power-fixtures/`；使用新的标签）：

```powershell
python tools/run-clarity-capture.py --serial <serial> --output native/out/clarity-ab-20260913 --label mpv-default-new --engine libmpv --buffer default
python tools/run-clarity-capture.py --serial <serial> --output native/out/clarity-ab-20260913 --label mpv-source-new --engine libmpv --buffer source
python tools/run-clarity-capture.py --serial <serial> --output native/out/clarity-ab-20260913 --label vlc-software-new --engine vlc --buffer source --fixture clarity-target-tagged.mp4 --decoder-mode software
python tools/analyze-clarity-captures.py native/out/clarity-ab-20260913
```

## 收尾与保留边界

设备结果共 16 个标签（含被打断的失败腿），归档后逐项核对本地与远端 SHA，再精确删除本次 154 个结果/素材文件及两个暂存文件。没有清空应用数据或删除已有媒体、资源和其它实验数据。

已恢复测试前主 APK `d4a75511…e0777` 与测试 APK `2f48a9b7…470fd`，从设备回读完整 SHA 确认一致。前后数据库完整性、schema、所有表的列和逐行逻辑内容一致；偏好文件 SHA 均为 `9eab1f8d05da0697e0963a4cbc66a622db05018edcd613c2466a1cbde29e3dfb`。该快照覆盖数据库和 `files/datastore`，不冒充完整 app cache 的逐字节比较。

对应证据：`cleanup.json`、`restored-apks.json`、`data-preservation.json`。测试工具和本报告保留在当前分支工作区；旧实验分支和 `main` 未变，未推送。

本轮验证为一台设备上的 1080p SDR 固定图诊断，不包含真实影片的全场景验收、MX 内部管线、拖动 480 px 预览的修改、HDR 或功耗测量。
