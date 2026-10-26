# 2026-10-17：依赖统一升级与新功耗基线

## 范围与原始状态

用户授权同时更新 `home-feishu-style` 与 `codex/libvlc4-comparison`：共用依赖保持相同版本，功耗分支的 ExoPlayer、libmpv 和 libVLC 分别核验更新与运行选项。两条代码线保留各自功能，今晚实验使用功耗分支。

升级前的已提交位置：

- 功耗分支：`***`，另有未提交的实验修复与工具。
- 最新测试分支：`***`，工作区干净。

功耗分支的原始状态、二进制补丁、56 个已修改或新增文件及 SHA-256 清单已保存至本机忽略目录 `native/out/dependency-upgrade-20261017/power-before`。测试分支的原始状态单独保存于 `test-before`。历史实验数据、执行凭据和已消耗的原生工作区不改写。

## 实验边界

本次升级后的 APK、原生库与运行选项构成新的功耗基线。新一轮须使用新的会话目录和标签，冻结主 APK、测试 APK、依赖清单及源码散列。旧夜的均值和样本数保持各自分组；跨版本比较应说明版本、渲染路径与仪器变化。

自动解码请求须同时核验实际硬件解码器；软件测试须核验实际软件路径。测试结果记录实际渲染模式，不能用请求值代替运行结果。构建和主机检查不等同于真机播放、画质或功耗通过。

## 执行记录

共用版本目录已统一：

| 组件 | 升级前 | 本轮 |
| --- | --- | --- |
| Gradle / AGP | 9.6.1 / 9.3.2 | 9.8.1 / 9.4.1 |
| Kotlin / KSP | 2.4.0 / 2.3.11 | 2.4.21 / 2.3.12 |
| Compose BOM / Core KTX | 2026.08.00 / 1.19.0 | 2026.09.00 / 1.19.1 |
| MIUIX UI（及测试分支 Blur） | 0.9.3 | 0.9.4 |
| Media3 | 1.11.0 | 1.11.1 |
| Room | 2.8.4 | 2.8.5 |
| Navigation / Coil（目录声明，当前未直接使用） | 2.10.0 / 3.6.0 | 2.10.2 / 3.6.3 |

其余已核验的稳定版依赖保持当前最新发布版本。libVLC 4 的最新发布包仍为 `4.0.0-eap29`，Exo 的 `ass-media` / `ass-kt` 仍为 `0.5.1`。

libmpv 采用官方 mpv-android 最近方案的固定源码闭包：mpv `0b7ed670`、FFmpeg `094a2f8a`、libplacebo `3330a515`、libass `b2fe9d87`、dav1d `362bc113`，构建脚本为 `687c7fd5`。同时更新 curl 8.22.0、FriBidi 1.0.17、HarfBuzz 14.5.0、Fontconfig 2.18.3、libxml2 2.15.4 与 libunibreak 8.0。完整 revision、大小与 SHA-256 见 `native/source-manifest.toml`。

这些 revision 是 ZivPlayer 显式锁定的源码选择，不宣称复现上游 CI 的二进制或全部取源行为：上游脚本仍有浮动获取，CI 的 FFmpeg 标签仍写 `n9.0`；本轮独立选择 `094a2f8a`，以取得较新的 MediaCodec 行为。源码归档中的 `MPV_VERSION` 仍为 `0.41.0-UNKNOWN`，运行时可能沿用该字符串；manifest 的描述标签不能替代完整 revision、归档 SHA 与实际 APK 身份。

