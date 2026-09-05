# 个人设备功能补全与主机验证

日期：2026-09-06（UTC+8）。本轮从已完成真机验收的初版继续开发，重点是媒体库、设置和
高级播放功能，以及格式、长时间播放和性能验证的准备。用户次日自行进行手机测试。
本轮没有运行 ADB、安装 APK、启动设备应用、连接模拟器、推送提交或公开发布。
跨设备/API/ABI 扩展运行矩阵已按用户要求延后。

## 本轮实现

- 媒体库：SAF 添加文件夹、递归扫描、取消/重新扫描、搜索与排序、收藏、隐藏、移除索引。
  扫描不登记播放历史，移除索引不删除原文件；部分失败保留尚未访问的已有记录。
  授权由文件夹和最近记录共同引用，仍有引用时不回收。
- 数据：Room v1→v2 增量迁移，新增库索引和播放资源/偏好表，保留最近记录与续播位置。
  DataStore 保存全局设置；应用启动时清理中断导入及失效引用，再开放偏好读取。
- 设置：跟随系统/明暗外观、续播、后台播放、常亮、自动画中画、记忆轨道、默认速度和快进步长；
  解码、画质、画面适配、去隔行、色调映射、缓存、音画/字幕延迟、字幕样式。
- 资源：导入/删除字体和 shader，选择字体、调整 shader 顺序；外置字幕复制到私有存储，
  不依赖原提供者的长期访问。支持文本 SRT/ASS/SSA/VTT/SUB；字体为 TTF/OTF/TTC。
  字体、shader、字幕分别限制 20/2/8 MiB，总计最多 128 项/128 MiB。
- 播放：横向全屏、手动/自动 PiP、PiP 播放按钮；已有 Surface 租约逻辑保持一致。
  按媒体保存音轨、字幕、字幕关闭及外置字幕关联，重新打开后先恢复，再播放。
  轨道发现额外等待最多 2 秒；手动选择、暂停、停止、新媒体使旧恢复失效。
- 诊断：页面可见时每秒采样、服务最多一个并发读取、缺失指标显示未知；
  JSON 含采样/导出时间及观测值，不含媒体 URI、文件名或导入路径。
- 运行稳定性：配置 setter 失败反向回滚，回滚失败进入 RESET；256 项原生事件队列溢出
  尽力暂停并要求重建。历史通道 512 项、缓存/失败媒体数量有界，重试公平轮转且每轮总计最多 2 秒。

实现边界与故障语义见 [ADR 0012](../adr/0012-library-preferences-and-managed-playback-resources.md)。
资源限制、时间预算及有界队列不等于对永久阻塞的原生调用有强制终止能力。

## 主机验证

执行命令：

```powershell
.\gradlew.bat --offline test lintDebug lintRelease :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest :apps:android:assembleRelease :build-logic:test
python -B tools/verify-media-migration.py
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk --report native/out/overnight-debug-apk-artifact.json
python -B tools/verify-android-artifact.py apps/android/build/outputs/apk/release/android-release-unsigned.apk --report native/out/overnight-release-apk-artifact.json
.\tools\create-format-matrix.ps1 -VerifyOnly
```

| 检查 | 结果与边界 |
| --- | --- |
| Gradle | 完整离线检查成功，734 个任务；含单元测试、Debug/Release lint、三个 APK 和 build-logic 测试 |
| JVM | 26 个 XML、186 项，失败/错误/跳过均为 0；覆盖偏好编解码、字体元数据、轨道匹配、首次播放等待取消、JSON 转义、提供者取消、配置回滚与运行队列容量 |
| Room 迁移 | 主机 SQLite 执行源码中的 7 条迁移 SQL，与导出的全新 v2 六表结构一致；历史哨兵、唯一约束、外键级联与完整性检查通过。Android Room 实际打开升级仍待手机验证 |
| 依赖闭包 | 严格校验元数据与锁文件齐全；新增 DataStore 1.2.1 及必要传递依赖，没有升级既有 Kotlin/Compose/Media3 版本 |
| APK | 两个主应用包的 20 个原生凭据库加 4 个锁定 AndroidX helper 字节匹配，ELF/ZIP 16 KiB 检查通过，DEX 为准确的 16 个 JNI 方法；Debug 签名和 zipalign 校验见机器记录 |
| 格式夹具 | 9 项原创夹具共 48,699,495 字节，ffprobe 属性与逐文件完整软件解码通过；不是手机播放结论 |
| 采集工具 | `collect-device-playback-evidence.ps1` 仅完成 PowerShell 语法检查，本轮未运行 |

