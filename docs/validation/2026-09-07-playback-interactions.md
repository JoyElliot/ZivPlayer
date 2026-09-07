# 2026-09-07：全屏交互、文件夹首页与播放列表验收

## 范围与实现

本轮在既有真实 libmpv 播放链上补充产品交互，沿用 Kotlin、Compose、MIUIX 和服务持有的唯一引擎。
布局参考用户提供的 MX Player 截图，图标和代码由本项目实现。

- 全屏采用单行标题、音轨/字幕入口、细时间轴、居中的上一项/播放/下一项，以及锁定、倍率、适配和队列入口。
  窄窗口底部操作分成两行；播放中自动隐藏控制层，暂停、错误和操作时保持可见。
- 双击暂停/继续；横滑预览进度并在松手时提交一次；长按临时 2 倍速，按住横滑在 0.25–4 倍间调整。
  松手、取消、媒体切换和控制器断连由服务恢复原速度，后续显式速度选择优先。锁定后禁用视频手势。
- 右侧快捷菜单提供九宫格倍速、音轨、字幕、外置字幕导入、适配、音量、循环和播放列表。
- 首页按授权文件夹浏览，保留搜索、排序、收藏与隐藏；点击项目或播放全部使用当前筛选/排序快照，最多 500 项。
- 队列由服务和 core 持有。上一项/下一项、点选、自然连播、单曲/列表循环均不依赖页面保持可见。
  Media3 的添加、移动、替换、删除和清空按顺序执行；当前项仍存在时不重载、不重置进度或速率。
- 队列仅为当前媒体延迟打开 FD；Room v3 新增历史可见性，预登记的未播放项目保持隐藏。

实现决定见 [ADR 0013](../adr/0013-playback-interactions-and-folder-queues.md)。

## 主机验证

完整离线执行：

```powershell
.\gradlew.bat --offline test lintDebug lintRelease :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest :apps:android:assembleRelease :build-logic:test
python -B tools/verify-media-migration.py
.\tools\create-format-matrix.ps1 -VerifyOnly
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk --report native/out/product-interactions-20260907/final-debug-artifact.json
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/release/android-release-unsigned.apk --report native/out/product-interactions-20260907/final-release-artifact.json
```

完整 Gradle 检查通过，共 734 个任务。28 份 JVM 测试 XML 合计 202 项（不重复计算 Android Release 变体），
失败、错误、跳过均为 0。完整 Debug/Release lint 均执行；`lintVitalRelease` 的 SKIPPED 不作为 lint 通过依据。
迁移脚本分别比对 v1→v2、v2→v3 与导出的 Room schema，验证旧历史/checkpoint、默认可见性、唯一约束和级联删除。
九份既有格式夹具通过 FFprobe 核验，本轮没有重新生成或替换手机个人媒体。

最终显示修正后的增量构建、lint 和 APK 摘要见本文末尾最终产物记录。

## 真机验证

设备为用户现有 Android 15 / API 35 / arm64 手机（型号 `23049RAD8C`）。本轮只使用独立生成、已经授权的
`ZivPlayer-Expansion-20260907` 文件夹和 instrumentation fixture provider。继续排除独立的音频焦点测试，未调整 OEM 策略。

| 组合 | 通过项数 | 执行时间 |
| --- | ---: | ---: |
| 新增手势 6、服务队列/临时速度 4、历史注册 1、文件夹到全屏完整流程 1 | 12 | 52.539 秒 |
| 原有原生播放、文档契约、启动、基础 UI | 7 | 21.485 秒 |
| 格式矩阵、媒体库重扫/取消、全屏/PiP/后台、字体与 shader | 4 | 62.077 秒 |
| 准备并保存每项音轨/字幕偏好 | 1 | 5.935 秒 |
| 明确 force-stop 后以新进程验证偏好恢复 | 1 | 7.940 秒 |
| 180 秒九种格式轮换压力测试 | 1 | 184.605 秒 |

本轮累计 26 个独立设备用例通过。压力循环实际运行 180.629 秒，读取 168 份诊断；覆盖 AVC、HEVC Main10、AV1、2160p、PQ/HLG 测试信号及 Opus/FLAC/PCM，
用于反复加载、循环与诊断读取，不等于真实长片耐久、HDR 显示链或全面泄漏验收。

