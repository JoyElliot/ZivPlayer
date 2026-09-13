# ZivPlayer 文档

[返回项目首页](../README.md)

## 从这里开始

| 你想了解什么 | 阅读入口 |
| --- | --- |
| 项目功能、支持平台 | [项目首页](../README.md) |
| 准备环境、构建 APK、运行检查 | [开发指南](development.md) |
| 各平台原生开发与代码共享策略 | [跨平台架构规划（ADR 0015）](adr/0015-native-platforms-and-shared-contracts.md) |
| libmpv 原生依赖、JNI 与构建产物 | [原生构建说明](../native/README.md) |
| 已完成的验证及其范围 | [拖动延迟、封面、元数据与触觉反馈](validation/2026-09-10-player-completion.md) |

## 当前模块

仓库目前只有 Android 应用。多个 Gradle 模块共同组成这个应用，各模块的职责如下；模块独立不等于已经具备跨平台二进制接口。

| 目录 / 模块 | 职责 |
| --- | --- |
| `apps/android` | Android 应用入口、导航与界面集成 |
| `core/model` | 媒体、轨道等基础模型 |
| `core/media-api` | 媒体来源、资料库与播放记录的接口 |
| `core/player-api`、`core/player-runtime` | 播放命令、状态、事件和串行状态机 |
| `data/media-android` | Android 文件访问、媒体索引与本地持久化 |
| `feature/library`、`feature/player`、`feature/settings` | 媒体库、播放器和设置功能 |
| `platform/libmpv-android` | libmpv 与 JNI 的 Android 适配 |
| `platform/playback-android` | Media3、播放服务与系统媒体控制 |
| `ui/design-system-miuix` | 应用使用的主题与 MIUIX 组件封装 |
| `native` | 原生源码和工具链锁定、包装层、构建与校验工具 |

实际模块清单以 [settings.gradle.kts](../settings.gradle.kts) 为准。

## 架构决策

ADR 记录某项设计的背景、决定和影响。以下按编号排列；历史实施阶段的描述应结合后续决策与验证记录阅读。

| 编号 | 主题 |
| --- | --- |
| [0001](adr/0001-android-kotlin-and-miuix.md) | Android 原生应用与 MIUIX 设计系统隔离 |
| [0002](adr/0002-libmpv-and-media3-boundary.md) | libmpv 播放引擎与 Media3 系统适配边界 |
| [0003](adr/0003-android-sdk-abi-and-toolchain.md) | Android SDK、ABI 与工具链基线 |
| [0004](adr/0004-dependency-and-native-supply-chain.md) | 依赖审查、锁定与原生供应链 |
| [0005](adr/0005-pure-player-contract-and-serialized-runtime.md) | 纯 Kotlin 播放契约与串行运行时 |
| [0006](adr/0006-bootstrap-libmpv-aar-is-not-a-release-artifact.md) | 临时 libmpv AAR 的使用边界 |
| [0007](adr/0007-media-session-service-and-surface-leases.md) | 播放服务与画面 Surface 的所有权 |
| [0008](adr/0008-durable-media-identity-and-local-history.md) | 持久媒体身份与本地播放记录 |
| [0009](adr/0009-single-item-playback-ui-and-controller-state.md) | 播放界面与控制器状态 |
| [0010](adr/0010-source-built-libmpv-and-native-provenance.md) | libmpv 源码构建与产物来源 |
| [0011](adr/0011-typed-per-instance-libmpv-jni-bridge.md) | 按实例隔离的类型化 JNI 桥接 |
| [0012](adr/0012-library-preferences-and-managed-playback-resources.md) | 媒体库、偏好与托管播放资源 |
| [0013](adr/0013-playback-interactions-and-folder-queues.md) | 播放交互与文件夹队列 |
| [0014](adr/0014-folder-home-and-portrait-player.md) | 文件夹首页、横竖屏播放器与转屏修复 |
| [0015](adr/0015-native-platforms-and-shared-contracts.md) | 各平台原生开发与共享行为契约 |
| [0016](adr/0016-live-seek-preview-and-exact-commit.md) | 主画面实时定位预览与精确提交 |
| [0017](adr/0017-exoplayer-libass-comparison.md) | ExoPlayer + libass 对比实验与回退边界 |
| [0018](adr/0018-local-artwork-and-media-details.md) | 本地封面、媒体元数据与独立缓存 |

## 验证记录

这些文档保存对应日期的命令、结果和验证边界。测试通过的设备、功能和构建范围各不相同；历史记录中的本地路径与一次性构建目录不能直接作为新环境的操作步骤。

| 日期 | 范围 |
| --- | --- |
| [2026-09-10](validation/2026-09-10-player-completion.md) | 拖动输入延迟、封面、元数据、触觉反馈与数据保留 |
| [2026-09-10](validation/2026-09-10-flac-seek-recovery.md) | 当前 MKV 的 FLAC seek 超时、音频就绪与全屏回归 |
| [2026-09-09](validation/2026-09-09-exoplayer-libass-comparison.md) | ExoPlayer + libass 原视频 Surface、长进度拖动与字幕对比 |
| [2026-09-09](validation/2026-09-09-scrub-suppression-and-surfaceview.md) | 拖动时临时暂停、大跨度预览、SurfaceView 与音频焦点 |
| [2026-09-09](validation/2026-09-09-live-seek-preview.md) | 实时拖动、双击出帧延迟与精确定位 |
| [2026-09-08](validation/2026-09-08-folder-home-and-portrait-player.md) | 文件夹首页、手势、横竖屏切换与画面比例 |
| [2026-09-07](validation/2026-09-07-playback-interactions.md) | 播放交互与文件夹队列 |
| [2026-09-07](validation/2026-09-07-personal-device-expansion.md) | 媒体库、设置、字幕资源与个人设备验证 |
| [2026-09-06](validation/2026-09-06-personal-device-expansion.md) | 个人设备扩展功能的早期验证 |
| [2026-09-05](validation/2026-09-05-initial-build.md) | 首次构建、原生播放与基础设备验收 |

## 文档维护约定

- 简体中文是主要维护语言，根目录 `README.md` 保持中文。新增介绍、指南和架构决策优先使用中文。
- 可按需要增加其他语言版本，并在对应页面提供语言切换链接；译文应与中文版本同步，尚未完成的译文不设置空链接。
- 命令、代码符号、路径、配置键、错误码、哈希和许可证标识保留原文。第三方许可证及历史审计原文保留其原始内容。
- README 用于介绍项目和提供入口。环境操作放在开发指南，设计取舍放在 ADR，设备结果和构建证据放在验证记录或原生技术文档。
- 明确区分已实现功能、未来计划和已验证范围。文档整理不将历史检查结果改写为新的验证结论。
