# 个人设备功能真机回归

日期：2026-09-07（UTC+8）。延续 [9 月 6 日功能与主机验证](2026-09-06-personal-device-expansion.md)，
按用户授权连接 USB 调试手机、覆盖安装并执行真实 SAF/Media3/libmpv 测试。
用户明确要求跳过音频焦点，后续命令均排除独立焦点测试。没有修改 OEM 电池、焦点或全局常亮设置。
没有推送提交或公开发布。

## 设备与数据保留

- Redmi Note 12 Turbo / 23049RAD8C，Android 15 / API 35，arm64-v8a，实际页大小 4096。
- 安装前保留应用私有 `databases` 与 `files` 快照；对初版执行 `adb install -r`。
- Android Room 实际打开数据库，从 v1 迁移至 v2；SQLite 完整性检查为 `ok`。
  迁移前后原有两条最近记录（包括续播字段）逐行一致，摘要为
  `d5db24aa1ec7a7c0800f15c521e893667a90baabd0c78aa1fb09816884f1849d`。
  全部测试结束后再次逐行比较，原有两条记录及全部字段仍相同；最终共 13 条，新增 11 条均为生成夹具。
- 仅在新建的 `Movies/ZivPlayer-Expansion-20260907` 中放入生成夹具，经应用文件夹选择器授予 SAF 权限。
  扫描首次发现 9 个媒体，随后新增两个多轨副本。测试会更新这些夹具的最近记录、进度及轨道偏好。
  文件夹收藏/隐藏标记和全局播放偏好在正常结束时恢复；原媒体文件没有被删除或改写。
- 资源测试曾因等待一个已被规范化的偏好值而挂起，主动结束了测试进程；随后在应用停止时恢复
  测试前的 DataStore 文件，并核对两端 SHA-256 为
  `bc03c5e07f796552e167d6ac78c32c59d84d57edc422801c7ad2a2c29470e456`。
  该次 instrumentation 的 `Process crashed` 是主动停止产生的记录，不计作自然崩溃或通过。
  最终偏好文件仍为同一摘要；系统 `stay_on_while_plugged_in` 前后均为 0。

## 发现并修复的问题

### 轨道发现之前过早恢复字幕关闭状态

真实新进程中，libmpv 的 `FILE_LOADED` 先产生 READY，异步轨道列表稍后到达。
原 `scheduleTrackRestore()` 在 READY + 空轨道列表时已经发出关闭字幕命令，核心会拒绝：
`SELECT_TRACK is not available in the current playback state.`
异常路径清掉待恢复选择，结果是默认音轨和默认内嵌字幕继续播放。

现在调度前和真正选轨前都检查 `SELECT_TRACK` 能力；轨道尚未可用时保留待恢复状态，
后续轨道快照再触发恢复。保留原有两秒尽力恢复期限和用户新意图优先规则。
真实测试先写入第二音轨、字幕关闭/外置字幕选择，再强制结束应用进程，另一次 instrumentation
确认新进程重开后的实际选轨和播放，而非仅检查数据库。

### 资源清理混用了 Android 路径别名

资源解析返回 canonical 路径，清理时却用目录项的 absolute 路径比较。
本机 Java 的实际记录为同一字体的 `/data/user/0/<package>/files/...` 与
`/data/data/<package>/files/...`。导入下一项资源或重启时，已有字体和字幕被当作孤立文件删除，
外置字幕关联也被清掉，轨道选择记录则仍残留。

现在清理两侧统一比较 canonical 路径。修复后，跨类型导入保留已有资源，强制结束进程后的
外置字幕恢复通过；字体和 shader 的实时效果也以实际视频帧确认。

### 自动 PiP 参数刷新遗漏视频能力变化

静态核验发现，自动 PiP 的启用条件读取 `canRenderVideo`，对应 Compose effect 的 key 却未包含它。
控制器断连、重连时该值可能单独变化，因而留下旧参数。现在将该字段加入刷新依赖。
这是独立的状态完整性修正，没有把它归因为本轮短片测试中的所有自动 PiP 失败。

早期 PiP 测试未等待 Activity 恢复到 RESUMED，且在系统缩放动画结束前取屏。
补齐生命周期、UI 前提与动画等待后，手动/自动 PiP 的原生帧和整屏截图均正常。
本轮未进一步注入控制器断连来单独验证该静态分支。

