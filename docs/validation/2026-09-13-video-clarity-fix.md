# 默认视频输出清晰度修复

> 当前状态（9 月 14 日收尾）：V30 已安装，通过三引擎默认取帧、连续两轮 VLC 暂停重建/PiP/后台定位及返回、恢复期间选轨和播放/暂停意图、非零初始位置恢复。严格后台时钟检查也通过。103 份设备记录已归档并清理，原测试包已恢复，最终数据库及持久偏好核对无变化。固定 eap29 的内置 MKV 字幕跳转限制仍保留；未执行新功耗矩阵，未提交或推送 Git。

## 已定位问题与实现

此前的[真机 A/B](2026-09-13-video-clarity-ab.md)在同一片源、同一暂停位置复现了两处独立问题：mpv/VLC 默认将 1080p 放大到 2400×1350 后再显示为 1920×1080；VLC 的硬解 SurfaceTexture/GLES 输出在原尺寸下仍有纵向采样异常。

- mpv 的 GPU 输出缓冲根据选中视频轨计算，普通 1920×1080 直接使用 1920×1080。非方形像素先换算为方形像素尺寸，旋转交换宽高，超大输出限制为 4096 长边；窗口旋转和 FIT/FILL/STRETCH 只改变显示矩形。Exo 保持解码 Surface 的源尺寸与自身 crop/rotation 处理。
- VLC 使用视频 Surface 和独立透明字幕 Surface，通过布局回调启用 `android-display`。普通本地源经独立 FD 预解析，确认没有旋转/镜像后使用 MediaCodec 直接输出；带变换或无法确认变换的源使用官方 `:no-mediacodec-dr` 图像复制路径，保留硬解及 GLES 回退。原生 SurfaceControl 子层使用实际 View 窗口尺寸，父 SurfaceView 不再叠加固定缓冲缩放。界面已选定 FIT/FILL/STRETCH 矩形，原生 DAR 与该矩形一致；旋转复制输出交换 DAR 轴。
- VLC 直接输出的父 Surface 没有视频队列；不能将父层 PixelCopy 的 `ERROR_SOURCE_NO_DATA` 判成黑屏，也不能伪造原始视频读回。测试改为保留系统读回及 SurfaceFlinger 子层 crop/变换证据。
- 暂停状态更换窗口时重新打开 VLC 播放器，按最新有效 Surface 绑定，恢复位置及音轨/字幕选择，避免旧 vout 关联失效窗口。全新暂停播放器可能既不创建视频输出，也不产生时间点；恢复时改为音量为零的短暂解码准备，在实际定位完成后请求暂停，等原生 Paused 再恢复音量并发布就绪。重建任务串行调度，播放器释放后重新取得媒体 FD；复用的 seekable `fd://` 描述符先复位。用户在恢复期间更改轨道时更新恢复快照；仅此次重建保留轨道元数据及字幕缓存，正常停止/换片仍清理。
- 排队重建仅在取得生命周期锁、确认当前窗口有效后进入恢复状态。无窗口暂停定位立即确认原生逻辑游标，避免等待窗口期间触发核心 8 秒超时。此类定位后再次后台播放时，以该游标重新建立播放器时钟，避免固定内核旧管线的 PCR/时间点跳变；普通后台播放路径不变。
- JNI seek 桥接等待原生定位起止及后续时间点，通过公开 time-point 插值返回实际原生时钟。它不把请求值当作实际完成位置，也不把时钟通知当作物理画面完成的证明。外挂字幕加入后等待真实新增轨道，再显式选择，修复字幕禁用后 `addSlave(select=true)` 未生效的问题。

官方 libVLC/libmpv 解码库字节未修改；仅重编译自有 `libzivplayer_vlc4.so` 桥接。默认引擎仍为当前实验分支的 VLC。用户数据检查范围见下文。

## 设备与清晰度证据

