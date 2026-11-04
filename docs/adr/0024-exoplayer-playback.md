# ADR 0024：ExoPlayer 播放内核与分离字幕输出

- 日期：2026-11-04
- 状态：已实现并通过主机与真机功能回归；物理音画/HDR/杜比显示及功耗未验收
- 替代：ADR 0002 的唯一 libmpv 内核决定，以及 ADR 0007/0011 中当前应用对 mpv、JNI 和 FD 的依赖
- 保留：core 权威队列、串行命令、加载/定位代际、历史身份、设置数据与播放交互

## 背景

用户基于此前功耗实验迹象，授权将 Android 主播放器迁移到 Media3 ExoPlayer，并尽量保留已有功能、连续动画和交互。当前代码没有可用于重新计算该实验结论的功耗样本；本迁移不把源码结构或主机构建结果当成功耗、HDR 或 Dolby Vision 的设备证据。

## 决定

- 新模块 `platform:exoplayer-android` 实现单项 `PlayerBackend`。ExoPlayer 使用与既有 Media3 Session 相同的 1.11.1 版本；音视频只由 Exo 播放。
- `DefaultPlayerSession` 继续持有权威队列、播放意图和定位合并。`RuntimeSessionPlayer` 保留原适配层的队列编辑、轨道恢复、临时倍速、历史记录和请求取消语义。
- 所有 Exo 调用在同一个应用 Looper 上执行。服务继续独立管理音频焦点和耳机拔出，禁用 Exo 的重复焦点/拔出处理。
- 每次媒体加载拥有独立 Exo 实例及监听器身份，替换前使旧回调失效并释放旧实例；界面展示变化仅切换视频输出，不创建另一个播放器。字幕源变化或需要重建的解码配置变更保留当前进度、播放意图、选择和定位代次。
- 媒体与外挂字幕使用原始授权 URI；Exo 的数据源负责打开和关闭资源。保存媒体身份、外置字幕关联与授权策略不变，不再让服务为字幕维持 mpv 借用 FD。
- 字幕经 Android 平台端口、Media3 `CueGroup` 和独立字幕视图输出。输出携带当前队列项身份，切片、停止、关闭和错误时清空；core 不引入 Android 字幕对象。
- 当前设置和导入资源保持存储兼容。音画与字幕延迟已接入；设置提供解码器初始化回退、无缝帧率匹配、跳过静音和音轨/字幕语言偏好。手动与单个媒体记住的轨道选择优先于全局语言偏好。
- 视频设置按 Exo 的实际能力设计；旧 mpv 专属偏好仍保留在存储中，不添加无效控件。
- 新导入的外挂字幕在替换播放源前执行有界读取与 Media3 解析预检；读失败、格式错误或没有可读 cue 时只拒绝命令。受管文件之后被删除或 I/O 发生变化仍可能导致异步播放错误，预检不等于所有字幕子源错误的完全隔离。旧 `.sub` 资源保留但不再接受新导入；当前支持 SRT、ASS/SSA、VTT 和 TTML/XML。
- 默认 ASS/SSA 解析不等同于完整 libass 渲染。高级特效和内嵌字体附件不属于首版已实现能力；导入字体和基础字幕样式另由字幕视图处理。

## 音画与字幕同步

- 音频延迟支持 ±10 秒。零延迟使用原生 Media3 sink；非零时在默认 sink 前变换已解码 PCM，并同步标记输出时间。正值使用等长 FIFO，前部填静音、片尾截掉延后部分；负值丢弃前导样本、提前剩余样本的 PTS，音频会较视频提前结束。定位后从新位置重新应用偏移，暂停/继续与倍速变化保持现有延迟线。
- 偏移发生在 Sonic 变速之前，单位为片源时间毫秒。非零偏移强制解码为 PCM，暂停压缩直通、offload 与跳过静音；不改变视频色彩处理。原始 PCM 按完整声道帧处理，支持不同位深及 8-bit 无符号静音；延迟缓冲最多 64 MiB，超限格式明确拒绝配置。正偏移在进入 FIFO 前透传 encoder priming，由默认 sink 裁剪；前导计数依实际下传量维护，避免阻塞期间 seek 后泄露 priming。负偏移提高本地最低缓冲并启用时间优先，保证先读完待丢弃的 PCM；极低内存仍受 Media3 的保护策略约束。
- 字幕延迟支持 ±60 秒，并覆盖内嵌和外挂字幕。非零时独立的 TEXT-only source 按偏移后的源位置读取，时间戳再映射到主音视频坐标；字幕不能改写音视频定位结果或扩展视频时长。提前字幕可独立读取较后的内容，不受默认 TextRenderer 一秒预读窗口限制。字幕源只保留所选轨道的缓冲，仍有额外解封装与 I/O 成本。
- 外挂字幕保持 Media3 按需读取。针对 1.11.1 lazy 源丢失 cue 结束时长的行为，仅对外挂整文件解析器在最后有限结束时刻补零时长空样本；保留跨 seek cue 的原输出次序，未知结束时刻不补样本。防止零/正/负偏移下定位到末条中段时误判 EOS，同时不要求打开已关闭或未选中的失效字幕。
- 修改延迟时重新准备当前项，保留当前进度、播放意图、手动轨道选择和定位代次；滑块在松手后应用。普通样式、语言偏好与帧率策略无需重建播放器。

