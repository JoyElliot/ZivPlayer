# 开发指南

[文档导航](README.md) · [项目首页](../README.md)

本文介绍当前 Android 应用的本地开发流程。所有命令均从仓库根目录执行；示例以 Windows PowerShell 为主。

## 环境要求

| 工具 | 当前要求 |
| --- | --- |
| JDK | 21；应用和核心模块的 JVM 编译目标为 17 |
| Android SDK | Platform 37、Build Tools 37.0.0 |
| Gradle | 使用仓库自带 Wrapper，当前为 9.6.1 |
| Python | 3.11 或更高，用于原生库和 APK 校验 |
| FFmpeg / FFprobe | 生成真机测试素材时需要 |
| Android 设备或模拟器 | 运行 instrumentation 测试时需要 |

SDK 路径可通过本机环境变量 `ANDROID_HOME` 或未跟踪的 `local.properties` 中的 `sdk.dir` 指定。JDK、SDK 和 Gradle 缓存属于开发环境，不提交到仓库。

依赖版本见 [版本目录](../gradle/libs.versions.toml)，Wrapper 版本见 [gradle-wrapper.properties](../gradle/wrapper/gradle-wrapper.properties)，SDK 与 ABI 见 [Android 构建配置](../apps/android/build.gradle.kts)。普通构建沿用仓库中的依赖锁和校验元数据。

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

```powershell
.\gradlew.bat --offline :apps:android:assembleDebug
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk
```

APK 校验检查原生库、AndroidX 辅助库、16 KiB ZIP 对齐和 DEX 中的 JNI 方法。APK 校验不能替代真机播放验证。`assembleRelease` 也仅表示相应构建任务完成，不代表已经具备正式发布所需的签名、原生来源和分发材料。

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

当前先完成 Android 开发，仓库保留 `main`。正式上线后再确定 beta 和稳定版的发布方式。
