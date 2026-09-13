# ADR 0017：ExoPlayer + libass 对比实验

- 日期：2026-09-09
- 状态：实验中，尚未决定替换正式播放后端
- 范围：Android；本分支对 ADR 0002 的单引擎约束作临时实验例外

## 背景

用户在较长动画中快速拖动进度条仍遇到冻结，并观察到画质下降。原实现为主播放器暂停、独立解码器产生约 480 像素的 SDR 位图预览。这条路径确实降低拖动时的清晰度；画面变化次数也不能证明预览跟上了目标时间。

实验前先提交全部已有修改：`***`（`main`），再创建 `codex/exoplayer-libass-comparison`。该提交和保存的基线 APK 是完整回退点。

## 决定

1. 使用 Media3 ExoPlayer 1.11.0 解码及输出原视频 Surface；ASS 使用 `io.github.peerless2012:ass-media:0.5.1` / `ass-kt:0.5.1` 的真实 libass 渲染。普通 SRT 使用 Media3 Cue/SubtitleView。
2. 核心 `PlayerBackend`、队列、播放意图、seek 串行化、持久身份和音频焦点仍由现有核心与播放服务管理。ExoPlayer 只拥有当前媒体的解码；每次核心 load 新建 ExoPlayer/AssHandler，隔离迟到回调。
3. 拖动继续沿用 BEGIN/PREVIEW/COMMIT/CANCEL/ABORT、token、媒体身份和序号校验，启用 ExoPlayer ScrubbingMode。普通拖动使用精确帧，连续请求推算的扫描速度超过约 100 倍时使用最近关键帧；松手无条件精确定位。UI 最短请求间隔为 16 毫秒，核心仍只保留一个正在执行的 seek 和最新待执行位置。拖动时保留播放意图并临时抑制实际播放；实验路径停用独立 480 像素位图预览。
4. 视频缓冲按选中轨道的源宽高设置，显示布局负责 FIT/FILL/STRETCH。ASS 图层使用实际显示矩形的尺寸，避免把源缓冲尺寸误当成字幕画布尺寸。
5. libass 时钟与字幕样本可能异步到达。暂停期间沿用现有 100 毫秒轮询请求字幕绘制，由 libass 的 unchanged 分支避免重复渲染；TextureView 就绪和尺寸变化时重放当前时间。轨道关闭立即触发清屏。
6. SurfaceHolder 销毁前同步解除 ExoPlayer 对旧 Surface 的引用。服务仍拥有 attach/replace lease；同步销毁围栏仅核对相同 Surface，避免 MediaController 异步 clear 晚于底层 Surface 销毁。标准 clear 仍负责清理控制器引用；服务在 clear 入队时捕获令牌，执行时若目标已替换则跳过。实验桥接持有弱引用，ExoPlayer 自身的同步 detach 有 2 秒超时保护。
7. 外挂字幕会重建 Media3 TrackGroup，因此显式音轨/字幕选择按稳定的后端 TrackId 重新绑定。显式字幕选择覆盖尚未执行的外挂字幕自动选择，保留 stop/replay 时的关闭状态。
8. 对设备的 `c2.android.flac.decoder`，codec reset 采用释放并重新初始化，处理部分 MKV 在 flush 后没有有效 PCM、导致 seek 持续 BUFFERING 的问题。保留原 AudioSink 与其他 codec 的默认行为，依据见 [FLAC 修复验证](../validation/2026-09-10-flac-seek-recovery.md)。

## 实验组织与依赖边界

暂时把 ExoPlayer 适配器放在 `platform/libmpv-android` 的独立 `exoplayer` 包中，以复用当前 Android Surface lease/configuration port；这不是最终模块命名方案。纯 Kotlin 核心不依赖 Android 或 ExoPlayer 类型。

本分支默认选择 ExoPlayer；`-PplaybackEngine=libmpv` 只切换播放后端，仍打包本分支的实验依赖。两种构建都必须使用 `tools/verify-android-artifact.py --exoplayer-experiment`。完整基线应使用已保存的原 APK 或 `main` 提交，不把这个构建参数当作原产物的复现保证。

新增 AAR 的 SHA-256 由 Gradle verification metadata 和 `tools/exoplayer-experiment-lock.json` 双重锁定。ass-kt 携带的重复 `libc++_shared.so` 通过 packaging pickFirst 解决冲突后，必须再次核对 APK 的 C++ 字节仍与原 staging receipt 完全相同。原 20 个库与 receipt 不作修改；实验 gate 只增加精确的 4 库/19 JNI 方法/ELF64/16 KiB/NEEDED 检查，严格默认 gate 仍拒绝实验 APK。

第三方扩展发布时以较早的 Media3 编译，当前对 1.11.0 已做 API 二进制检查和设备验证，但 Matroska ASS 入口仍依赖 Media3 私有字段和字幕前缀格式；升级任一依赖都必须重新验证。该实验不替代原生发布来源审查。

## 已知功能边界

- 原视频交给 MediaCodec/Surface 路径，不再生成低分辨率 SDR 视频位图；这不等于已验证所有 HDR、色彩和设备输出能力。
- mpv 着色器、缩放滤镜、色调映射配置、音频延迟以及部分字幕覆盖样式尚未映射；界面通过配置说明标记该实验限制。
- 软件解码模式只选择设备可用的 MediaCodec 软件解码器，不等同于 libmpv/FFmpeg 的软件格式覆盖率。
- 外挂字幕临时副本限制 32 MiB，目前识别 ASS/SRT；未验证其它字幕格式、DRM、直播、网络异常恢复与正式发布。
- seek 完成使用解码器帧回调和 READY 围栏；回调不是屏幕光学呈现证明。对比同时读取 Surface 像素，并用自有条码素材验证实际视频帧。

## 依据和验收

- [Media3 ScrubbingModeParameters 官方 API](https://developer.android.com/reference/androidx/media3/exoplayer/ScrubbingModeParameters)
- [libass-android 项目](https://github.com/peerless2012/libass-android)
- [本次设备对比及回退步骤](../validation/2026-09-09-exoplayer-libass-comparison.md)

决定是否保留 ExoPlayer，取决于相同设备、相同片源的快速拖动跟随、松手帧衔接、字幕样式、Surface 生命周期和用户实际观看结果。更新帧率、目标位置误差和画质应分别评估。