## 视频展示

现有连续旋转和圆角裁剪依赖 TextureView。API 33 的 SurfaceView 并不保证等价的任意角度变换，不能直接替换 View 类后宣称交互不变。

- 稳定且与真实窗口方向对齐的完整播放与 PiP 使用 SurfaceView，保留设备 HDR 输出通路。
- 动画、方向交接和圆角迷你展示使用 TextureView；继续沿用现有展示状态、几何、手势和系统方向交接。
- 两种视图由一个输出协调器管理，始终只有一个输出连接到同一个 Exo；旧 Surface 的销毁不得清除新输出。每次连接复制同一 BufferQueue 的 Surface 句柄身份，过滤 A→B→A 时迟到的旧首帧回调；不增加像素复制或额外渲染层。切到直接输出时，纹理上一帧覆盖到新目标的首帧通知。
- Surface 设置与清除在应用主线程当前调用栈内完成，绕开可能等待字幕 I/O 的普通命令锁。MediaSession 对 Surface 的确认不等待 SimpleBasePlayer 的 pending future，延后解绑会让视图先销毁消费端；真机重建已捕获 `rendering to non-initialized(obsolete) surface`。服务关闭也在释放 MediaSession 前同步解绑；后端重建窗口保留新视图的输出请求。
- TextureView 阶段的 HDR 表现受平台限制。切换输出是否引发黑帧、解码器重配或 HDR 模式变化，必须通过真实设备验证，不能仅由对象身份断言证明。

## 打包与历史材料

应用依赖图不再包含 `platform:libmpv-android`。历史 native 源码、锁定输入、检查工具和原回归保留，可独立维护；它们不再是当前 APK 的构建前提或组成部分。

APK 校验默认检查 Exo 版本标记、应用入口和锁文件一致性，并拒绝旧 mpv 类；仅允许锁定的 AndroidX 原生辅助库，并检查其字节及 ELF/ZIP 16 KiB 对齐；显式 `--engine libmpv` 保留历史产物校验入口。当前 APK 的验证结果不得沿用旧 mpv 的构建或设备收据。

## 验证

主机验证覆盖后端策略、核心与服务适配回归、字幕/输出切换策略、应用编译、Lint、设备测试编译和优化 APK 打包检查。用户已重新授权真机功能测试；本轮使用其连接的 Android 15 手机验证实际播放、字幕、转屏/PiP 与暂停定位。HDR/Dolby Vision 显示、物理音画同步、功耗和长时间稳定性仍需独立验收；本轮不恢复已停止的功耗实验。


历史主机收据见 [dev.exo2 验证记录](../validation/2026-11-04-exoplayer-migration.md)，后续修复、APK 与真机证据见 [dev.exo4 真机回归](../validation/2026-11-04-exoplayer-device.md)。

## API 依据

- [Media3 1.11.1 字幕源构造](https://github.com/androidx/media/blob/1.11.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/DefaultMediaSourceFactory.java)：现代字幕提取路径与合并 source。
- [Media3 1.11.1 AudioSink](https://github.com/androidx/media/blob/1.11.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/audio/AudioSink.java)：PCM buffer、PTS 与重复调用契约；[DefaultAudioSink](https://github.com/androidx/media/blob/1.11.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/audio/DefaultAudioSink.java) 的时间锚点及变速处理。
- [Media3 1.11.1 TimeOffsetMediaPeriod](https://github.com/androidx/media/blob/1.11.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/TimeOffsetMediaPeriod.java)：period 时间坐标映射；当前实现另外限定字幕不能推动 AV 定位，且保留时间 sentinel。
- [Media3 Surface 类型](https://developer.android.com/media/media3/ui/surface)：SurfaceView 与 TextureView 的显示限制。
- [AOSP Android 13 Surface](https://github.com/aosp-mirror/platform_frameworks_base/blob/android13-release/core/java/android/view/Surface.java)：Parcelable 句柄与独立释放语义。
