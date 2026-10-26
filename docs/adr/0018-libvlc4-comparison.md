# ADR 0018：libVLC 4 播放实验

- 日期：2026-09-13
- 状态：独立实验分支
- 分支：`codex/libvlc4-comparison`

## 目的与保留点

在现有播放器界面、媒体库和播放服务中增加 libVLC 4，供手动播放对比。此次工作结束 Exo/mpv 的功耗测量，不沿用其功耗结论来评价 VLC。

创建新分支前，完整现状已保存为 `***`，保留在 `codex/exoplayer-libass-comparison`。新分支从该提交建立。`main` 的 libmpv 基线仍为 `***`。实验分支继续保留，不合并、不删除。旧 APK 和测试 APK另存于本机 `native/out/libvlc4-experiment-20260913/previous-experiment-apks`。

## 引擎与会话

默认 `playbackEngine=vlc`；可显式选择 `exo` 或 `libmpv`。所有选择共用当前实验依赖图，只有所选后端创建播放器。原 Exo/mpv instrumentation 的进程内参数仍覆盖构建默认值，避免旧测试悄悄改测 VLC。

固定依赖为 Maven Central 的 `org.videolan.android:libvlc-all:4.0.0-eap29`，这是预览版本。AAR 内实际原生引擎属于 `4.0.0-dev`，旧 Android Manifest 的 `3.0.0` 标签不能用作引擎版本证据。依赖锁和校验元数据保留既有版本，新 AAR、Java JNI 描述符和目标 ABI 字节另存于 `tools/libvlc4-experiment-lock.json`。

`LibVlcPlayerBackend` 复用 `PlayerBackend`、现有 Surface lease、配置和拖动接口。队列、历史、音频焦点、Media3 会话、生命周期暂停和用户播放意图仍由原有核心/服务负责。每次 load 建立独立 MediaPlayer；旧事件按实例与 load generation 失效。

初次从零位置准备时以 `:start-paused` 打开。恢复非零位置或重建暂停 Surface 时，新播放器先保持音量为零并启动解码，在 Playing 事件后提交精确定位；收到实际原生时间点后请求暂停，等 Paused 确认才恢复音量并发布 READY。恢复期间的用户播放/暂停意图保留到准备结束。SAF 媒体由后端持有 PFD；服务提供的字幕 FD 先复制，再将有界内容写入本次播放专属缓存文件，直到停止完成后释放。

## 拖动与原生完成通知

eap29 Java API 缺少定位完成事件，而且 `setTime` 的 JNI 包装未正确传回原生返回值。独立 `libzivplayer_vlc4.so` 接出固定版本公开 C API 的 `watch_time` / `set_time`，不重编译或替换官方 VLC 解码库。

JNI 桥接通过公共 `MediaPlayer.getInstance()` 获取原生播放器指针，保留一个引用，并注册唯一的时间观察器。视频和正在播放的定位请求须先经过非空 `on_seek`、空指针结束通知，再等待后续有效时间点；PCR 重置可能提前触发空指针通知，不能立即读取旧的 `getTime()` 作为完成位置。暂停音频没有新输出时间点，使用原生定位结束通知确认逻辑游标。时间点缓存通过公开的 `time_point_interpolate` 读取，并在暂停时冻结；时间观察器也可能报告 demux/input 时间，不能单独作为物理屏幕首帧证据。

全新暂停播放器不保证产生首个时间点或创建视频输出。真机恢复中曾出现定位结束通知已到达、但没有后续时间点的停滞；等待首个时间点、等待 Vout 或提前调用 `next_frame` 都未解决。最终初始化采用静音启动解码、定位后等待暂停确认的流程；不保留实验性的额外帧步进入口。普通已就绪播放器的暂停定位仍等待真实原生时间点，准备/定位超时继续生效。观察器维持 100 ms 节流。

请求令牌在原生线程关联，Java 回调只投递 Handler，禁止在 VLC 回调锁内调用播放器 API。核心保留单个在途请求并合并较旧的预览；KEYFRAME 使用 fast seek，松手提交使用精确 seek。未完成的请求通过超时进入 RESET 错误。

暂停且没有视频窗口时，用户定位仍立即提交原生逻辑定位并更新后续窗口恢复位置；不能将请求无限排队到 Surface 返回。后台播放意图复用已经提交的定位，避免重复占用原生观察器。窗口重建仅在持有生命周期锁并确认当前 Surface 有效后设置恢复标记，避免排队期间窗口销毁留下无法结束的恢复状态。

