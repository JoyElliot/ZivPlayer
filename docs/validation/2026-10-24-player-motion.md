# 播放器动画起步、方向选择与优化预览验证

- 日期：2026-10-24
- 代码线：`main`，起点 `***`，最终应用代码 `***`
- 设备：Xiaomi 23049RAD8C / marble，Android 15 / API 35，真实无线 ADB
- 最终主机构建：`0.1.0-dev.rotation10`，versionCode `11`，`preview`，按用户新指令未安装、未真机测试
- 手机保留：`0.1.0-dev.rotation9`，versionCode `10`，`preview`
- 范围：展示与方向交接；没有修改功耗实验代码线

## 本轮处理

1. 移除视频顶部常驻的白色拖动条，保留不可见下拉命中区域。
2. 新播放项目取得可信媒体方向后，从迷你端点直接展开到目标方向。同一播放身份手动改为竖屏，再收起、重开时保持竖屏。READY 或 PLAYING/PAUSED 本身不足以判定音频项目，避免视频轨道尚未发现时误选竖屏。
3. 长距离、带旋转的全屏↔迷你弹簧降为 stiffness 260，普通展开/收起仍为 420，独立转屏为 360。动画中断和手势接管继续沿用当前进度与速度。
4. 转屏连续值在图层读取；控制布局在半程淡出点切换轴向。合并横竖屏播放按钮为同一组节点，避免创建另一组按钮及其动画状态。整层淡出采用 `ModulateAlpha`，减少整屏离屏纹理合成；它在控件重叠区域的过渡 alpha 与整组离屏合成略有不同，端点不变。
5. FIT/FILL 先计算横竖端点尺寸再插值，消除原来的缩放反向拐点。例如 16:9 横视频向竖屏转动时，旧计算会先从 1080 高度涨到约 1253，再缩小；新路径在两端之间单调变化。STRETCH 保持其原有窗口插值方式。
6. 系统栏、亮度和圆角监听只在实际输入变化时更新，保留唯一 TextureView/SurfaceTexture、真实系统方向、JUMPCUT 与稳定安全区的已有交接方案。
7. 缓存最后成功提交的 PictureInPictureParams，相等时不再同步调用系统。对齐变化而自动进入选项保持 false 时，可能没有实际参数变化；此时无需等待一次 WM 调用。比例、播放动作、真实 bounds 或自动进入状态变化仍会提交。失败不更新缓存，显式进入 PiP 先使缓存失效；缓存属于单个 Activity。该项仅完成静态审查和主机构建，尚未真机复测。

架构及边界见 [ADR 0023](../adr/0023-system-rotation-handoff.md)。

## 构建与已安装版本

第一次对照时手机是旧的 `rotation7` Debug；当时采集的是基线，不是本轮优化。随后安装的 `rotation8` 已包含打开方向、长路径节奏及优化预览构建，用户反馈其明显改善。追加全屏内转屏修正后安装 `rotation9`。最后新增 PiP 参数去重并构建 `rotation10` 时，用户要求把手机交回另一线程做功耗实验，后续立即停止一切设备操作，未安装 `rotation10`。

`preview` 继承 release，开启 R8、资源收缩和优化规则，非 debuggable，允许 shell profiling，使用现有开发签名供原位覆盖安装。它是开发预览构建，不将其等同于正式发布验收。rotation9 的 Debug 回归包与已安装预览来自同一份应用源代码；恢复数据时只临时安装同版本 Debug，随后覆盖回同版本 Preview，没有恢复旧 APK。

最终主机 APK（rotation10，未安装）：`apps/android/build/outputs/apk/preview/android-preview.apk`，250354289 bytes。

```text
SHA-256 9269aaae65d6af9070fc7e04dc99a115af8cd5f0c497019147e78dd26d2d6ece
```

`rotation10-preview-audit.json` 核对 20 个播放器原生库与 4 个 AndroidX 辅助库、APK 原生库对齐和 16 个 JNI 方法签名。报告的 `releaseInput=false`、`runtimeRegistrationEvidence=pending-device-art-smoke` 保持原始含义：这是静态打包审计，不能单凭它宣称所有运行时注册已验证。此前 `delivered-preview-audit.json` 对应已安装的 rotation9，其真实本地视频打开、播放和方向切换另有设备操作记录。

已安装 rotation9 的 APK 为 250354285 bytes，SHA-256 `3823eded124b39e5d4b31ece4566faff75b06bfef01a52925bb16d37bf3bda73`。不能把下列真机记录转记到 rotation10。

## 自动检查与设备行为

最终主机任务：