## 真机检查记录

| 检查 | 观测与边界 |
| --- | --- |
| 原有基线 | 排除音频焦点后的 7 项通过；包含实际解码、双音轨、内嵌 ASS/SRT、外置 ASS/SRT、字幕关闭、定位、倍速、重播和 Activity/Surface 重建 |
| SAF 与媒体库 | 实际系统选择器授权；扫描、重扫、收藏/隐藏保留、扫描进度回调中的取消和中断状态、历史不被扫描改动通过 |
| 9 类格式 | 每项播放推进、暂停、定位至约 1 秒、停止，以及同 UID 诊断读取通过；视频分辨率、编解码器和 PQ/HLG 转移函数均检查实际值 |
| 跨进程轨道记忆 | 修复后第二音轨、字幕关闭、外置 SRT 重开恢复通过；prepare 清旧夹具选择，成功后写进程标记，verify 检查新进程并消费标记 |
| 字体与 shader | 默认 1.25 倍速随新媒体生效；Noto Serif 英文字幕字形可见；原创反色 shader 开启后原生视频帧明显变化，关闭后固定帧恢复，设置状态无错误 |
| 全屏 | 横屏、重建、退出、播放状态与原生彩色帧通过；截图要等 Surface/系统过渡完成 |
| 手动 PiP | 进入、保持播放、返回，以及动画结束后的合成截图和彩色原生帧通过；调用小窗动作已注册的 PendingIntent，暂停/继续通过（未模拟系统按钮触点） |
| 自动 PiP | 等前台 RESUMED、UI 条件和配置完成后按 Home，进入并保持播放；动画结束后的彩色原生帧、合成截图通过 |
| 后台与纯音频 | 禁止后台时 Home 暂停，允许后台时继续；纯音频禁用全屏/PiP 并遵循后台策略，最终组合回归通过 |
| 短片切换压力 | 330.096 秒测试主体、JUnit 总计 334.323 秒，循环加载 9 类夹具，无播放错误；不替代真实长片 |

最终 Debug APK 上的组合回归 **11 项通过**（77.181 秒），跨进程 prepare / verify
各 **1 项通过**（5.076 / 7.189 秒），压力测试 **1 项通过**，共 **14 项**。
音频焦点没有计入通过数量。此前用于定位的失败和重复运行不重复累计。

格式诊断的 `video-codec` / `audio-codec` 为 mpv 人类可读描述，例如 `H.264 / AVC / ...`，
不是 FFmpeg 短名；测试已修正该断言。4K 与 PQ 样本观测到 `mediacodec`，
实际报告尺寸分别为 3840×2160 和 1920×1080；未知诊断字段保留 `null`。
这些短样本结果不证明稳定硬解性能、HDR 屏幕输出、色彩准确度或所有音轨都已人工听验。

扫描取消测试在目录进度回调中抛出 `CancellationException`，覆盖收尾与保留路径，
不等于已经测试提供者永久阻塞、任意查询中断或未访问子目录的所有分支。
字体字形由截图目视核验；shader 另有 PixelCopy 的帧差与恢复断言。

## 短时运行采样

压力测试期间执行了真实 `collect-device-playback-evidence.ps1`，300.675 秒内取得 56 次系统采样：

| 观测 | 结果 |
| --- | --- |
| 进程 | 全部采样为同一 PID，无中途替换 |
| 文件描述符 | 229–318，首项 310、末项 229 |
| TOTAL PSS | 233,227–331,689 KiB，首项 290,147、末项 245,571 KiB；媒体类型随时间改变 |
| 播放器诊断 | 305 次；221 次有视频丢帧/解码丢帧值，报告最大值均为 0；84 次不可用，保留 null |
| 解码器 | 183 次 `mediacodec`，38 次 `no`（软件路径），84 次不可用；不是全部格式都在硬解 |
| 电池温度读数 | USB 连接期间 35.3→35.4 °C，电量 99%；不是经过校准的单应用功耗测试 |

观察窗口内句柄末尾回落、PSS 随类型变化，没有据此宣称全面无泄漏、恒定内存或真实长片性能通过。
采样目录为 `native/out/device-runtime-20260907T050046053Z/`。
`gfxinfo` 记录的是 Android UI 帧，不能作为原生视频 FPS。

