# 2026-09-08：文件夹首页、横竖屏播放器与手势验证

## 实现范围

实现决定见 [ADR 0014](../adr/0014-folder-home-and-portrait-player.md)。

- 首页按真实目录展示文件夹列表，提供搜索、排序、分类和固定迷你播放条；来源管理、最近播放与设置有独立入口。
- 搜索结果绑定查询及索引快照；异步加载不丢失恢复的目录；返回列表保留浏览状态。超过 500 项时单项仍可打开，播放全部明确提示队列上限。
- 横竖屏统一沉浸播放器：左/右双击分别后退/快进 5 秒，中央双击暂停/继续；左侧竖滑调窗口亮度，右侧竖滑调音量；保留单击显隐、横滑进度、长按变速、锁定与快捷菜单。
- 手势能力更新不打断同一媒体的双击；媒体切换清除旧反馈；紧凑布局统一按可用宽度判断，避免临界宽度丢失播放按钮。
- 视频改用 TextureView 与控制层在同一窗口合成，保留显示节点及服务会话；原生缓冲区按视频比例固定尺寸，显示层负责适应、填充和拉伸。横竖旋转不改变原生输出几何，保留系统动画，不跳转或临时恢复播放。
- 视频比例优先使用所选轨道，贯通像素比例及旋转元数据，覆盖 90/270 度旋转与非方形像素。

## 主机验证

原始日志、APK 审计、录屏和截图位于忽略目录 `native/out/folder-player-20260908-recovery/`。

```powershell
.\gradlew.bat --offline :core:model:test :platform:playback-android:testDebugUnitTest `
  :platform:libmpv-android:testDebugUnitTest :apps:android:testDebugUnitTest `
  :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest `
  :apps:android:lintDebug :platform:playback-android:lintDebug :platform:libmpv-android:lintDebug `
  :feature:library:testDebugUnitTest :feature:player:testDebugUnitTest `
  :feature:library:lintDebug :feature:player:lintDebug :ui:design-system-miuix:lintDebug
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk `
  --report native/out/folder-player-20260908-recovery/final-artifact.json
```

`final-build.log`：403 项 Gradle 任务成功，21 项执行、382 项为最新状态。六个模块的 JVM 测试共 146 项，失败、错误和跳过均为 0。

| 模块 | JVM 测试 | Debug lint Error / Warning |
| --- | ---: | ---: |
| core/model | 3 | 不适用 |
| platform/playback-android | 50 | 0 / 0 |
| platform/libmpv-android | 70 | 0 / 0 |
| apps/android | 10 | 0 / 7 |
| feature/library | 2 | 0 / 0 |
| feature/player | 11 | 0 / 3 |
| ui/design-system-miuix | 未执行 JVM 测试 | 0 / 1 |

最终 Debug APK：`apps/android/build/outputs/apk/debug/android-debug.apk`。
SHA-256：`aef424007d90f31c9e5018255f696b50f73af5911c426a4e468849c10923936a`。
`final-artifact.json` 确认 20 个凭据原生库、4 个锁定 AndroidX helper、16 KiB ZIP 对齐及 16 个 DEX JNI 方法检查通过。
审计工具自身的运行时注册字段仍为 `pending-device-art-smoke`；原生运行时验证由下述真实设备冒烟日志单独提供。
未重建原生栈、修改数据库 schema 或改动媒体授权策略。

## 真机验证

设备为 23049RAD8C、Android 15、中文界面、1080 × 2400；使用保留数据的覆盖安装。
自动化执行仅使用现有已授权的生成样本目录 `ZivPlayer-Expansion-20260907` 和 instrumentation fixture provider。

最终执行 `final-device-tests.log`：`OK (26 tests)`，117.539 秒。
`device-identity.json` 核对手机上主应用和 instrumentation APK 的哈希均与本地最终构建一致。

| 用例 | 数量 | 覆盖 |
| --- | ---: | --- |
| LibraryHomeUiTest | 6 | 目录与搜索队列、隐藏来源、500 项边界、异步目录恢复 |
| FullscreenGestureTest | 10 | 双击、竖滑、长按、取消/多指/锁定、能力变化 |
| PlayerUiContractTest | 5 | 播放控件、临界宽度、最近播放与迷你播放条入口 |
| LaunchSmokeTest | 1 | 启动与导航 |
| LibraryPlaybackUiTest | 1 | 真实服务队列、亮度/音量、临时变速、三次暂停转屏、FIT/FILL/STRETCH、返回筛选列表 |
| VideoDisplayGeometryDeviceTest | 1 | 优先选择当前视频轨与无视频轨回退 |
| NativePlaybackSmokeTest 指定方法 | 1 | 原生播放、字幕/音轨、重播与 Surface 重建 |
| PersonalDevicePlaybackTest 指定方法 | 1 | 全屏、PiP 与后台策略 |