本机证据目录：`native/out/clarity-fix-20260913/`。设备为 23049RAD8C、Android 15，屏幕 1080×2400；固定测试图为 1920×1080、24 fps、BT.709/有限范围、SAR 1:1 的 H.264，SHA-256 为 `2e8afcefd66a50e556b87cde88176e4a9cb48fccde71c99222149bd5b2d7d26e`。每轮重新启动测试进程，在 2 秒位置暂停后取三帧，正常输出不传 Surface 尺寸覆盖。

以下为同一引擎修复前后的屏幕读回指标，采样位置和归一化方法见分析脚本；不是主观画质百分比或屏幕光学 MTF：

| 输出 | 一像素竖线调制度 | 一像素横线调制度 | 斜边 10–90% 宽度 |
|---|---:|---:|---:|
| 旧 mpv 默认 | 0.5540 | 0.5525 | 1.178 px |
| 修复后 mpv 默认 | 0.9956 | 0.9948 | 0.803 px |
| 修复后 VLC 默认硬解直接输出 | 1.0000 | 1.0000 | 0.800 px |

早期 mpv 的 `mpv-fixed-default-01/02`、VLC 的 `vlc-fixed-default-04/05` 分别重复一致；最终 V30 的 `libmpv-v30-default-final-01`、`vlc-v30-default-final-01` 再次复现上述指标，每轮三张屏幕图逐像素一致。`exo-v30-default-final-01` 也完成回归取帧。主 APK SHA-256 为 `4801e9b863cc86ed977fa0a7bc7472282afebae80f0352bd7c6b736bbe1dde10`，三轮均有实际安装哈希回执，分析日志为 `analysis-v30.log`。V28 的同名阶段记录保留，不改标为 V30。

VLC 日志确认 `c2.qti.avc.decoder`，SurfaceFlinger 显示原生 `vlc_video_control` 子层，1920×1088 分配裁剪为 1920×1080 后按 1:1 显示。`vlc-fixed-software-01` 和 `vlc-copy-default-01` 的原尺寸固定图检查也通过。直接输出没有可用的父层 PixelCopy 视频帧，报告中对应 raw 指标留空。

Exo 默认输出的回归取帧通过，但既有系统读回与解码 Surface 的纵向结果不一致；详见前次 A/B。该证据不能用于引擎之间的屏幕光学排名。

## 回归与验收边界

几何扩展场景和最终包的验收分列如下。较早 APK 上完成的几何和字幕样本没有重新测成完整矩阵，不将其改标成 V30 新运行：

| 范围 | 已完成证据 | 验收边界 |
|---|---|---|
| mpv 非方形像素、90 度元数据、4K | `mpv-fixed-sar-01`、`mpv-fixed-rotate90-01`、`mpv-fixed-4k-01` | 输出几何、裁剪及可见画面 |
| mpv FIT/FILL/STRETCH、暂停旋转、页面重建、PiP | `mpv-fixed-regression-02` | 暂停位置及截图 |
| VLC 非方形像素、4K、90/270 度元数据 | `vlc-fixed-sar-01`、`vlc-fixed-4k-01`、`vlc-policy-rotate90-01`、`vlc-reopen-rotate270-01` | 几何及变换；旋转源采用图像复制路径 |
| VLC 带音频的暂停重建、PiP、后台定位/播放/暂停再返回 | `vlc-v30-background-clock-final-02/03`，V30 | 连续两轮通过 READY、原生暂停、恢复快照清除、实际经过时间范围及画面检查；返回位置分别为 2,417 / 2,600 ms，不是 sample-exact 同步证明 |
| VLC 初次恢复非零位置 | `vlc-v30-background-clock-final-03`，V30 | 请求 2,000 ms，Prepared 时控制器为 1,951 ms、原生及桥接为 1,958 ms 且已暂停；未使用请求值伪造原生时钟 |
| 窗口快速销毁及无窗口暂停定位 | `vlc-v30-background-clock-final-02/03`，V30 | 在真实 lifecycle Mutex 内排队替换窗口后立即销毁；恢复标记无残留，2,416 ms 逻辑定位完成，随后后台播放建立新时钟 |
| VLC 内置 ASS/SRT 顺播至 5 秒 | `vlc-v28-embedded-ass-linear-01`、`vlc-v28-embedded-srt-linear-01`，V28 | 与跳转路径分开验收，检查实际中文、双行、描边 |
| VLC 外挂 ASS/SRT 选择及禁用 | `vlc-v28-subtitles-final-01`，V28 | 原生选轨状态及画面字幕变化；不包含内置字幕跳转后的视觉通过 |
| 恢复期间用户切换轨道、先播放再暂停 | `vlc-v30-restore-intent-final-01`，V30 | prepared=false、surfaceReopening=true、nativeVolume=0 时将 spu/3 改为 spu/2，再依次发送播放、暂停；重建及 PiP 返回后保持最新选择和原生暂停 |

