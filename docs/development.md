# 开发指南

[文档导航](README.md) · [项目首页](../README.md)

本文介绍当前 Android 应用的本地开发流程。所有命令均从仓库根目录执行；示例以 Windows PowerShell 为主。

当前 `codex/libvlc4-comparison` 是 [libVLC 4 对比实验](adr/0018-libvlc4-comparison.md)分支，默认运行 libVLC 4.0.0-eap29。`-PplaybackEngine=exo` 或 `-PplaybackEngine=libmpv` 只切换后端，本分支 APK 仍包含三套依赖，统一使用下文的 VLC 实验校验参数。之前的完整 Exo/mpv 实验状态保留在 `codex/exoplayer-libass-comparison` 的 `***`，原 libmpv 基线保存在 `main` 的 `***`。

## 环境要求

| 工具 | 当前要求 |
| --- | --- |
| JDK | 21；应用和核心模块的 JVM 编译目标为 17 |
| Android SDK | Platform 37、Build Tools 37.0.0 |
| Android NDK / CMake | 26.1.10909125 / 3.22.1，用于独立的 VLC 定位事件 JNI 桥接 |
| Gradle | 使用仓库自带 Wrapper，当前为 9.8.1 |
| Python | 3.11 或更高，用于原生库和 APK 校验 |
| FFmpeg / FFprobe | 生成真机测试素材时需要 |
| Android 设备或模拟器 | 运行 instrumentation 测试时需要 |

SDK 路径可通过本机环境变量 `ANDROID_HOME` 或未跟踪的 `local.properties` 中的 `sdk.dir` 指定。JDK、SDK 和 Gradle 缓存属于开发环境，不提交到仓库。

依赖版本见 [版本目录](../gradle/libs.versions.toml)，Wrapper 版本见 [gradle-wrapper.properties](../gradle/wrapper/gradle-wrapper.properties)，SDK 与 ABI 见 [Android 构建配置](../apps/android/build.gradle.kts)。普通构建沿用仓库中的依赖锁和校验元数据。

本轮共用依赖、三核心运行配置和新功耗基线见[升级验证记录](validation/2026-10-17-dependency-upgrade.md)。libmpv 默认使用 `gpu-next`；功耗 instrumentation 可用 `-e mpvVideoOutput gpu` 对照旧渲染器，并核验运行时 `current-vo`。

## 准备原生库

Android 应用需要 `native/out/android` 中经过校验的 libmpv 栈和 JNI 包装层。该目录被 Git 忽略，首次克隆不会包含这些文件。

原生构建与 Android 应用构建是两个步骤：

1. 在 Linux 原生构建环境中，按锁定的源码、工具链与 wrapper 构建流程生成产物及执行凭据。详细步骤见[原生构建说明](../native/README.md)。
2. 使用 `native/tools/android_staging_tool.py export`，指定该次构建工作区和独立核对的凭据 SHA-256，导出匹配 [android-staging-lock.json](../native/android-staging-lock.json) 的完整 staging。需要转移到另一台应用构建主机时，保留完整目录与凭据文件。
3. 在应用构建主机上运行下列校验。它会核对锁定输入、凭据、ABI、文件清单和原生库字节。

```powershell
python -B native/tools/android_staging_tool.py verify
```

校验通过后再构建应用。缺少 staging 或字节不匹配会使 Gradle 构建失败；不要用任意下载的 `.so` 替换，也不要删除校验任务来绕过这个前置条件。