```powershell
$classes = @(
  'LibraryHomeUiTest', 'FullscreenGestureTest', 'PlayerUiContractTest', 'LaunchSmokeTest',
  'LibraryPlaybackUiTest', 'VideoDisplayGeometryDeviceTest',
  'NativePlaybackSmokeTest#sourceNativePlaybackTracksSubtitlesReplayAndSurfaceRecreation',
  'PersonalDevicePlaybackTest#fullscreenPictureInPictureAndBackgroundPolicies'
) | ForEach-Object { 'io.github.joyelliot.zivplayer.' + $_ }
adb -s f4b5356a shell am instrument -w `
  -e personalFixtureFolder ZivPlayer-Expansion-20260907 -e class ($classes -join ',') `
  io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
```

此前稳定帧测试通过后，用户指出连续转屏仍有短暂错位、拉伸或残影，并确认禁用动画也未解决实屏闪现。
最终采用固定原生缓冲区和按比例布局的显示节点，恢复系统动画。用户再次用重力切换竖屏与横屏，明确确认“重力旋转正常，我已测试”。
这次用户验收对应 APK `0599efdcae31abe3ad6bffa9fd3097a79a28cad38f443670001a5f7a903d2c0e`；随后补齐视频比例元数据，最终包保持相同转屏实现并通过上述完整回归。

暂停旋转断言覆盖首次暂停的竖→横，以及队列切换后的横→竖→横：

- 采样生成彩条帧的上下区域，检查画面位于居中的 16:9 范围。
- 直接读取旋转前后 TextureView 的视频像素，比较同一暂停帧，排除控制层文字干扰。
- 监听真实 MediaController，要求旋转期间没有恢复播放及 SEEK/SEEK_ADJUSTMENT 事件，播放身份和暂停状态一致。
- 后端 JVM 用例验证重复重绘不改写 `pause`、`time-pos` 或当前 `window-scale` 值，并拒绝旧 surface lease。
- 用实际屏幕彩条范围和颜色数量区分 FIT 的黑边、FILL 的裁剪及 STRETCH 的拉伸。

界面进度每 500 ms 采样，暂停后的重绘可能发布最终视频帧时间；不把 UI 进度采样差误判为 seek。
最终包截图包括 `product-player-portrait.png`、`product-paused-landscape.png` 和 `product-video-fit-{fit,fill,stretch}.png`。

固定缓冲区版本另有 `fixed-render-transition.mp4`、`fixed-render-rotation-tests.log`（24.621 秒）、
`fixed-render-fit-tests.log`（26.012 秒）及 `fixed-render-pip-tests.log`（34.337 秒）。
`rotation-review.mp4` 是固定缓冲区录屏从第 17 秒起截取的 16 秒片段。
旧 `jumpcut-*`、`final-transition-recording.mp4` 等文件只保留为未通过版本的调查证据。
录屏为可变帧率；[Android 官方文档](https://developer.android.com/tools/adb#screenrecord)说明 screenrecord 不支持录制期间旋转，因此连续视觉验收同时采用用户的实体屏幕确认。

## 数据、清理与验证边界

`data-preservation.json` 对比安装前后应用私有数据：数据库完整性正常；26 条个人历史全部保留；2 个授权来源、35 条媒体索引、4 个既有播放资源及文件、DataStore 设置、个人外部字幕记录均保持一致。
个人历史中 3 条仅更新最近打开时间和播放检查点，更新时间为香港时间 14:02:52–14:03:25；另有 1 个媒体的所选字幕/音轨更新。
这些时间与用户约 14:02 的手动播放和重力旋转验收吻合。没有自动回滚用户的播放进度和选轨；不把最终快照描述为逐字节完全不变。

用户指出的异常目录经检查含 4 个空目录、0 个文件、0 个重解析点，没有 Git 跟踪或项目引用。
已逐层删除空目录，无需重建；证据为 `probe-cleanup.json`。

没有修改设备的常亮和息屏超时设置，没有清空应用数据，也没有运行独立的音频焦点干扰用例。
本轮只构建及运行 Debug；按功能和修复拆分本地提交，没有推送远端。
其他设备、大字体、特殊刘海布局、TextureView 功耗与 HDR、复杂 ASS 仍未覆盖。
填充模式会一起裁剪视频和已由 mpv 合成的字幕。
