# 2026-09-10：拖动延迟、封面、媒体信息与触觉反馈

## 本次范围

设备为 23049RAD8C、Android 15。本次继续优化 `codex/exoplayer-libass-comparison` 工作区，保留 [FLAC seek 修复](2026-09-10-flac-seek-recovery.md)，补充本地封面、媒体元数据及临时倍速触觉反馈。正式 `main` 基线仍为 `***`；未新增提交、推送或改写正式分支。

## 拖动输入积压与修复

此前测试确认了画面在拖动期间更新、松手后恢复，但像素更新次数不能证明画面跟得上手指。本次新增 `ExoScrubLatencyDeviceTest`：通过实际滑块 MotionEvent 输入、独立 PixelCopy 采样和原创视频里的帧编号条码，测量当前目标位置与实际 Surface 图像的差距。另以同一 ExoPlayer 实例和同一视频 Surface 直接调用 `seekTo`，作为跳过应用命令适配层的参考。

发现应用适配层逐次发送高频预览命令，旧目标积压；核心状态机又等待旧预览结束后才提交新目标。修改分为三处：

- `PlaybackControllerViewModel` 对预览命令只保留一个正在提交的请求及最新目标，旧目标不再进入无界提交队列。
- `DefaultPlayerSession` 通过后端能力声明，允许 ExoPlayer 的 KEYFRAME 预览被更新的 KEYFRAME 预览替换；EXACT 提交、已排队的 EXACT 和 libmpv 原有顺序语义保持独立。请求代次、旧回调及超时仍受检查。
- `ExoPlayerBackend` 核对当前 seek 的位置和序号后才接受视频帧完成信号，避免较早的视频帧完成后来的定位。

测试视频为 90 秒、24 fps、1280×720 的 H.264 原创素材，关键帧间隔 4 秒；每个方向用 2 秒扫过 20 秒内容，即 10 倍时间轴速度。输入跟随 Choreographer，采样独立执行。下表的跟随差距为 `abs(目标媒体时间 - 条码帧时间) / 10`，统计每方向开始 250 ms 之后、结束 2 秒之前的样本；P95 使用有序样本下标 `floor((n-1)*0.95)`。它是沿本次匀速轨迹折算的时间差，不是光学测量的触控到屏幕扫描延迟。

| 路径 | 正向中位数 / P95 | 反向中位数 / P95 | 正向 / 反向不同帧数每秒 |
| --- | --- | --- | --- |
| 修改前应用滑块 | 661.8 / 984.4 ms | 881.2 / 1560.4 ms | 11.5 / 12.5 |
| 修改前直接 ExoPlayer 参考 | 97.1 / 147.9 ms | 95.7 / 149.9 ms | 16 / 17 |
| 最终应用滑块 | 154.4 / 273.2 ms | 142.3 / 202.1 ms | 14 / 16 |
| 最终直接 ExoPlayer 参考 | 94.8 / 140.7 ms | 95.9 / 146.9 ms | 17.5 / 17.5 |

不同帧数按完整 2 秒扫描中不同条码数量除以 2 统计，包含最初画面；不代表持续播放 fps。最终包六次停稳目标的出帧延迟中位数为 89.7 ms，松手完成约 59.1 ms。每条路径均为单次同设备采样，受调度和温度影响，不是多轮统计置信区间，也不保证所有片源相同。该结果证明应用路径存在可修复的显著额外延迟，因此保留本次 ExoPlayer 实验继续验证。

### 当前个人片源的剩余限制

对当前 HEVC Main10、1920×1080 文件另外测量 500–520 秒区间。该文件没有帧编号条码，证据为 decoder release PTS 与 PixelCopy 图像变化，不能将 PTS 当作已经光学显示的帧身份。前后对照均只使用已授权 URI 和未注册的临时媒体 ID。

早期应用优化后，该区间的正 / 反向跟随差距中位数约 425 / 271 ms，直接 ExoPlayer 参考约 486 / 262 ms；参考路径也只有约 2 / 5.5 个不同 release PTS 每秒。最终应用包复测为约 493 / 370 ms、3 / 5 个不同 release PTS 每秒，六次停稳目标中位数约 242 ms。实测解码器为 `c2.qti.hevc.decoder`，500 秒前的同步帧为 486.611 秒，下一个为 501.626 秒，相隔约 15 秒。Media3 scrubbing 和异步 codec 路径已启用，运行中的 codec operating rate 由约 24 提高到 240；重排样本数返回未知值，不能解释为零。结合直接参考仍慢的结果，剩余成本主要与该区间的随机访问和解码有关。

没有通过跳过帧正确性检查、伪造 READY 或扩大超时隐藏问题。此次不能承诺这类长 GOP HEVC 文件达到标准 H.264 测试素材的跟随速度。

## 封面、媒体信息和触觉反馈

[ADR 0018](../adr/0018-local-artwork-and-media-details.md)记录完整边界。音乐读取内嵌图片；视频优先内嵌图片，否则提取同步帧缩略图。媒体库、最近播放、迷你播放器和音乐播放器显示可用封面；音乐页补充标题、艺术家和专辑。列表更多菜单及播放器更多菜单可打开“媒体信息”，显示实际读取到的标签、时长、容器 / 编码、分辨率、采样率和声道等信息，未知字段不伪装成零。

提取在独立有界后台任务中按需执行，图片最长边 768 像素，磁盘缓存目标上限 64 MiB / 1024 个文件，内存图片缓存 12 MiB。24 小时有效期以真实内容读取时间计，文件提示变化、图片缺失或截断会触发重建；AtomicFile 备份可恢复。不增改数据库结构或原文件，不因探测创建播放历史。Media3 显示字段更新保留请求 token、序号、来源和媒体身份。