服务回归特别验证：快速添加后移动、替换后删除、删除当前项、清空后立即添加；保留当前项的编辑维持 period UID、
已播放位置及 1.5 倍速；编辑后立即暂停或停止不丢弃编辑、不自动恢复播放。用户已经预约的新打开请求不会被旧文件 EOF 抢占。
临时倍速验证松手、暂停、显式选择、切项、断连，以及旧 token 的延迟更新/结束。

触摸回归验证单击与双击互斥、横滑只提交一次、长按横滑进入慢放、取消/身份变化结束操作、时间轴取消与锁定。
真实 UI 流程验证文件夹筛选、播放全部、全屏长按变速、双击暂停、快捷菜单选速、队列切换和退出全屏。

## 数据保留

升级前取得当前应用范围内的数据库和文件快照。首次 v2→v3 打开后，13 条旧历史在排除新增可见性列后逐字段一致，
全部旧行默认可见，数据库 integrity 为 `ok`。测试只允许改变独立生成夹具的历史/偏好。

26 项用例完成后的再次快照确认：13 条历史仍在，原有两条非夹具记录及 checkpoint 全字段未变，摘要为
`d5db24aa1ec7a7c0800f15c521e893667a90baabd0c78aa1fb09816884f1849d`。
三个原有托管资源及文件内容均保留。全局 DataStore 设置前后 SHA-256 一致：
`9eab1f8d05da0697e0963a4cbc66a622db05018edcd613c2466a1cbde29e3dfb`。
本次以 `product-interactions` 的实时快照为基线，与前一轮独立验收记录的设置摘要不同。
应用自身更新了 profile 安装标记，并产生 mpv 缓存；未恢复或删除这些正常生成文件。

USB 调试测试期间按用户要求临时开启 USB 常亮，原始 `stay_on_while_plugged_in=0` 已记录；全部测试后恢复为 `0` 并读回确认。

## 最终产物记录

截图复核发现并修复了刘海屏横屏弹窗右缘裁切：Compose 1.12.0 默认使用可见显示区域裁剪，而全屏内容按窗口宽度测量。
抽屉显式关闭该裁剪路径，使测量和定位使用全窗口坐标，并继续为实际内容保留安全边距。
最终截图确认关闭按钮、音量 `100%`、滑块末端和队列右缘完整可见；六个手势用例与完整 UI 流程在该修正后通过。
锁定/菜单期间也不再暴露视频层的无障碍点击操作。

最后追加的 `setMediaItems → pause → append` 回归先复现了旧列表被编辑的问题；修复后重新执行完整服务四项和 UI 一项，
35.543 秒全部通过（`replacement-pause-device.log`）。它覆盖最新 Debug 包的队列安装、播放取消、临时速度及真实界面流程。
随后重新执行适用的服务 JVM 测试、两种完整 lint 和 Release 构建；增量 lint/Release 构建共 620 任务、33 秒成功。
最终两种 lint 各有 6 个 Warning、0 个 Error，涉及 PiP sourceRectHint、Modifier 默认值和 KTX，未称为 lint clean。

最终两种 APK 均通过 20 个凭据原生库、4 个锁定 AndroidX helper、ELF/ZIP 16 KiB 对齐和 16 个 DEX JNI 方法检查。
本轮未改变原生依赖构建输入或 JNI wrapper；静态报告中的运行注册待验字段由独立 Debug instrumentation 证据补充，
不能据此推断 Release 已运行。

| 产物 | SHA-256 |
| --- | --- |
| Debug，覆盖安装后从手机读取并与本地产物比对一致 | `eedb9191cbd5c94b1159df7aa50b3f243f07b9e4926747bfac29ca55431bb8cc` |
| Release unsigned，构建与静态检查 | `3f511b02bd8667b29d6d3c4ad34977653fd502a6f738dc3aa99222702ff972bd` |
| Debug instrumentation | `0aa8f9773fdf6f70142cc0b643589c5118b58a04d14f9f6272cfd2e8abafbc47` |

最后一次 UI 回归后再次取得 `final-data.tar`，重新确认原有两条历史、三个托管资源和全局设置均保持上述摘要。
最终界面截图为 `accepted-product-fullscreen-controls.png`、`accepted-product-fullscreen-menu.png` 和
`accepted-product-fullscreen-queue.png`；使用生成的彩条媒体，未用个人视频做界面验收。

证据保留在 Git 忽略的 `native/out/product-interactions-20260907/`：构建/lint、instrumentation、APK 审计 JSON、
数据库快照、服务事件和应用截图。个人截图、数据库和临时日志不提交仓库。
Release unsigned 仅进行构建与静态审计，真机结论针对 Debug；未公开发布或推送远端。
