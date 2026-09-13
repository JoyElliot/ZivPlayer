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

媒体以 `:start-paused` 打开，暂停准备阶段静音。准备完成后才向核心发布 READY，由核心决定自动播放。存在恢复位置时通过原生精确定位完成事件结束准备。SAF 媒体由后端持有 PFD；服务提供的字幕 FD 先复制，再将有界内容写入本次播放专属缓存文件，直到停止完成后释放。

## 拖动与原生完成通知

eap29 Java API 缺少定位完成事件，而且 `setTime` 的 JNI 包装未正确传回原生返回值。独立 `libzivplayer_vlc4.so` 接出固定版本公开 C API 的 `watch_time` / `set_time`，不重编译或替换官方 VLC 解码库。

JNI 桥接通过公共 `MediaPlayer.getInstance()` 获取原生播放器指针，保留一个引用，并注册唯一的时间观察器。定位请求必须先观察到非空 `on_seek` 开始通知，再由空指针完成通知确认。请求令牌在原生线程关联，Java 回调只投递 Handler，禁止在 VLC 回调锁内调用播放器 API。核心保留单个在途请求并合并较旧的预览；KEYFRAME 使用 fast seek，松手提交使用精确 seek。没有原生完成通知时超时进入 RESET 错误，不以乐观时间读数冒充完成。

释放顺序为失效旧实例、移除观察器、解绑 Surface、后台线程执行阻塞的 Java stop、主线程 release，最后关闭媒体 FD 和删除本次专属字幕缓存。Java 的 `EndReached=0x109` 在当前 v4 内核实际表示通用 Stopping；后端等待错误事件之后的 Stopped，显式 stop 已提前失效，防止误触发自动下一首。

## 显示、配置与测量边界

使用现有 VideoViewport，拖动时不启动第二个缩略图解码器。通过无布局回调的 `IVLCVout.attachViews()` 选择 VLC 的 OpenGL ES 合成，字幕与视频写入同一 Surface。这里没有启用需要独立布局回调的 `android-display`；自动解码仍允许 MediaCodec，渲染输出与解码模式是两个维度。缓冲继续沿用原 libmpv 路径的稳定显示比例及屏幕长边尺寸，包含 SAR/旋转后的显示比例；FIT/FILL/STRETCH 由界面布局控制。屏幕比例匹配与旋转细节需设备验收。

已接入自动/软件解码选择、音量/静音、倍速、音轨/字幕选择、外挂字幕、音频和字幕延迟。解码模式在下次打开媒体时应用。mpv 着色器、缩放滤镜、色调映射、导入字体和字幕覆盖样式未迁移；界面显示实验配置说明。Java 没有提供实际解码器名称，因此诊断 decoder 保持空值，不能将请求的软件模式当作运行证据。

HTTP 源当前只映射 User-Agent 和 Referer；带其它自定义头的媒体明确失败。普通无自定义头的网络 URI 交由 VLC 打开。外挂文本字幕按内容识别 ASS/SSA 或 SRT，单文件上限 32 MiB。

应用 ID、签名和数据 schema 沿用旧版本；安装测试包时保留应用数据，无需卸载。构建、主机测试和 APK 字节检查仅覆盖代码/产物边界，实际暂停预览、精确定位、字幕显示、旋转、后台恢复、解码表现和功耗仍需设备验收。

## 来源

- [固定 Android AAR](https://repo.maven.apache.org/maven2/org/videolan/android/libvlc-all/4.0.0-eap29/)
- [libvlcjni 固定源码](https://code.videolan.org/videolan/libvlcjni/-/tree/a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21)
- [对应 VLC 公开播放器 API](https://code.videolan.org/videolan/vlc/-/blob/c84ef1eda4f14f8b57a5b266b51c843cf51ed28b/include/vlc/libvlc_media_player.h)
