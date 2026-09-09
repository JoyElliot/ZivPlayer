# ADR 0001：Android 原生应用与独立的 MIUIX 设计系统

- 状态：已接受
- 日期：2026-08-30
- 更新：2026-09-09；跨平台策略由 [ADR 0015](0015-native-platforms-and-shared-contracts.md) 补充

## 背景

ZivPlayer 面向多平台发展，首个交付目标是原生 Android 应用。在第二个平台开始前，为整个仓库启用 Kotlin Multiplatform 会提前引入源码集、插件和原生构建约束。

选用的 UI 库 `compose-miuix-ui` 是社区项目，并非小米官方 SDK；其公共 API 明确处于实验阶段。

## 决定

- 使用 Kotlin 和 Jetpack Compose 开发 Android 应用。
- 初始基线锁定 `compose-miuix-ui` 稳定版 `0.9.3`。
- 通过独立的设计系统模块使用它的 Android 产物。
- 功能模块使用 ZivPlayer 自己封装的主题和组件，避免在整个应用中直接依赖 MIUIX。
- 领域模型和播放契约保持独立，不引入 Android 或 UI 类型。各平台原生实现与代码共享的选择遵循 ADR 0015，Kotlin Multiplatform 保留为可选方案。
- 最低支持 Android API 26。

## 影响

MIUIX 升级集中在一个模块内，未来替换 UI 库也能减少对功能逻辑的影响。初始选型暂缓使用部分组件：`miuix-blur` 要求 API 33，而当时的导航模块仍在稳定版与候选版之间变化。

2026-09-09 的补充仅调整跨平台方向并整理中文表述，不改变当前 Android UI 技术栈。原先预留的 KMP 迁移路径不再作为后续平台的默认路线。
