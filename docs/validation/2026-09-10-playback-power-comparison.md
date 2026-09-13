# ExoPlayer 与 libmpv 真机稳定播放功耗对照

后续状态：2026-09-12 的 `matched-overnight-v2` 正式批次取得 17 轮合格数据，随后因 03:20 的 Wi-Fi 断联中断。08:55 已完成重新连接、设置恢复、个人数据核对及测试文件归档清理，完整测试矩阵仍未完成，详见 [9 月 12 日复测记录](2026-09-12-playback-power-comparison.md)。本文保留 9 月 10–11 日的历史协议、中断经过与初步数据；下面的旧控制参数和暂停状态不代表新批次。旧 H.264 数据不混入新批次均值。

## 本轮中断记录

开始前确认全部外部供电断开、电池状态为放电、电量 87%、温度 28.9°C，设备端 APK / 测试 APK SHA-256 与准备记录完全一致。首对同尺寸 H.264 整机均值为 Exo 1.349 W、libmpv 1.439 W，约差 6.3%；每引擎仅一轮，起始温度相差 1.7°C，不能视为完成的重复试验结论。

2026-09-11 00:02:35 第三轮启动，instrumentation 立即报 `The staged power fixture is missing.`。随后只读检查确认 `cache/power-fixtures` 仍存在但为空，五份片源均已不在目录中。没有确定清理执行者；测试片源选用可回收缓存目录本身不适合本次长时间测量，后续需改为明确管理生命周期的应用私有持久目录。已结束的第二轮仍持有文件句柄并完成播放，不能从这一轮播放成功推断后续还能重新打开文件。

控制端发现中断时无线 ADB 连接也已断开，重新连接成功后停止应用并发送熄屏指令，确认 `mWakefulness=Dozing`、`mStayOn=false`，恢复原屏幕超时 *** ms。本机已保留两轮有效 JSON/JSONL、第三轮失败日志与进程退出记录。中断后再次核对：个人数据库全部表、结构和逐行内容与基线一致，个人设置 SHA-256 不变。没有继续启动新一轮测试。

用户随后解释 Codex 连接刚刚断开并已恢复，明确要求继续。再次核验时手机已重新解锁。修复把五份片源迁至 `files/power-fixtures`、结果迁至 `files/power-results`；每份通过已保存 manifest 的 SHA-256 校验，全部测试结束后按精确清单清理。应用源码未发现会清空测试片源目录的逻辑，当前无法归因清理执行者。手机端测试失败会请求熄屏，控制端矩阵异常退出也会停止应用并熄屏；正式无人值守运行须传入 `--sleep-after`，保证成功完成后同样熄屏。独立隐藏控制进程还以 Windows 临时执行状态请求防止测量电脑休眠，退出时释放请求。

持久目录版本的 H.264 双端功能短测 `persistent-smoke01` 通过。随后 `persistent-formal01` 在 00:25:32 启动首轮，等待 Activity 进入 `RESUMED` 超时，尚未创建播放控制器、预热或计量。退出记录确认停止应用、恢复 600000 ms 屏幕超时、发送 `KEYCODE_SLEEP` 均成功，最终为 `Dozing`。启动前将闲置超时缩至 120 秒，但使用 `KEYCODE_WAKEUP` 并不能可靠刷新已亮屏设备的闲置计时，导致播放器取得窗口常亮标志前锁屏。

控制脚本现已改为：解锁状态检查通过后，在播放器已停止的桌面注入 HOME，并核验 PowerManager 实际用户活动时间递增，再设置临时 120 秒超时；冷却时每 30 秒刷新实际按键活动，正式播放依靠窗口常亮标志，计量期间不注入按键。兼容本机 `Display Group User Activity / lastUserActivityTime` 字段，不将 attention 或 no-change-lights 时间误当作亮屏活动。清理失败或未确认熄屏会使整次运行明确失败。离线控制回归 7 项通过；手机目前锁定，超过两分钟冷却的实机常亮验证及重新开始的正式矩阵仍待进行，不能称修复已实机验收。

## 测量对象与边界

- 同一台 `23049RAD8C`、Android 15 / API 35，使用同一个 debug APK、真实 MainActivity、MediaSession 和 PlaybackService。每次测量前强制停止本应用，并确认旧进程已退出。
- 进程内测试开关在 Activity / Service 创建前选择 ExoPlayer 或 libmpv；不改用户持久化设置。引擎版本、实际视频解码器、视频规格均从运行中的后端读取。
- 测量整机电池侧电流和电压，包含显示屏、CPU/GPU、解码、系统与测试框架开销。这不是外置仪表测量，也不是解码器单独供电轨的读数。
- 结论只适用于本设备、此 APK、这些本地片源和设置；不覆盖 4K、HDR、网络播放、字幕渲染、拖动或所有现实片源。