最终回归首次发现无内嵌标题的文件会用文件名覆盖控制器原有显示标题，导致两个既有真机用例失败。已修为“真实标签标题 → 原显示标题 → 文件名”的回退顺序；没有放宽原测试等待条件，修复后完整 28 项重跑通过。早期失败日志保留。

长按成功进入临时倍速时发送一次 `GestureThresholdActivate`，松手或取消退出时在清理路径发送一次 `GestureEnd`。未成功进入倍速、锁定状态和普通点击不会发送这组反馈；取消时仍恢复倍速。使用平台的短促手势触觉，尊重系统触觉设置。真机 Compose 测试核对事件与恢复顺序；用户休息期间未要求人工确认，马达主观手感尚未验收。

## 最终验证

最终安装 APK SHA-256：

`97192834a73be7945a60456758e1a3599f564dad57368497322181b0fd15d837`

| 检查 | 结果与范围 |
| --- | --- |
| Debug / AndroidTest APK 构建 | 通过，JDK 21，Gradle offline，沿用项目受限 JVM 预算 |
| JVM 测试 | 核心运行时、媒体数据、播放服务、媒体库、播放器及应用共 139 项，0 失败 / 错误 / 跳过 |
| 选定 Android Lint | 六个模块 0 errors；保留现有 KTX、依赖升级、Modifier / plural 等提示 |
| APK native gate | 28 个锁定库、原 C++ runtime、16 KiB 对齐、16 个 mpv 与 19 个 libass JNI 方法通过；Debug 实验包，不是 release 发布验收 |
| 最终真机回归 | 28 项通过，约 124 秒；包含下述八个测试类 |
| 标准素材延迟 | 应用实际滑块和直接 ExoPlayer 参考各 1 项通过，最终数字见上表 |
| 当前个人片源 | 500–520 秒局部拖动、全片 10%–90% 大跨度各 1 项通过；大跨度每方向 2 秒，主画面约 18 / 22 次像素更新每秒，松手至续播 334 ms |

最终 28 项的测试类为 `MediaDetailsDeviceTest`、`MediaArtworkUiTest`、`MediaMetadataSessionDeviceTest`、`FullscreenGestureTest`、`LibraryHomeUiTest`、`NativePlaybackSmokeTest`、`ExoSeekCompletionDeviceTest` 和 `SeekPreviewDeviceTest`。覆盖真实文件标签与封面像素、损坏缓存和备份恢复、媒体信息弹窗、横竖屏音乐布局、会话元数据 / 队列身份、快速切歌、手势、媒体库、字幕 / 多轨 / Surface 重建、真实音频焦点、当前 FLAC 文件的定位与续播、条码视频帧定位。截图已逐张检查。

以上最终设备用例共 32 项。静态 native gate 的 `runtimeRegistrationEvidence` 固定标记为 `pending-device-art-smoke`；不能单凭该 JSON 宣称真机通过。实际运行结果由设备日志与 `final-verification-summary.json` 关联，手机 `base.apk` 的 SHA-256 与本地构建产物一致。Android instrumentation 的最终 `INSTRUMENTATION_CODE: -1` 不能单独区分成败，核验同时检查 `OK` 摘要、逐项成功状态与失败状态。

API 26–34 未进行本次真机覆盖，平台检索器支持范围与播放解码范围也不完全相同。无封面或无法提取时保留通用图标；没有联网匹配封面。

## 数据保留与设备收尾

本次开始和结束的独立快照核对：SQLite 完整性通过，排除已知测试 URI 后的 245 条既有记录及检查点、3 个目录、254 项索引、5 个资源及原有文件、个人选轨 / 外挂字幕关联与设置均保持不变。仅 AndroidX `files/profileInstalled` 安装标记变化；统计依据为本次 `before-data.tar`，不沿用上一次验证的记录数量。应用已停止，私人片源临时配置已删除。

工作期间通过 ADB 保持屏幕常亮，完成真机检查后已将 `stay_on_while_plugged_in` 设为 `***`、发送锁屏休眠事件，确认 `mStayOn=false`。设备进入系统 `Dozing / DOZE_SUSPEND` 状态；原 `screen_off_timeout=***` 保持不变。按用户要求关闭常亮，没有恢复开始时会继续保持充电亮屏的数值 `***`。

## 本地证据

忽略目录 `native/out/player-completion-20260910/` 保存原始日志、性能 JSON、个人数据比对、APK 和界面截图；个人媒体 URI、数据库与私人画面不写入仓库。主要文件：

- `final-build-checks.log`、`title-fallback-build.log`、`final-device-suite-fixed.log`、`final-apk-verification.json`。
- `before-latency-adapter.json`、`before-latency-native.json`、`final-latency-adapter.json`、`final-latency-native.json`、`performance-summary.json`。
- `codec-private-latency.json`、`final-private-latency.json`、`final-private-wide.json`；私人片源证据与标准条码素材分开解释。
- `data-preservation.json`、`final-verification-summary.json`、`phone-power-after.json`、`phone-power-after.txt`、`phone-display-after.txt`。
- `metadata-library.png`、`metadata-information.png`、`metadata-music-player.png`、`metadata-session-landscape.png`。

最终安装包和测试包为 `player-completion.apk`、`player-completion-test.apk`。本次原始安装包保存在 `before.apk`；正式 libmpv 回退包仍为 `native/out/exoplayer-libass-20260909/libmpv-baseline.apk`。
