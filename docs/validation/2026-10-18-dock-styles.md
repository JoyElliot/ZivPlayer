# 2026-10-18：液态玻璃与简洁底栏

## 结果与范围

在 `home-feishu-style` 分支恢复液态玻璃导航与上方半包围播放卡片为默认，将现有扁平底栏保留为第二方案。设置入口：“设置 → 播放与外观 → 底栏样式”。缺少新键的旧偏好采用玻璃默认值，保留其他设置。方案和几何依据见 [ADR 0022](../adr/0022-selectable-home-dock.md)。

原卡片恢复自 `***` 的定型实现；两种外观的 `MiniPlayer` 都只提供视频槽位，视频继续由同一个 `PersistentVideoStage` 显示。槽位观察祖先图层的平移/缩放；玻璃布局使用稳定系统栏间距。独立只读复核对比了历史几何，并核对当前锁定的 [Compose UI 1.12.1 源码](https://dl.google.com/dl/android/maven2/androidx/compose/ui/ui-android/1.12.1/ui-android-1.12.1-sources.jar) 中图层矩阵、子树矩形更新和无 debounce 回调路径。

未改实验分支、播放引擎、原生参数和依赖版本。未推送远端。用户本轮只授权完成后静默安装，因此未启动应用、注入触摸/按键、执行 instrumentation 或修改系统设置。

## 主机验证

使用既有 JDK 21、Android SDK 和 Gradle 缓存，严格离线构建，不重写锁文件或依赖校验元数据：

```powershell
.\gradlew.bat :core:model:test :data:media-android:testDebugUnitTest :apps:android:testDebugUnitTest :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest :apps:android:lintDebug --offline --no-daemon --console=plain
python tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk --report build/dock-styles-20261018/apk-audit.json
```

构建成功，1 分 3 秒；374 项任务中 126 执行、248 已最新。相关 JVM 测试共 **43 项**（model 3、data 22、app 18），失败、错误和跳过均为 0。新增 codec 验证覆盖升级缺键、无效值回退、双方案持久化、重置和无关播放选项保留。Lint 为 **0 error、8 warning**，与前轮相同。

新增 `NavigationStyleContinuityTest` 两个 Compose 用例已编译进测试 APK：双底栏切换及展开/收起保持 TextureView 对象，祖先只改变图层位移/缩放时更新槽位。**本轮没有执行这些用例**，也未把此前版本的真机结果当作本次验收。真实动画观感、逐帧延迟、玻璃合成和实际播放连续性仍未在本轮测量。

主 APK 审计通过：20 个受审计原生库、4 个锁定 AndroidX helper、16 个 DEX JNI 方法以及 16 KiB ZIP/ELF 对齐。仍是 debug APK，不改变 native 收据的 `releaseInput=false`。

## 静默安装

2026-10-18 11:18（Asia/Taipei），仅向先前同一台 Xiaomi `marble` 手机执行 `adb install -r`，返回 `Success`。随后通过包路径读取 APK SHA-256，与主机产物一致。没有打开应用或回装旧版本，也没有安装测试 APK。覆盖安装保留应用数据；未执行清数据或数据恢复。

| 产物 | 字节数 | 已核对的 SHA-256 |
| --- | ---: | --- |
| `ZivPlayer-glass-and-simple-20261018.apk` | 293297731 | `6f56c3bb869ba9411dfd30857e6dac0e3bc0f7ab3071b5e75175d5461ccc3d9a` |

源码提交：`***`（偏好与设置，47 行）和 `***`（双外观、视频槽位交接与编译用例，328 行）；文档单独提交。每次新增加删除的文本行数均不超过 1000。

本机证据在忽略目录 `build/dock-styles-20261018/`：`validation-1.log`、`test-summary.json`、`apk-audit.json`、`install-output.txt`、`installation.json` 和冻结的主 APK。