构建/锁文件日志、APK 检查 JSON、迁移结果、格式 manifest、校验值均在被 Git 忽略的
`native/out/overnight-*` 和 `native/out/format-matrix/`。没有重建或更改 23 项原生源码输入、
6 项 JNI wrapper 输入及历史凭据；没有把此前的手机测试计入本轮通过数量。
首次播放等待的单元测试覆盖取消与准备完成两个分支；MpvSessionPlayer 的真实超时、代次隔离、
FD/命令锁配合和速度优先关系本轮只有静态核验，仍需上述手机交互测试。

## 明日手机测试顺序

1. **覆盖安装与迁移**：使用 `apps/android/build/outputs/apk/debug/android-debug.apk` 覆盖初版，
   保留应用数据。确认此前最近记录和进度仍在，重新打开后可续播。
2. **文件夹**：添加本地媒体文件夹，扫描、搜索、排序、收藏/隐藏；扫描中取消、切换文件夹、
   移除正在扫描的根；再次扫描应保留收藏状态。仅移除索引后确认原文件仍在。
   在最近记录仍引用目录媒体时确认仍能重开；撤销系统授权后应显示可恢复错误。
3. **全屏与 PiP**：播放横屏/竖屏视频，进出全屏、旋转、返回普通页面；手动 PiP、自动 PiP、
   PiP 暂停/继续、返回全屏。纯音频应不提供视频 PiP。关闭后台播放后立即回桌面应暂停；
   自动 PiP 开启时分别验证 Home 手势和三键导航的系统行为。
4. **设置**：修改后重新打开应用检查持久化；逐项比较速度、步长、比例、延迟与字幕样式。
   首次播放前手动设速度，应使用手动值。导入字体和已知可用的 mpv shader，确认字幕/画面变化，
   调整顺序并删除正在选择的资源，确认回退可用。未知 shader 的 GPU 编译结果必须实测。
5. **轨道记忆**：用初版多轨测试文件切换音轨/字幕、关闭字幕、导入两个外置字幕；停止重开、
   切走再回来、结束进程后从历史重开，分别确认保存结果。准备过程中暂停或改选轨道，
   不应在稍后被旧恢复重新播放或覆盖。无法区分的同名外置轨道不自动猜选。
6. **诊断与格式**：打开诊断观察解码器、视频参数、丢帧与缓存，切页后停止采样；导出 JSON 并
   检查未知值为 null。将下表夹具自行复制到手机，逐项观察声音、图像、定位和结束重播。
7. **持续播放**：先用正常长片播放 30–60 分钟，穿插暂停/定位、后台/PiP、文件切换。
   记录温度、卡顿、声音同步和诊断值，再对比解码/画质设置。短夹具循环只适合切换压力，
   不替代真实长片、复杂字幕或持续高码率测试。

| 夹具 | 用途 |
| --- | --- |
| `avc-1080p50mbps.mp4` | 1080p30 AVC High 4.2 + AAC，目标 50 Mbit/s、实际视频约 48 Mbit/s |
| `hevc-main10-flac.mkv` | 1080p Main 10 + FLAC |
| `av1-opus.webm` | 1080p AV1 + Opus |
| `hevc-2160p30.mkv` | 4K30 HEVC Main 10 + AAC |
| `hdr-pq-main10.mkv` / `hdr-hlg-main10.mkv` | BT.2020 PQ / HLG 实际转移函数信号，不含 mastering metadata，不作为校色标准 |
| `audio-opus.opus` / `audio-flac.flac` / `audio-pcm.wav` | 纯音频与无视频界面行为 |

可选的主机采样命令（先由用户正常打开并播放；填入实际设备序列号）：

```powershell
.\tools\collect-device-playback-evidence.ps1 -Serial YOUR_DEVICE_SERIAL -DurationSeconds 600 -IntervalSeconds 5
```

该工具只读已运行应用的 PID、FD 数、内存，以及 UI 帧/电池/热状态，不安装、启动、停止或
操作播放。输出位于 `native/out/device-runtime-时间戳/`；读取失败保留缺失信息。
`gfxinfo` 是 Android UI 帧数据，视频 FPS/丢帧应看应用诊断；电池与热状态不是经过校准的应用功耗测量。
HDR 显示链、真实功耗及完整泄漏结论须根据手机观测判断，本轮没有给出通过结论。