失败证据也完整保留：V20/V22 暂停初始化定位超时，V21 仅在详细日志下通过。V24 轻量回调确认定位结束及帧请求都发生在新播放器首个时间点之前，随后没有时间更新；V25 等首个时间点、V26 等 Vout 均超时，说明不能被动等待全新暂停管线自行启动。V27 静音启动解码后通过，V28 删除诊断及额外帧步进后连续两轮通过。V23 第一轮停在系统锁屏，未进入播放器，单列为环境失败。

收尾增量检查中，V28 的 `vlc-v28-absent-seek-repro-01` 复现无窗口定位超时，`vlc-v28-transient-surface-repro-02` 复现排队窗口销毁后恢复标记残留、无法后台播放。V29 修复这两项，但较宽松的回归检查未限制后台进度相对真实经过时间的跳变，不能据其通过结果验收时钟连续性。同步记录确认 `vlc-v29-bridge-background-timing-01` 中 bridge 和 getTime 都从 2,416 ms 提前到约 8,718 ms；新增严格断言后，`vlc-v29-clock-jump-repro-01` 明确失败：仅播放 144 ms，暂停位置却为 9,525 ms。

V30 对无窗口定位后的播放重建时钟，`vlc-v30-background-clock-final-02/03` 连续通过新断言：后台暂停/返回位置分别为 2,452/2,417 ms、2,584/2,600 ms，实际画面中的时码也保留。固定公共 time_point 可来自 input/demux 或 output，且不携带来源标记，不能靠 system_date/rate 过滤为物理显示帧；固定源码哈希及来源记录在 `native-timer-contract.json`。另有两项测试工具错误单列：V28 transient-repro-01 的反射方法匹配不唯一，V30 background-clock-final-01 未处理重开期间原生播放器暂时为空；修正测试后使用新标签重跑，旧记录未覆盖。

4K 样本由 1080p 测试图放大生成，仅检查输出几何与裁剪，不证明真实 4K 细节保真。以上未覆盖 HDR、全部编码、所有机型或网络源完整矩阵。

`subtitle-analysis-v28.json` 对同位置字幕开关截图按原像素比较：外挂 ASS/SRT 分别改变视频内 60,199 / 39,676 个像素，变化全部位于下方字幕区域；字幕区域外的视频像素不变。与旧报告结果一致，内置 ASS/SRT 在跳转后的画面区域没有变化，不能因测试脚本的选轨状态断言通过就写成视觉通过。

## 固定 VLC 预览版本的剩余限制

同一含两条内置文本字幕的 MKV，顺播可见字幕；直接定位到已有字幕事件中间的 5 秒后，选轨成功但字幕 Surface 没有字幕缓冲，当前活动字幕未重新出现。外挂同内容字幕能够显示，内置字幕的数据包时间也覆盖目标位置。该隔离结果将问题收窄到固定 eap29 的原生跳转/字幕预读路径；本次没有替换官方内核，不能宣称该限制已修复。

