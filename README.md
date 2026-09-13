# ZivPlayer

ZivPlayer 是一款面向多平台的开源媒体播放器，关注格式兼容、字幕体验和流畅播放。目前优先开发 Android 版本，各平台客户端采用原生语言和工具链。

目前处于开发阶段，优先完善 Android 的播放体验与稳定性。

## 当前功能

- **文件夹媒体库**：添加本地文件夹，扫描和浏览音视频，支持搜索、排序、收藏与隐藏。
- **播放列表与播放记录**：按目录播放、管理队列，通过迷你播放条返回播放器，支持最近播放和断点续播。
- **封面与媒体信息**：读取音乐内嵌封面、视频缩略图及标题、艺术家、专辑、时长和编码信息，支持查看媒体详情。
- **横竖屏播放**：沉浸式播放器、横竖屏布局适配、画中画，以及适应、填充和拉伸三种画面模式。
- **触控手势**：左 / 右双击后退 / 快进 5 秒，中央双击暂停 / 继续；横滑调整进度，左 / 右竖滑调整亮度 / 音量，长按临时变速，进入与松手退出时提供短促触觉反馈。
- **实时定位预览**：横滑画面或拖动进度条时更新主画面，松手后精确定位。
- **音轨与字幕**：切换音轨和内嵌字幕，导入外部字幕与字体。
- **播放设置**：倍速、循环播放、后台播放策略及 mpv 用户着色器配置。

Android 版本使用 Kotlin、Jetpack Compose 和 MIUIX 构建界面，正式基线以 libmpv 作为播放引擎，Media3 负责系统媒体控制和播放服务集成。当前实验分支使用 ExoPlayer + libass 进行对比，范围与回退方式见 [ADR 0017](docs/adr/0017-exoplayer-libass-comparison.md)。

## 平台与开发方向

当前 Android 最低支持 **Android 8.0（API 26）**，构建目标为 `arm64-v8a` 和 `x86_64`。实际设备验证范围见[验证记录](docs/validation/2026-09-10-player-completion.md)。

后续平台将分别实现原生界面和系统集成，共享稳定的播放行为约定，并按需要复用底层原生库。现有 Kotlin 核心保持模块隔离，Kotlin Multiplatform 是可选方案。其他平台的实现和发布时间尚未确定。

仓库以 `main` 保存正式基线，通过目录和模块区分职责，后端对比使用独立实验分支。跨平台与代码共享的具体边界见[架构规划](docs/adr/0015-native-platforms-and-shared-contracts.md)。

## 本地构建

需要 JDK 21、Android SDK 37、Build Tools 37.0.0 和 Python 3.11+。构建使用仓库自带的 Gradle Wrapper。

**首次克隆还需要准备经过校验的原生库。** `native/out/android` 是被 Git 忽略的构建产物；普通 Gradle 构建不会自动编译 libmpv。准备步骤见[开发指南](docs/development.md)。

准备完成后，在仓库根目录执行：

```powershell
python -B native/tools/android_staging_tool.py verify
.\gradlew.bat :apps:android:assembleDebug
```

APK 输出路径：`apps/android/build/outputs/apk/debug/android-debug.apk`。

## 项目文档

- [文档导航](docs/README.md)：模块说明、架构决策与验证记录。
- [开发指南](docs/development.md)：环境准备、构建和测试。
- [跨平台架构规划](docs/adr/0015-native-platforms-and-shared-contracts.md)：原生客户端、共享契约和仓库组织。
- [原生依赖与构建](native/README.md)：libmpv、JNI、源码锁定和构建产物校验。

## 开源许可

ZivPlayer 使用 [GPL-3.0-or-later](LICENSE) 许可证。第三方组件保留各自的版权和许可条款，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