连续暂停重建/PiP 后，无窗口定位再恢复播放还复现了固定内核的时钟跳变：已确认 2.416 秒的逻辑定位，播放 144 ms 后却暂停在 9.525 秒，watch_time 也会返回提前的时间点。公共 time_point 不携带 input/output 来源；system_date 的特殊值只表达暂停，不能用于识别已输出帧。后端仅在这类无窗口定位后的下一次播放重建播放器，按保留的逻辑位置建立新时钟并静音准备；普通后台播放沿用现有路径。设备验收同时检查播放推进与实际经过时间的范围，不能只看 READY、isPlaying 或返回前后位置一致。

释放顺序为失效旧实例、移除观察器、解绑 Surface、后台线程执行阻塞的 Java stop、主线程 release，最后关闭媒体 FD 和删除本次专属字幕缓存。Java 的 `EndReached=0x109` 在当前 v4 内核实际表示通用 Stopping；后端等待错误事件之后的 Stopped，显式 stop 已提前失效，防止误触发自动下一首。

## 显示、配置与测量边界

使用现有 VideoViewport，拖动时不启动第二个缩略图解码器。视频 Surface 与透明字幕 Surface 均准备完成后绑定，通过布局回调启用 `android-display`。普通本地视频经独立文件描述符预解析确认没有旋转或镜像后，允许直接 MediaCodec 输出；带变换及无法确定变换的源使用官方 `:no-mediacodec-dr` 图像复制路径，保留硬件解码。固定 eap29 的 Java 解析状态常量仍沿用旧值，成功必须按对应 VLC 4 原生状态 `6` 判断。

SurfaceView 按实际窗口大小布局，原生输出填充界面选定的 FIT/FILL/STRETCH 矩形；带旋转的复制输出在设置 DAR 时交换轴。直接输出位于原生 SurfaceControl 子层，父 Surface 的 PixelCopy 可能没有视频队列，验收须结合系统读回与原生 crop/变换。暂停页面重建时重新建立播放器，串行绑定最新有效窗口，保留位置及最新轨道选择，避免复用仍关联旧窗口的 vout。恢复期间的选轨更新快照，正常停止/换片才清除旧轨道元数据及专属字幕缓存。外挂字幕等待实际新增轨道并显式选择，避免此前禁用文本轨道使 `addSlave(select=true)` 未生效。实现与真机范围见[清晰度修复记录](../validation/2026-09-13-video-clarity-fix.md)。

已接入自动/软件解码选择、音量/静音、倍速、音轨/字幕选择、外挂字幕、音频和字幕延迟。解码模式在下次打开媒体时应用。mpv 着色器、缩放滤镜、色调映射、导入字体和字幕覆盖样式未迁移；界面显示实验配置说明。Java 没有提供实际解码器名称，因此诊断 decoder 保持空值，不能将请求的软件模式当作运行证据。

HTTP 源当前只映射 User-Agent 和 Referer；带其它自定义头的媒体明确失败。普通无自定义头的网络 URI 交由 VLC 打开。外挂文本字幕按内容识别 ASS/SSA 或 SRT，单文件上限 32 MiB。

应用 ID、签名和数据 schema 沿用旧版本；安装测试包时保留应用数据，无需卸载。构建、主机测试和 APK 字节检查仅覆盖代码/产物边界，实际暂停预览、精确定位、字幕显示、旋转、后台恢复、解码表现和功耗仍需设备验收。

固定 eap29 已复现内置 MKV 文本字幕顺播正常、跳转到活动字幕中间后未重现的限制，以及部分暂停 MKV 精确定位返回位置偏离请求值。时间桥接保留实际原生结果；当前修复不宣称解决上述原生限制，也不把选轨状态或 READY 单独视为画面通过。

## 来源

- [固定 Android AAR](https://repo.maven.apache.org/maven2/org/videolan/android/libvlc-all/4.0.0-eap29/)
- [libvlcjni 固定源码](https://code.videolan.org/videolan/libvlcjni/-/tree/a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21)
- [对应 VLC 公开播放器 API](https://code.videolan.org/videolan/vlc/-/blob/c84ef1eda4f14f8b57a5b266b51c843cf51ed28b/include/vlc/libvlc_media_player.h)