## 输出尺寸对照

首组有效 H.264 四轮的实际 Surface 缓冲区为 Exo `1920×1080`、libmpv `2400×1350`。原路径整机均值分别为 1.286 W 和 1.618 W，Exo 低 20.5%；这包含显示路径和目标像素数差异，不能解释为单纯解码引擎的差异，也不是多编码最终结论。

后续 `matched-1080p` 协议只通过 debug instrumentation 进程内开关，把两端实际渲染目标统一为 `1920×1080`，可见画面布局保持相同。每轮正式采样前后及每秒检查实际 SurfaceHolder 缓冲区尺寸，并监听采样期间的 Surface 重建与尺寸变化；任一不符则排除。普通启动不设置此开关，沿用原显示逻辑。汇总按 `surfaceProtocol` 分组，原路径与同尺寸路径不会混入同一个均值。

充电功能验证已完成八种组合、每组合两引擎，共 16 项。全部连续播放、首尾实际尺寸均为 `1920×1080`。另有 13 份按播放阶段自动抓取的 SurfaceFlinger 证据，系统合成器报告的两端实际 `geomBufferSize` 均为 `1920×1080`。这些 dump 只在功能验证期间采集，不在正式功耗窗口执行。

新增全过程监控后，另跑 H.264 双端两项短测：逐秒尺寸记录一致，Surface 生命周期没有中途变化，均通过。准备结果与最终测试 APK / 源码 SHA-256 保存在 `matched-readiness.json`。此次构建校验确认 native 字节与 JNI 合约不变，50 项播放服务单元测试通过。

## 固定场景

用户固定静音、最低亮度、飞行模式；为彻底拔掉 USB，保留用户开启的 Wi-Fi 无线调试连接。蓝牙在测试期间关闭。每轮使用 `cmd display set-brightness 0` 交由显示系统钳制到真实最低背光（本机 `brightnessMin=0.000854597`，对应系统整数亮度 `1`）。前后从 DisplayManager dump 核验 `adjustedBrightness=brightnessMin`，并每秒检查整数亮度和手动模式。每次前后还检查飞行模式、Wi-Fi、蓝牙、媒体音量与静音状态、窗口没有覆盖系统亮度，以及窗口焦点与显示刷新率。

正式功耗测量要求物理拔掉所有充电来源。每个采样点都要求 `EXTRA_PLUGGED=0`、电池状态为 `DISCHARGING`；没有使用 `dumpsys battery unplug` 模拟断电。测试时应用保持前台横屏全屏，控制栏自动隐藏，不操作播放器。充电期间的验证必须同时显式启用 `--smoke --allow-external-power`，结果固定为 `validationOnly=true`、`measurementQualified=false`，汇总脚本再次拒绝此类数据。

## 片源和矩阵

由同一原创确定性 `ffmpeg testsrc2` 图像序列生成五份 1920×1080、24 fps、360 秒 MKV：H.264、HEVC 8-bit、HEVC 10-bit、VP9、AV1。所有文件复用完全相同的 AAC LC、48 kHz、双声道音轨，输出静音。目标视频码率 6 Mbit/s，实际码率因编码器而异；每项 Exo/mpv 对比使用完全相同的文件字节。

30 秒素材在封装阶段拼成连续的 360 秒时间线，播放阶段不循环、不跳转。每次播放前完整读取文件并核对 SHA-256，统一文件缓存状态。生成参数和 ffprobe 数据保存在 `native/out/power-fixtures/manifest.json`；设备部署清单为 `native/out/power-comparison-20260910/staged-fixtures.json`。

自动模式覆盖五种编码；另测试 H.264、HEVC 8-bit、VP9 的强制软件解码。HEVC 10-bit 强制软件模式另作兼容性检查：Exo 的 `c2.android.hevc.decoder` 报 `ERROR_CODE_DECODING_FAILED`，libmpv 正常输出，故不将此项作为两者正常播放的功耗对比。自动模式不是硬件解码的保证，因此单独记录实际路径。该设备的 MediaCodec 枚举显示 AVC / HEVC / VP9 硬件解码器，AV1 只有软件解码器。

## 采样与资格判定

每个正式样本先预热 90 秒，再采集 240 秒稳定播放，每秒写一条本地电池记录；无线连接只在每 30 秒传输少量状态文字，完整结果在测量后取回。每项使用 Exo → mpv → mpv → Exo 顺序，得到每引擎两个独立进程样本。每轮之间至少冷却 45 秒，并要求起始电池温度不超过 36°C。最初 35°C 门槛在任何正式数据产生前调整：自然冷却后读数在约 35.9°C 维持数分钟；保留原始冷却日志，并另检查实际配对温差。