早期固定内核测试中，暂停 MKV 精确定位也有未满足请求位置的例子：请求 3,500 ms，原生时间点返回约 4,583 ms。该样本未在 V30 重跑，不能标为已修复；桥接仍保留实际返回值。主机测试证明桥接关联、引用及回调行为，不能保证 VLC 每种封装都逐帧准确。以上两项应纳入后续内核升级或专门定位测试。

## 本地产物、验证与数据保留

- 最终主包：`native/out/clarity-fix-20260913/fix-v30-main.apk`，SHA-256 `4801e9b863cc86ed977fa0a7bc7472282afebae80f0352bd7c6b736bbe1dde10`，已安装到设备。最后六轮通过的验收使用 `fix-v35-test.apk`，SHA-256 `f545ae771b1eea941baef32367b4320ca9e6e95482566b0456cca6947b8d08ed`。全部实际安装哈希、归档哈希见各轮 `receipt.json`。
- `build-unit-v30-background-clock.log`：最终主包、测试包构建及 Android app / libmpv / playback 单元测试通过；core player-api / player-runtime 测试保持有效的 up-to-date 结果。最终测试脚本空值修正在 `build-test-v35-native-reopen-timing.log`，不改变主包字节。
- `artifact-v30.json`：34 个原生文件、158 个 DEX 原生方法、固定 JNI 描述符、16 KiB ZIP/ELF 对齐以及原有 C++ runtime 字节检查通过。验证器此前终端输出的 157 为过期常量，现改为动态计数。官方 VLC 两个 ABI 共六个库的哈希保持固定值。R8 保留规则已同步实际 `(long, long)` 回调签名，但本次没有构建/验收启用压缩的 release 包。
- `host-seek-v28.log`：实际 C 桥接的 ASan/UBSan 主机测试通过，覆盖 JNI 所有权、seek 起止/时间点、暂停/播放时钟、拒绝、原生线程回调及关闭/重开。V29/V30 未改变 C 桥接，源码及二进制哈希仍与该测试对应。真实解码、字幕和画面由设备记录验证。
- `data-preservation-v30-final.json`：最后设备清理、恢复原测试包之后，数据库完整性、全部表结构、全部行及列摘要、持久偏好均与测试前一致；媒体库 565 项、最近播放 405 项等数据没有变化，偏好 SHA-256 均为 `9eab1f8d05da0697e0963a4cbc66a622db05018edcd613c2466a1cbde29e3dfb`。检查限定于数据库和 `files/datastore`，没有用旧备份覆盖用户数据。
- `cleanup.json` 首次清理 85 份记录；`cleanup-final.json` 重新检查这些归档及远端缺失状态，再逐成员核对并清理新增 18 份记录和复用片源，共保留 103 份原始成功/失败记录。只删除本任务明确命名、内容哈希匹配的结果与片源；本地原始归档不删改。
- `installed-final-v30.json` 确认主包为 V30，原测试 APK 已恢复为 SHA-256 `2f48a9b717e4e84ab1ee1c9f4949667f6fb9d3bc2a95a47a36029498062470fd`。按用户要求测试与收尾期间持续常亮，原息屏时限 *** ms、充电常亮配置 *** 保存在 `keep-awake-before.json`；最终恢复回执为 `keep-awake-restored.json`。
- 本地未提交源码以 HEAD `***` 加 `source-v30-final-local.zip` 保存；`source-v30-final-local.json` 关联源码逐文件哈希、主/测试包、构建与验收证据。没有提交或推送。

## 功耗结论

本次没有执行新的功耗矩阵。此前各批次的传感器数据保持原样，但其适用范围已在三份旧报告和[历史功耗范围修正](2026-09-13-power-evidence-scope-correction.md)中标明。修复后的性能必须使用新 APK、新 cohort 和默认输出重新测量，并先完成画质门槛。
