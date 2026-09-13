# libVLC 4 实验包构建与主机验收

日期：2026-09-13。范围：[ADR 0018](../adr/0018-libvlc4-comparison.md)。未安装到设备、未继续 Exo/mpv 功耗测量，也未进行 VLC 真机播放验收。

## 保留与产物

| 项目 | 结果 |
| --- | --- |
| 新分支 | `codex/libvlc4-comparison` |
| 完整旧实验状态 | `codex/exoplayer-libass-comparison` → `***`，保存此前全部 91 项工作区改动 |
| 原 libmpv 基线 | `main` → `***` |
| 默认引擎 | `org.videolan.android:libvlc-all:4.0.0-eap29`（预览版） |
| 应用 ID / 版本 | `io.github.joyelliot.zivplayer` / versionCode `1`、versionName `0.1.0-dev` |
| 安装包 | `native/out/libvlc4-experiment-20260913/ZivPlayer-libVLC4-eap29-debug.apk` |
| 包大小 | 385,205,181 bytes |
| APK SHA-256 | `d4a75511dea36d5a64813597f65c169af815d03ed2ab985143414522f55e0777` |
| 签名证书 SHA-256 | `3a27207ed1817859b019edfa3226aa2ac84d8ab0961b8c3a3681a940c75d9cc7`，与保存的旧实验主 APK 一致 |

旧主 APK 与旧 test APK 保留在同一产物目录的 `previous-experiment-apks/`，均已复制后回读散列。设备数据、应用安装和系统设置未被本次工作修改。覆盖安装新包时无需卸载；媒体库/历史/设置 schema 未变。

## 已完成检查

1. 构建：`:apps:android:assembleDebug` 成功。BuildConfig 确认为 `EXOPLAYER_EXPERIMENT=false`、`LIBVLC4_EXPERIMENT=true`。NDK 26.1.10909125、CMake 3.22.1 为 arm64-v8a / x86_64 构建独立定位桥接。
2. JVM 检查：core player-runtime 46 项、libmpv-android 71 项、playback-android 50 项、app 10 项，共 177 项，0 失败、0 错误、0 跳过。核心未变，核心测试任务复用了有效结果；适配/应用任务执行通过。这些测试主要覆盖既有核心与服务契约，并非 Android 上的 VLC 渲染测试。
3. JNI 主机测试：在 WSL 使用实际 `vlc4_seek.c`，以公开 VLC API/JNI fixture 注入启动/完成/拒绝/关闭事件，覆盖请求不可重叠、无开始通知不能完成、重复完成失效、失败请求清理、原生线程 attach/detach、关闭后失效和重新打开。AddressSanitizer 与 UndefinedBehaviorSanitizer 检查通过。fixture 不替代真实 LibVLC 线程或 ART 验证。
4. Lint：`:apps:android:lintDebug` 成功，0 Error/Fatal、28 Warning；没有指向新增 libvlc 文件的告警。
5. 供应与打包：原 staging receipt 的 20 个库保持精确匹配。另有 4 个 AndroidX 辅助库、4 个 libass 库、4 个官方 VLC 库和 2 个自建桥接库，共 34 个；157 个 DEX native 方法描述符匹配。官方 AAR SHA-256 为 `2033a8552d7c62c2c2deb52568c91ceb97378c532cdb69e9d457755654cbc727`。
6. C++ 冲突：VLC 两个 64 位 ABI 的 `libc++_shared.so` 与原 receipt 字节完全相同，APK 继续核对原散列；不会覆盖原 receipt 来放宽校验。
7. 独立打包检查：原生库未压缩，ZIP 数据偏移和目标 ELF LOAD 满足 16 KiB。官方 `zipalign -c -P 16 4` 与 `apksigner verify` 通过，签名方案为 v2。
8. 负向门控：不带参数的原生基线 gate、仅带 `--exoplayer-experiment` 的 gate 均按预期拒绝本次 VLC APK；仅显式 `--libvlc4-experiment` 接受。
9. 依赖：三个受影响模块的旧锁定坐标全部保留；只增加 VLC 与所需传递依赖，没有升级既有 Kotlin 编译器或其它旧坐标。

主机证据位于 `native/out/libvlc4-experiment-20260913/`：`delivery.json`、`apk-verification.json`、`bridge-build-audit.json`、`dependency-audit.json`、`strict-gate-negative-checks.json`、`build-and-checks.log`、`core-tests.log` 和签名输出。APK 原生来源锁见 `tools/libvlc4-experiment-lock.json`。

## 手动测试重点

- 同一媒体正常打开、暂停/播放、历史位置恢复、前后台切换、旋转与画中画。
- 慢速拖动、快速横扫、反复松手提交、拖动后立刻切换媒体；留意画面是否停在最终位置及音频是否意外恢复。
- ASS/SSA/SRT、外挂字幕、音轨切换、关闭字幕、倍速及音频/字幕延迟。
- FIT/FILL/STRETCH，尤其非方形像素或带旋转元数据的视频。
- 自动/软件解码选择：修改后重新打开媒体；诊断不提供实际解码器名称，不能用设置值作为实际路径证明。

当前输出使用 VLC OpenGL ES 将字幕与视频合成至同一 Surface。未接入的 mpv 专有显示与字幕选项见 ADR/应用配置提示。VLC 的播放兼容性、实际解码路径、画质、定位时延、掉帧和功耗均尚无本次设备测量结论。