实际画面由 Surface PixelCopy 核验，预热阶段要求图像变化；稳定阶段每 61 秒与结束时抽取低分辨率画面指纹。61 秒避免与片源 30 秒内容周期重合。PixelCopy 和图像哈希会增加少量 GPU / 内存读取工作，两端采用相同规则，其开销包含在整机测量中，尚未单独标定。Exo 额外读取真正的视频输出帧计数；MKV 的 Media3 Format 可能不提供帧率，此时结合 SHA-256 对应的 ffprobe 规格和输出帧数核验。

下列情况不能进入正常播放功耗均值：窗口或设置变化、未持续播放、时间推进不接近 1 倍速、解码器变化、缺少丢帧证据、超过 1% 丢帧、Exo 输出帧不足、采样间断、供电状态变化、传感器无效或系统报告热节流。异常和失败文件保留，不能悄悄删除或算作较低功耗。

已排除的有效性事件：`formal02-avc-automatic-1-exo` 播放正常，但实际系统亮度为 `9`、背光高于设备最低值，因此整轮不进入功耗比较。其后尚在准备中的 mpv 轮次由测试控制端停止。`smoke05-avc-automatic-2-libmpv` 的 ApplicationExitInfo 明确记录 `OneKeyClean` 外部强制停止，不能描述为 libmpv 解码崩溃。HEVC 10-bit 软件解码则重复两次得到 `format_supported=NO_EXCEEDS_CAPABILITIES`、`c2.android.hevc.decoder`、`CodecException: Error 0xe`，属于该软件路径在本片源上的兼容性失败。

## 电量计解释与计算

Android 文档约定 `CURRENT_NOW` 以微安为单位、放电为负；本机实测拔掉 USB 后持续为正，充电时可为负，所以显式采用本机放电符号 `+1`。没有对电流直接取绝对值。

本机 `CURRENT_AVERAGE` 与 `ENERGY_COUNTER` 返回不支持的哨兵值；原始 `CHARGE_COUNTER` 数量级也不符合直接解释为标准微安时，因此保存为 `chargeCounterRaw`，仅用于与积分变化交叉检查，绝不静默换算成标准单位。`dumpsys powerstats` 没有可用供电轨数据，非 root 应用也不能读取受保护的电池 sysfs。

对相邻采样点的功率 `P = I × V / 1,000,000`（微安 × 毫伏 → 毫瓦）做梯形积分，使用实际单调时钟间隔；能量为 mWh，电荷积分为 mAh。均值按时间加权。样本内 P05/P95 只描述电流波动，不是置信区间。引擎比较使用每轮均值、重复次数和轮间范围，避免把连续 1 秒样本假装成独立重复试验。

计量边界参考：[Android BatteryManager](https://developer.android.com/reference/android/os/BatteryManager)、[Android 整机功耗测量](https://source.android.com/docs/core/power/device)、[Power Stats HAL](https://source.android.com/docs/core/power/power-stats-hal)。厂商电量计没有通过外部电表标定，绝对功率仍有计量不确定性。

## 重现与原始证据

```powershell
python tools/create-power-fixtures.py
# 通过已配对的无线 ADB 安装测试 APK，并按 staged-fixtures.json 部署测试文件后：
python tools/run-power-benchmark.py --serial <wireless-ip:port> --prefix <unique-label> --smoke
python tools/run-power-benchmark.py --serial <wireless-ip:port> --prefix <unique-label> --surface-protocol matched-1080p --max-start-temperature 36 --sleep-after
python tools/summarize-power-benchmark.py --discharge-sign 1
python tools/test-power-summary.py
python tools/test-power-controller.py
```

`native/out/power-comparison-20260910/` 保存每轮 JSON、逐秒 JSONL、instrumentation 日志、安装前应用数据快照、APK 校验及汇总。`tools/test-power-summary.py` 用解析解验证积分单位、线性电流、符号校准、无效数据剔除、充电功能验证隔离、尺寸资格判定与中途尺寸变化剔除，9 项通过。

充电暂停时重新抓取应用数据：SQLite 完整性正常，所有表的结构、行数及逐行内容完全一致，个人设置文件 SHA-256 相同。文件差异仅为新增的九个已识别 mpv shader cache，以及 `files/profileInstalled` 更新；没有删除原缓存。它们保留至正式测试结束后的清理与最终核对，不能把当前快照描述为整个文件树逐字节不变。