原生文档中的已消耗工作区与历史凭据仅用于追溯，不能直接复制成新的构建命令。当前原生检查产物与正式发布输入仍有区别，发布所需的剩余工作见[原生构建说明末尾](../native/README.md#remaining-native-gates)。

## 构建 Android 应用

环境和原生库准备好后执行：

```powershell
.\gradlew.bat :apps:android:assembleDebug
```

输出：`apps/android/build/outputs/apk/debug/android-debug.apk`。

首次构建可能需要联网获取已锁定的 Gradle 依赖。只有在本机缓存完整时，才使用 `--offline`；离线参数不会自动准备缺失依赖。

项目默认将应用构建的 Java 内存目标设为约 2 GiB。[根配置](../gradle.properties)使用 896 MiB 堆、768 MiB Metaspace 上限和 128 MiB 代码缓存，并将 JVM 可用处理器数设为 4、Gradle worker 设为 1。Kotlin 在 Gradle 进程内编译；独立的 [build-logic 配置](../build-logic/gradle.properties)也指定该策略，避免另起 Kotlin 守护进程。Gradle 空闲 10 分钟后退出。这是构建资源预算，不是操作系统级的内存硬限额；测试、Lint 的独立 JVM 仍有自己的开销。

```powershell
.\gradlew.bat --offline :apps:android:assembleDebug
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk --libvlc4-experiment
```

VLC 实验校验保留原 staging receipt 的 20 个库、4 个 AndroidX 辅助库和4 个 libass 库，再检查官方 AAR 的 4 个 VLC 库与两个 ABI 的独立定位桥接，共 34 个原生库、157 个 JNI 方法及 16 KiB 对齐。VLC AAR 的重复 C++ 运行库必须与原 receipt 字节一致，不能覆盖原校验值。桥接源码与输出散列锁在 `tools/libvlc4-experiment-lock.json`，修改桥接后需要重新构建并独立核对再更新散列；不同工具链或宿主路径生成不同字节时校验会拒绝。

旧 Exo 分支使用 `--exoplayer-experiment`，`main` 原基线使用无实验参数的严格默认校验；两者都应拒绝当前 VLC APK。APK 校验不能替代真机播放验证。`assembleRelease` 也仅表示相应构建任务完成，不代表已经具备正式发布所需的签名、原生来源和分发材料。

macOS / Linux 上的应用构建入口为 `./gradlew`，Python 命令通常为 `python3`。这只是构建主机的区别，当前应用目标仍为 Android。

## 本地检查

根据变更涉及的模块选择检查范围。以下命令覆盖核心、数据、播放器适配和界面的 JVM 测试：

```powershell
.\gradlew.bat :core:model:test :core:media-api:test :core:player-api:test :core:player-runtime:test `
  :data:media-android:testDebugUnitTest `
  :platform:libmpv-android:testDebugUnitTest :platform:playback-android:testDebugUnitTest `
  :feature:library:testDebugUnitTest :feature:player:testDebugUnitTest :feature:settings:testDebugUnitTest `
  :apps:android:testDebugUnitTest
```

Android 静态检查与测试 APK 编译：

```powershell
.\gradlew.bat :data:media-android:lintDebug `
  :platform:libmpv-android:lintDebug :platform:playback-android:lintDebug `
  :feature:library:lintDebug :feature:player:lintDebug :feature:settings:lintDebug `
  :ui:design-system-miuix:lintDebug :apps:android:lintDebug `
  :apps:android:assembleDebugAndroidTest
```

修改 `build-logic` 后，在它自己的构建边界内执行 `./gradlew -p build-logic build`；PowerShell 使用 `.\gradlew.bat -p build-logic build`。修改原生工具时，按其[测试说明](../native/README.md#source-cache-commands)运行对应检查。

## 设备验证

安装 FFmpeg / FFprobe 并连接测试设备后，先生成原创音视频与字幕素材：

```powershell
.\tools\create-device-fixtures.ps1
.\tools\create-seek-fixture.ps1
.\tools\create-metadata-fixtures.ps1
.\gradlew.bat :apps:android:assembleDebugAndroidTest
```

随后按验证目标选择设备用例。通用 instrumentation 入口是：

```powershell
.\gradlew.bat :apps:android:connectedDebugAndroidTest
```

该入口会运行测试套件，并不负责准备全部个人设备用例所需的目录与授权。涉及个人文件夹的用例还需要生成样本、SAF 授权及 `personalFixtureFolder` 参数，具体命令和前置条件见[设备验证记录](validation/2026-09-07-personal-device-expansion.md)及[最近一次分组选测](validation/2026-09-08-folder-home-and-portrait-player.md)。使用测试素材，并在执行前确认用例会修改哪些应用设置。

音频焦点、后台播放和系统媒体控制依赖设备实际行为。字幕显示、声音、旋转动画和画面连续性也需要对应的实屏或录屏检查；测试 APK 编译成功不代表这些项目已通过。

## 文档与发布

新增文档以中文为主，维护约定见[文档导航](README.md)。构建日志、设备结果和一次性工作区信息放入专门记录，不逐次追加到根 README。

当前先完成 Android 开发，以 `main` 保存正式基线，后端对比在独立实验分支进行。正式上线后再确定 beta 和稳定版的发布方式。