```text
:apps:android:testDebugUnitTest
:apps:android:lintDebug
:apps:android:assembleDebug
:apps:android:assembleDebugAndroidTest
:apps:android:assemblePreview
--offline --no-daemon --console=plain
```

- `rotation10-build.log`：BUILD SUCCESSFUL（1m 14s）；53 个应用 JVM 测试通过；Lint 0 error、9 warning。此前 rotation9 的相同主机任务亦通过。
- `rotation8-final-instrumentation.txt`：31/31，通过打开方向、首页输入、展示连续性、系统方向交接、两种底栏及真实播放回归。
- `rotation9-instrumentation.txt`：缩放与合成改动后，展示连续性及真实播放 10/10。
- `delivered-instrumentation.txt`：rotation9 按钮复用改动后，打开交互及真实播放 6/6；新增断言确认转屏前后播放按钮语义节点身份不变。
- `delivered-continuous.json`：`completed=true`，`playingStateInterruptionsDuringHandoffs=0`；同一视频输出经过迷你、全屏、真实系统转屏及 PiP，并覆盖暂停后的定位、系统音量和 Activity 重建。
- `delivered-ui-result.json`：rotation9 R8 Preview 三轮重启进程后的真实系统方向往返完成，手动竖屏→迷你→重开保持竖屏。截图检查顶部常驻白线已移除，横竖控件终点位置正常。

这些设备 instrumentation 运行于 Debug；不能将其写成在 R8 包内跑过全部同样用例。rotation9 Preview 的本轮直接操作重点为方向、打开和收起，未另行完成 Preview 专用 PiP 回归。rotation10 新增缓存后的真实播放与 PiP 回归未执行；用户暂停设备验证优先于原定测试计划。

新增参数缓存另做独立只读审查：未发现会错误跳过实际变化的路径；核对了逐字段比较、失败后重试、显式进入旁路、Rect 复制、RemoteAction 未修改以及 Activity 重建边界。现有测试未直接断言参数提交次数和异常重试次数，该静态结论不替代 OEM 设备回归。

初次新增的预置方向测试因关闭测试时钟的位置太晚而失败：动画已在断言中间进度前自动完成。把 prepared 状态先设为 false，关闭自动时钟再启动后通过，未用生产代码绕过。另一次 Preview 自动化在用户手测期间无法取得 idle UI dump，未把该轮后续菜单/PiP 步骤计为通过。

## 帧耗时与测量限制

原始记录保留于被忽略的 `build/player-motion-20261024/`。`summarize_frames.py` 从 `dumpsys gfxinfo ... framestats` 计算 `FrameCompleted - IntendedVsync`，分位数采用 nearest-rank。下列数据反映应用渲染与提交的耗时代理，不是视频解码帧率，也不是实际屏幕呈现延迟。

该 OEM 的样本 Flags 全为 32，而 Android 15 AOSP `FrameInfo.h` 公开定义只覆盖更低的位；其含义未获确认。`DisplayPresentTime` 全为 0，没有用它推导屏幕呈现帧。不能将这些样本转换为“恒定 60/120 FPS”或全程零卡顿保证。

展开/收起对照使用同一设备、同一本地视频、暗色液态玻璃首页和暂停场景，进度约 33–34 秒，并非严格同一个视频帧。每组三次；表内单位 ms。

| 构建 | 展开 P95（三次） | 收起 P95（三次） |
| --- | --- | --- |
| rotation7 Debug 基线 | 37.15 / 54.19 / 56.59 | 28.75 / 28.54 / 31.20 |
| rotation8 Debug | 56.90 / 40.08 / 35.73 | 35.75 / 24.86 / 27.18 |
| rotation8 R8 Preview | 25.56 / 17.35 / 27.96 | 14.45 / 18.12 / 20.83 |
| rotation9 R8 Preview，按钮复用前 | 24.57 / 22.38 / 20.32 | 15.86 / 15.34 / 16.34 |

Debug 的结果说明，不能把本次改善全归因于某个逻辑改动；优化构建也是对照中的显著差异。上表最后一行属于按钮复用前的 rotation9 候选，不冒充最终包重新测过的展开/收起统计。

全屏内转屏单独检查：rotation8 Preview 首次转竖 P95 45.83、最大 54.66；rotation9 候选首次转竖仍出现 76.93 的尖峰，因此继续处理控件复用，没有只保留较好的预热数据。已安装的 rotation9 APK 对三轮新进程分别测两方向，结果如下：

| 路径/轮次 | 样本数 | P95 | 最大值 | 起步 200 ms 内最大值 |
| --- | ---: | ---: | ---: | ---: |
| 转竖 1 | 77 | 19.20 | 29.92 | 22.16 |
| 转竖 2 | 77 | 13.68 | 49.50 | 16.98 |
| 转竖 3 | 77 | 19.24 | 37.54 | 22.01 |
| 转横 1 | 77 | 22.37 | 35.07 | 22.65 |
| 转横 2 | 77 | 14.78 | 64.76 | 15.34 |
| 转横 3 | 77 | 19.11 | 24.41 | 21.41 |

