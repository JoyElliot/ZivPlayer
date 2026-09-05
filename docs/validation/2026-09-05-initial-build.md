# 初版构建与真机验收记录

日期：2026-09-05，最后的服务清退复核于 2026-09-06 00:05（UTC+8）完成。初版本地构建已完成，并在 Redmi Note 12 Turbo
（23049RAD8C，Android 15 / API 35，arm64-v8a）完成下述验收。
这是可安装的个人开发 Debug 版本；完整产品与正式发布仍有后续工作。

## 已交付功能

- Android 原生 Kotlin、Compose + MIUIX；libmpv 为唯一播放内核，Media3 负责系统会话。
- 双 ABI 源码构建的 20 个原生库、凭据绑定暂存、Gradle 离线依赖闭包；bootstrap AAR 已退役。
- SAF 本地文件打开、播放/暂停/停止/重播、定位、速度、音量、单曲循环。
- 音轨选择、内嵌 ASS/SRT、关闭字幕、外置 ASS/SRT 导入。
- Room 最近记录、持久 URI 授权检查、进程结束后的历史续播。
- 后台媒体通知、系统播放控制、瞬时音频焦点处理、耳机断开自动暂停。

## 本次修复

1. Media3 1.11 默认连接回调没有开放播放器命令。服务现显式设置可信控制器的静态命令上限，
   再由运行时动态可用命令限制当前操作；应用内自定义命令仍限制为同 UID。
2. 文档提供者交付的描述符经 `/proc/self/fd/N` 重开时在真机被拒绝。
   媒体与字幕改用 mpv 借用的 `fd://N`，由原有 Kotlin PFD 生命周期管理关闭；
   `fd://` 和 `fdclose://` 均禁止作为持久媒体标识。
3. 旧 Surface 销毁时，只有成功释放当前令牌的视图才通知服务清除视频输出。
4. 测试夹具提供者改用独立测试 APK 中的纯 Java 实现，避免目标 APK 的 Kotlin 依赖去重
   导致另一个进程缺少标准库。Compose 测试在控制器连接后等待 UI 空闲，再检查输出。

## 验证结果

| 范围 | 结果与证据 |
| --- | --- |
| 主机完整检查 | 离线 Gradle 573 个任务成功，包含 JVM、Debug/Release lint 与三个 APK 的构建；`native/out/initial-build-quality-gate.log` |
| JVM | 20 个 XML 共 168 项，失败、错误、跳过均为 0 |
| Native 工具 | Windows `Ran 310 tests`，通过且跳过 30 项；WSL 本轮暂存工具 7/7。没有声称本轮在 WSL 重跑完整 310 项 |
| APK 检查 | Debug 和未签名 Release 的 20 个凭据库及两个锁定的 AndroidX 辅助库字节匹配，22 个 ZIP 条目均为存储模式并按 16 KiB 对齐；DEX 含准确的 16 个 JNI 方法；Debug 通过 zipalign 与 v2 签名检查 |
| 真机自动化 | 全部 7 项通过，19.643 秒；移除临时 `mpv.conf` 后，同一原生播放测试再通过 1 项，10.643 秒。见 `native/out/device-all-instrumentation.log`、`device-native-default-config.log` |
| 原生播放链 | 实际服务/JNI/解码/视频输出；2 音轨与 2 内嵌字幕；暂停、定位 4 秒、1.25 倍速、音轨切换、字幕关闭、导入两个外置字幕；停止后重播保留轨道选择；Activity/Surface 重建后继续播放 |
| 字幕画面 | 人工检查测试截图，确认彩条视频、中文/英文 ASS 和 SRT、双行与描边；`native/out/default-native-*.png`。保存截图的测试本身不做像素断言 |
| 可听音频 | 用户确认手机和蓝牙耳机均能听到测试音 |
| 焦点 | 独立测试 APK 的 Activity 请求真实瞬时焦点，播放器收到焦点丢失并暂停，归还后恢复；测试断言包含焦点丢失原因 |
| SAF / 历史 | 原生 DocumentsUI 选中 `Movies/ZivPlayer-Test-20260905/tracks-subtitles.mkv`；Room 保存持久 content URI 和 1,208 ms 未完成进度；强制结束本应用后重新启动，最近记录仍在，点击后恢复到该位置附近并继续。见 `native/out/device-history-db/before-reopen.json`、`device-history-after-process-death.png`、`device-history-resumed.png` |
| 后台与通知 | Home 返回桌面后循环播放；系统服务显示 mediaPlayback 前台服务和通知 ID 1001；通知卡片显示文件名/进度，点击暂停及继续后会话分别为 PAUSED / PLAYING。见 `native/out/device-background-service.txt`、`device-notification-*-session.txt`、`device-notification-paused.png` |
| 停止后的服务清退 | Media3 1.11 默认保留前台状态最多 600 秒；停止约 662 秒后，系统服务记录不再包含前台状态及前台通知字段。Activity 仍绑定服务。见 `native/out/device-stopped-service-after-timeout.txt`；此短暂保留不是持续播放 |
| 耳机断开 | 用户断开 REDMI Buds 6 Pro；系统 A2DP 设备消失，播放器自动 PAUSED 于 6,604 ms，用户确认声音停止。见 `native/out/device-headset-before-session.txt`、`device-headset-after-session.txt`、`device-headset-after-route.txt` |

真机使用的 Debug APK 和测试 APK 的 SHA-256 已从已安装包读取，与最终本机产物一致。
本地校验值、源码提交和逐项机器记录保存在 `native/out/initial-build-SHA256SUMS.txt`、
`native/out/initial-build-host-verification.json` 和 `native/out/initial-build-device-verification.json`。
这些生成结果、测试夹具和 APK 均被 Git 忽略，源码及本记录单独提交。

## 设备条件与恢复

- MIUI/HyperOS 阻止 instrumentation 的后台 Activity 启动，测试规则用 ADB shell 将应用置于前台。
- 原先开启的“允许多应用同时出声”会阻止音频焦点丢失回调。用户暂时关闭后，焦点测试通过，
  随后由用户恢复。此结果不保证能覆盖 OEM 主动忽略焦点的策略。
- 用户授权的临时充电常亮已恢复为测试前值 `stay_on_while_plugged_in=0`。
- 临时 mpv 诊断配置和焦点追踪已移除；正常字体配置仍保留。
- 应用及测试 APK 留在手机，原创测试文件留在 `Movies/ZivPlayer-Test-20260905`，便于继续验收。

## 后续工作与验收边界

- 扩展 API 26 至目标版本、x86_64、不同 GPU/芯片与 16 KiB 系统的运行矩阵；当前真机仅覆盖 Android 15 arm64。
- 补全媒体库扫描、完整设置、PiP、字体管理、shader、诊断页面及原计划中的高级功能。
- 补高码率/高分辨率、多格式、HDR/色彩、长时间播放、性能/功耗、更多文档提供者、权限撤销和完整资源泄漏矩阵。
- 当前外置字幕的导入与重播已经验证；跨进程自动恢复外置字幕和轨道偏好不是本次验收结论。
- 正式签名、最终 Release/R8 运行验收、CI 发布、accepted release builder、SBOM、对应源码及许可/留存材料仍待完成。
  原生执行凭据仍为 `ready=false`、`releaseInput=false`，不修改历史凭据，也不视作公开发行许可。
- 本阶段只做本地提交，没有推送或公开发布。