## 主机检查与安装包

完整离线 `test lintDebug lintRelease assembleDebug assembleDebugAndroidTest assembleRelease :build-logic:test`
成功，734 个 Gradle 任务；26 份 JVM XML 共 186 项测试，失败/错误/跳过均为 0。
普通 Debug/Release lint 执行成功：每种变体 6 个 Warning、1 个 Hint、0 个 Error。
保留的提示涉及 PiP `sourceRectHint`、Modifier 默认值、KTX、百分比字符串和整数 State；不称为 lint clean。
日志中的 `lintVitalRelease` 为 SKIPPED，不能与已执行的完整 `lintRelease` 混为一谈。

两个主应用 APK 的静态校验均通过：20 个凭据原生库 + 4 个锁定 AndroidX helper，
ELF/ZIP 16 KiB 对齐与 16 个 DEX JNI 方法形状符合检查。
静态产物 JSON 的 `runtimeRegistrationEvidence` 仍为 `pending-device-art-smoke`；
实际 Debug 运行证据保存在独立 instrumentation 日志中，Release 没有真机运行结论。

| 产物 | SHA-256 |
| --- | --- |
| Debug，已覆盖安装且从手机重新读取摘要一致 | `6b9b8f21e8c1c317892729c37f45c6217fa8bbe99c0775cda68b13a54912dae2` |
| Release unsigned，仅构建与静态校验 | `476101f09d7630ec951efa8852d4b5cfc9a9d7b99d121d05a4813b68447f0b97` |
| Debug instrumentation APK | `17b7b8a4d17080b4299502479017b18e41bafda8925a4f009c2f452821546e42` |

本轮没有重建原生依赖、改动 JNI wrapper 或公开发布。

## 可复现入口与证据

新增 `PersonalDevicePlaybackTest` 默认跳过；必须显式提供 `personalFixtureFolder`。
请只授权独立生成的测试目录，不将已有个人媒体目录交给这些会修改夹具历史的测试。
夹具来自 `tools/create-format-matrix.ps1` 与 `tools/create-device-fixtures.ps1`。
将 `tracks-subtitles.mkv` 复制为 `tracks-off.mkv`、`tracks-external.mkv`，另放入
`external.srt`、本机可读的 `NotoSerif-Regular.ttf`，以及以下原创 `test-invert.glsl`：

```glsl
//!HOOK MAIN
//!BIND HOOKED
//!DESC ZivPlayer original validation invert
vec4 hook() {
    vec4 color = HOOKED_tex(HOOKED_pos);
    return vec4(vec3(1.0) - color.rgb, color.a);
}
```

单项测试命令示例（`SERIAL` 替换为实际设备号）：

```powershell
adb -s SERIAL shell am instrument -w -e personalFixtureFolder ZivPlayer-Expansion-20260907 -e class io.github.joyelliot.zivplayer.PersonalDevicePlaybackTest#preparePersistentChoices io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
adb -s SERIAL shell am force-stop io.github.joyelliot.zivplayer
adb -s SERIAL shell am instrument -w -e personalFixtureFolder ZivPlayer-Expansion-20260907 -e class io.github.joyelliot.zivplayer.PersonalDevicePlaybackTest#verifyPersistentChoicesAfterProcessRestart io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
```

不要在一条默认 instrumentation 命令中依赖 JUnit 方法排序来验证进程重启，也不要运行整个
`NativePlaybackSmokeTest` 类来绕过本轮明确跳过的焦点测试。
压力测试还需要单独提供 `stressSeconds`（30–600 秒）；短文件循环只用于重复加载和切换压力。

私有数据库快照、失败/修复后 instrumentation 日志、诊断 JSON、PixelCopy 帧、整屏截图及构建日志
保留在被 Git 忽略的 `native/out/device-expansion-20260907/`。整屏图含手机桌面，未提交至仓库。

## 本轮未验收

音频焦点按用户要求跳过。跨设备/API/ABI、三键导航与手势的完整组合、授权撤销和复杂目录故障、
真实长片 30–60 分钟、复杂字幕持续负载、HDR 显示链、经过校准的功耗，以及全面泄漏分析仍需专项验证。