“起步”以第一条已记录帧的 IntendedVsync 为零点，不是输入点击的精确时间。六条路径该窗口均未记录超过 33.3 ms 的样本。这组 rotation9 样本中超过 33.3 ms 的帧均位于约 702–727 ms 的系统方向交接阶段；最大值仍有 64.76 ms。因此结果支持本机起步改善，不能宣称系统交接慢帧已经完全消失。进程重启也不等于系统/GPU 缓存全冷，且新旧轮次有运行时温度、调度及视频位置差异，单项改动的贡献未被严格隔离。

另一次候选包的 `rotation9-cold-turn.atrace` 用于定位阶段，不与上述独立 gfxinfo 样本逐帧配对。输入后约 669 ms，WMS 的 `updateGlobalConfiguration` 耗时 48.195 ms wall / 33.035 ms CPU；应用首个 resize frame 为 30.213 ms wall / 11.066 ms main CPU，其中一次 `Compose:sideeffects` 中的同步 Binder 等待约 12.774 ms。首个新方向的 RenderThread 绘制为 4.857 ms，随后提交成功。该 trace 不支持把所有延迟归于视频解码或缓冲区分配。

起步 150 ms 内虽有七个 `shader_compile` 标签，但嵌套缓存命中；七项合计 7.241 ms、缓存读取合计 0.233 ms，纹理上传合计 2.329 ms。没有直接的 GL 编译耗时证据，不能仅凭标签认定冷 shader 编译是主要原因。采集到的 main doFrame 最大值也不等于另一次 gfxinfo 的 76.93 ms，未强行将两者归为同一个尖峰。

针对该同步调用的源码核对：Android 15 官方 IActivityClientController 的第39项（0x27）是非 oneway 的 setPictureInPictureParams，应用对齐回调正好从 Compose SideEffect 调用该方法。编号不是跨接口唯一证明，也未核对 OEM Stub，故此为调用链支持的定位。rotation10 的参数去重能消除同值调用，不能预先声称消除了这次 12.774 ms 等待：实际 bounds 或自动 PiP 状态变化仍须提交。Android 13（本项目 minSdk 33）已有参数结构 equals，Builder 会复制 sourceRect；现有 RemoteAction 为 Activity 内复用且不再修改的对象。

## 数据与设备恢复

最终恢复使用用户手测 rotation8 后保存的 `pre-rotation9-state`，而非更早的基线快照。恢复前校验归档路径与哈希，恢复后核对 DataStore、Room 数据库及 WAL/SHM 共四个文件 SHA-256 一致。原始归档、视频文件名、截图和设备日志保留在本地忽略目录，不提交仓库。

`pre-rotation9-state-restoration.json` 记录 `fileHashesMatch=true`、`settingsMatch=true`：亮度 12、自动亮度 1、自动转屏 0、user_rotation 0、息屏 600000 ms，三项系统动画比例均恢复 1.0。STREAM_MUSIC 恢复为原始 21/150。该手机的 shell 音量 set 会发生数值缩放，恢复采用显式参数启用的 `DeviceVolumeRestoreTest` 调用 AudioManager，并核对实际值；普通测试运行不会无条件调整音量。

恢复之后保留 versionCode 10 rotation9 Preview，未再启动应用改写刚恢复的用户状态。`delivered-install-verification.json` 核对手机已安装 base.apk 与上述 rotation9 APK 的 SHA-256 相同、包标志无 DEBUGGABLE，并停止测试包。用户宣布开始功耗实验后，没有继续读写或操控手机；rotation10 仅生成在主机。

## 参考

- [Compose 性能与延后状态读取](https://developer.android.com/develop/ui/compose/performance)
- [Compose 图层与合成策略](https://developer.android.com/develop/ui/compose/graphics/draw/modifiers)
- [Android 15 FrameInfo 定义](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android15-release/libs/hwui/FrameInfo.h)
- [Android 15 FrameMetrics 定义](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android15-release/core/java/android/view/FrameMetrics.java)
- [Android 15 Activity Client Binder 接口](https://android.googlesource.com/platform/frameworks/base/+/android-15.0.0_r1/core/java/android/app/IActivityClientController.aidl)
- [Android 13 PiP 参数比较与 Rect 复制](https://android.googlesource.com/platform/frameworks/base/+/android-13.0.0_r1/core/java/android/app/PictureInPictureParams.java)