所选 mpv 已包含四项相关修复：[gpu-next 硬解帧映射生命周期](https://github.com/mpv-player/mpv/commit/69e63f425a531f814431fba12750bdb3721357f2)、[绘制期间到来的 OSD 重绘请求](https://github.com/mpv-player/mpv/commit/606db63248cbf1757907ca1db1a348d597156c06)、[Android AImageReader 缓冲上限从 5 改为 3](https://github.com/mpv-player/mpv/commit/c8d3f6884dd4c4725bae18f72dcf48163b512ea8)、[处理 FFmpeg 对已释放 MediaCodec 缓冲返回的错误](https://github.com/mpv-player/mpv/commit/14f2d48cbc7dda61adb4bd181e107a1f3f76e533)。这些是选择本轮源码的直接理由；目标手机的稳定性、画质和功耗收益仍须实测。

运行选项：

- libmpv：默认 `gpu-next`，保留 `gpu` 对照；继续 `mediacodec,mediacodec-copy` 与已有 AudioTrack/OpenSL ES 策略，记录实际 VO、AO、硬解 interop。保留用户选择的缩放和 HDR 画质，不用 `profile=fast` 改写画质条件。
- Exo：保留默认开启的动态调度，开启 MediaCodec 视频帧进度调度；默认适配器在 API 31+ 的异步路径满足前提，较旧 API 不保证触发。允许兼容的音频独占播放使用 offload，并记录实际回调；有视频时由 Media3 保持普通播放。保留 FLAC flush 修复与 SurfaceView。
- VLC：保留 Android 默认音频输出；普通未变换本地视频优先直接输出，旋转等情形保留复制路径。软件解码与实际硬解分类继续分别核验。

两条分支的依赖解析、锁文件刷新、各自 build-logic 构建与 Wrapper 更新已通过。Wrapper JAR SHA-256 与官方 `3b8a25775a69158b5ad2b1d17a80a88dc7b40a352a73eb27d06c612a7ce68e98` 一致。Kotlin 的官方 fully-supported 表尚未覆盖整组最新 AGP/Gradle，实际编译与测试结果须单列。

共同的 AndroidX Core 与 Core KTX 均锁定为 1.19.1。UI 模块及可选播放器适配模块通过依赖约束对齐应用基线，避免单独构建模块时回落到 VLC 间接引入的旧 Core；约束不向原本未使用它的模块额外引入组件。

原生源码物化、全新 APT/SDK 投影、独立 composition smoke、两份 preparation、namespace probe 和双 ABI 原生重建均已通过。20 个库已通过 ELF/API/导入符号、依赖闭包与 JNI 合同审计，执行凭据 SHA-256 为 `15852d91f9c9a5e3d6e980bc154a98c676dcf9f2868671ca955e3379a0ea675e`（82,777 字节）。新 staging 已在两条分支分别验证；旧 staging 与 lock 均已归档。

两种 ABI 的生成配置均启用 Android Media NDK、Android EGL、libplacebo 和 AAudio，Vulkan 保持禁用。AAudio 的编译可用性不代表它已成为默认音频输出；本轮保留现有音频选择策略。

离线检查已通过：原生工具 310 项（环境条件跳过 27 项）、功耗汇总 18 项、夜间计划 8 项、缺失收集证据回归 1 项、控制器 10 项、接续 17 项（Windows Bash 环境跳过 4 项）、重复性守护 13 项，以及使用 ASan/UBSan 的 VLC seek bridge 主机检查。两个 AndroidX helper AAR 的目标 ABI ELF、16 KiB 对齐、SONAME/NEEDED 及字幕 AAR 的 19 个 JNI 签名另经独立核验。

## 最终构建与产物校验

刷新锁文件和校验元数据后，两条分支均以 `--offline` 运行完整的相关单元测试、主 APK 与 instrumentation APK 构建以及 `lintDebug`，不再写入验证元数据。功耗分支 231 项 JVM 单元测试通过，测试分支 214 项通过，均无失败或跳过；`feature:settings` 没有测试源码。lint 均无错误，分别报告 12 项和 10 项 warning，本记录不将其视为已消除。

实际 APK 校验结果：

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| 功耗主 APK | 388449680 | `46a0c824d23cf00b452d4db44e33171decef93475fc218f05009d75ad3de1aee` |
| 功耗测试 APK（流式哈希修复后） | 29061079 | `17192b56c846a8381586abc60470eb0642ed06631b30e72b5d504fffe97de6c7` |
| 测试分支主 APK | 292379748 | `5da4f18f33883721dae2dd0672510666d8809e841b13948a38dee2d000e7d746` |
| 测试分支测试 APK | 3048511 | `db8ead1ef15daec071d575df1f0cf694d19d2365f92b20e29d82b62ce86240f0` |

功耗主 APK 的 34 个原生库、158 个 DEX JNI 方法及 16 KiB ZIP/ELF 对齐检查通过；测试分支主 APK 的 20 个受审计库与 4 个锁定 AndroidX helper、16 个 DEX JNI 方法及 ZIP 对齐通过。两份测试 APK 均未携带 `.so`。实际打包的 C++ 运行库与本轮锁定的 NDK 29 来源一致。

VLC seek bridge 重建后的字节变化已单独检查：两种 ABI 的代码段、只读数据、动态依赖、重定位、JNI 导出和程序头均相同，差异限于包含 SDK/CMake 构建路径的 DWARF 调试信息和 build-id。记录完整 section 散列后，仅更新桥接输出的大小和 SHA-256；源码、ABI、JNI 和依赖约束继续保留。

本机产物位于 `native/out/dependency-upgrade-20261017/artifacts/{power,test}`，每组包含主 APK、测试 APK 和独立校验报告；汇总见同级 `artifact-manifest.json`，桥接对比见 `bridge-audit/section-comparison.json`。这些是调试与实验用产物，原生执行凭据仍为 `releaseInput=false`，本轮未生成新的 release APK。

最初本机构建收尾时 ADB 尚未发现手机；后续已通过无线调试完成下述真机验证。用户随后明确要求现在仅验证功能，功耗实验留到睡前，因此本轮未启动 `PowerPlaybackBenchmarkTest` 或夜间 runner，也未切换飞行模式、蓝牙、亮度或系统音量等功耗环境。

## 真机功能验证

设备为 `23049RAD8C / marble`，Android API 35。安装前已备份原主 APK、原测试 APK、数据库及用户资源，分别校验散列；原主 APK 为 `0e017cea…`，原测试 APK 为 `2f48a9b7…`。安装的新主 APK 在设备端再次核对为上表的 `46a0c824…`。

本轮使用实际应用的 Activity、MediaSession、PlaybackService 与 backend，运行 `ClarityCaptureTest`，不是独立的原始播放器对照。六个功能用例均完成，instrumentation 为 `OK (1 test)`，各自 `completed=true`、`cleanupSucceeded=true`：

| 核心 | AVC 自动解码与显示恢复 | AV1 软件请求播放 | 实际记录 |
| --- | --- | --- | --- |
| libmpv | 通过 | 通过 | AVC：`gpu-next / mediacodec / aimagereader`；AV1：`gpu-next / decoder=no / opensles` |
| ExoPlayer | 通过 | 通过 | AVC：`c2.qti.avc.decoder`；AV1：`c2.android.av1-dav1d.decoder / audiotrack / audioOffloaded=false` |
| libVLC 4 | 通过 | 通过 | 直接视频输出已确认；本测试未提供实际 decoder 名称，不据此宣称硬解分类通过 |

三核心的 AVC 用例均覆盖播放推进、暂停、定位至约 2 秒、FIT/FILL/STRETCH、竖横屏切换、暂停状态 Activity 重建和 PiP 往返，并检查恢复后的可见目标。主代理另已目视检查各核心的恢复截图及 AV1 彩条/时钟画面。VLC 通过 SurfaceControl 子层输出，父 Surface 无 buffer 是该路径的预期证据形态，屏幕回读仍可见视频。

首次 AV1 尝试暴露了测试工具的堆限制：`ClarityCaptureTest` 在准备媒体之前用 `readBytes()` 一次性分配约 281 MB，超过设备的 256 MiB Java 堆。已将样片及截图的 SHA-256 改为 8 KiB 缓冲流式读取，离线重建 instrumentation APK 后，三核心 AV1 功能用例全部通过。主 APK 未改变；此前 AVC 用例使用的测试 APK 为 `8f88b962…`（28,626,561 字节），修复后的测试 APK 为上表的 `17192b56…`。失败尝试和两组测试 APK 身份均保留在证据中。

收尾已恢复原测试 APK，保留更新后的主应用，删除本轮创建且已归档校验的两份临时素材及九个测试结果目录。数据库 schema、8 张表共 1002 行的逻辑内容及偏好文件散列与备份完全一致；飞行模式、Wi-Fi、蓝牙、亮度、亮度模式、屏幕超时、保持唤醒设置及音乐音量/静音状态均与开始时一致。

证据位于 `native/out/dependency-upgrade-20261017/device-preflight-20261017/`，汇总为 `functional-verification.json`，数据与设置核对为 `user-data-comparison.json`、`device-cleanup.json`。现有捕获日志还出现系统媒体封面 FileProvider 授权异常，以及 VLC 对 `libcuda.so.1` 的设备探测失败；本轮未观察到它们导致播放或显示恢复失败，封面问题仍需另行排查来源。未将短时间功能成功扩大为持续帧率、HDR、纯音频 offload、长时稳定性或省电结论。

### 随后的开发分支安装

用户随后要求亲自观看更新后的开发版视频效果，已将 `home-feishu-style` 的主 APK 覆盖安装到同一手机，设备端 SHA-256 核对为 `5da4f18f33883721dae2dd0672510666d8809e841b13948a38dee2d000e7d746`，正常启动至前台。该代码线使用 libmpv，默认 `gpu-next`；安装前再次备份了当前数据，并确认两线 Room v3 schema 一致，使用保留数据的覆盖安装。证据位于 `native/out/dependency-upgrade-20261017/development-install-20261017/`。

因此，手机此时保留的是开发分支包。后续三核心功耗实验启动前，需要安装已冻结的功耗分支包并重新捕获当时的设备基线；不能沿用此前实验包仍在手机上的假设。

## 新夜准备入口

新 APK 完成检查并在手机上通过播放预检后，在新目录备份设备状态与原测试 APK。`prepare-power-night.py` 新增 `--artifact-report`、`--original-test-apk`、`--mpv-video-output`、`--codecs`、`--full-matrix`；后者禁用旧 Exo 均值触发的跳过规则，适合建立升级后的基线。比如 `--codecs avc --full-matrix` 包含三核心的 AVC 硬解平衡块及条件 AV1 软件块。

工具冻结 APK 身份、原生 manifest 散列、实际 APK 校验报告、包含未跟踪源码的源快照、codec 日志 helper 与渲染配置。收集及汇总检查实际 APK 身份和 mpv 两端实际渲染器；不跨 APK 或渲染配置拼接 ABBA/BAAB。

会话名只接受 1–20 个 ASCII 字母或数字，与手机 runner 的约束一致。缺少仪器日志、events 或 decisions 时，收集器保留审计信息并拒绝将受影响的行计为合格测量。会话末尾的设备恢复结果仍单独保存在 runner exit 记录中，不代替逐腿的测量资格核验。

## 上游核验来源

- [Gradle 9.8.1](https://docs.gradle.org/9.8.1/release-notes.html)、[AGP 9.4](https://developer.android.com/build/releases/agp-9-4-0-release-notes)、[Kotlin 发布](https://kotlinlang.org/docs/releases.html)、[KSP 2.3.12](https://github.com/google/ksp/releases/tag/2.3.12)。
- [MIUIX 0.9.4](https://github.com/compose-miuix-ui/miuix/releases/tag/v0.9.4)、[Media3](https://developer.android.com/jetpack/androidx/releases/media3)、[Room](https://developer.android.com/jetpack/androidx/releases/room)。
- [mpv-android 官方方案](https://github.com/mpv-android/mpv-android/releases)、[后续依赖更新](https://github.com/mpv-android/mpv-android/commit/687c7fd586d826fd0387809e14bec7a2b4fc45a1)。
- [Media3 功耗选项](https://developer.android.com/media/media3/exoplayer/battery-consumption)、[mpv 渲染器差异](https://github.com/mpv-player/mpv/wiki/GPU-Next-vs-GPU)。
