# 页面切换时刘海白条与画面偏移（2026-09-20）

## 复现与归因

用户在三引擎验证过程中看到刘海区域变白，不能限定为某一种返回操作。复查三引擎各两套真实应用截图，发现共同的窗口缺陷：横屏初始内容正常居中；Activity 重建后，左侧出现 111 px 全高白底，1920×1080 视频从 X=240 移到 X=295，之后 PiP/后台返回继续保持错误布局。三引擎的视频 buffer/crop 没有对应变化。这属于应用窗口布局接入，不能归咎于解码依赖。

证据目录：[cutout-fix-20260920](../../native/out/cutout-fix-20260920/)。本阶段开始前另存数据库/偏好快照；沿用第一阶段的同一台 Android 15 手机和原创 `clarity-clock-audio.mp4`，素材 SHA 为 `2a7018040bf901eca879456e909e9d8684c7b96bb1717904a939b1e16ad3b65b`。

旧包三个诊断记录均保持 V30 主包 SHA `4801e9b863cc86ed977fa0a7bc7472282afebae80f0352bd7c6b736bbe1dde10`：

| 记录 / 操作 | 窗口内容区 | 视频 X |
| --- | --- | --- |
| `vlc-v30-window-before-01` 初始 / 横屏返回 | 2400×1080，X=0 | 240 |
| 同记录 Activity 重建后 | 2289×1080，X=111 | 295 |
| `vlc-v30-window-probe-01` 重设 decorFits=false、请求 insets | 仍为 2289×1080，X=111 | 295 |
| 同记录再设置 cutout ALWAYS | 同上 | 295 |
| `vlc-v30-root-probe-01` 取消 content 父容器 fitsSystemWindows 并清 padding | 恢复 2400×1080，X=0 | 240 |
| 同记录后续 PiP 返回 | 保持 2400×1080，X=0 | 240 |

诊断同时记录：decor 一直是全幅，cutout mode=3，状态栏和导航栏均隐藏；偏移发生在 `android.R.id.content` 的父 FrameLayout。该容器 `fitsSystemWindows=true`，重建后多出 `paddingLeft=111`，而 content 自己的 padding/margin 都为零。日间 Android windowBackground 是白色，播放器 Compose 根和视频 View 则一直明确为黑色。

平台源码也解释了该容器的来源：无标题窗口启用 `windowActionModeOverlay` 时，[Android 15 PhoneWindow](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r5/core/java/com/android/internal/policy/PhoneWindow.java) 选择 [screen_simple_overlay_action_mode](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r5/core/res/res/layout/screen_simple_overlay_action_mode.xml)，它在 content 外又放置了 `fitsSystemWindows=true` 的 FrameLayout。[View 的默认 insets 处理](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r5/core/java/android/view/View.java) 会把其收到的传统系统区域值变成 padding；因此 Window 的 edge-to-edge 设置不能单独阻止这个嵌套容器再次留白。首次启动与重建的精确 OEM 分派时序仍属推断，本次修复依据的是实机父容器状态和可重复的干预对照。

## 应用修复

`MainActivity` 在创建 Compose 内容后关闭平台内容父容器的自动系统区域留白并清除已有 padding。应用继续把原始 insets 传给 Compose，由页面的 safeDrawing/statusBars/navigationBars padding 处理控件避让；没有消费或伪造刘海信息，也没有改视频 buffer、Surface 尺寸或播放器适配层。

播放器/PiP 的窗口背景随页面设为黑色，离开后恢复原主题背景；保存播放器页面可见状态，让重建第一帧也使用正确背景。没有全局更改日间主题或启动屏颜色。

## 回归条件

新增检查同时要求：内容区等于 decor 全幅且原点为零；横屏有左侧黑边时，刘海区域不得成为白底。保留原来的暂停状态、位置、buffer 大小和画面可见性断言。另加入两轮实际返回列表、点击迷你播放器、恢复全屏的流程，不保存偏好或注册测试素材到媒体库。

`vlc-v30-window-regression-red-01` 用新增测试包、旧 V30 主包运行，准确在 Activity 重建后因 content 2289×1080、X=111 失败。该失败和全部诊断原始归档保留，不改写为通过。

## 构建与原生字节保持

应用 12 项 JVM 测试通过，`lintDebug`、主包和测试包编译通过。最终主包 `cutout-fixed-v2-main.apk` SHA 为 `0e017ceaf91a39fa71348e898ef06e0265ca31cd9a2ea44508e939f0f2962392`。第一版回归测试包 SHA 为 `b2d0e61c28da5b114ce43ea14d6b07739f6a736cead8a7ed7b1a6e49c4d265eb`；它在旧包上因窗口偏移失败，在新包上通过重建/PiP 的窗口和白色像素检查，但随后因自动化文字查找接口不支持 Compose 虚拟节点而超时。失败记录 `vlc-cutout-fixed-v2-01` 保留。

测试工具改为遍历真实无障碍节点树，窗口/像素/播放断言保持原要求。第二版测试包 SHA 为 `19799096b6a775f5e434ef32aef78ccfc9e01acfa23e024a90a8ed904bf73a5c`；该调整不改变生产代码或主包。

首次通过 `D:\DevTools\Android\Sdk` 构建时，artifact gate 拒绝了桥接 SO 的散列。独立 ELF 核验发现，两 ABI 的代码、常量等段相同，差异是 `.debug_line` 中 SDK 路径和相应 build-id；其余 32 个 native 条目相同。回到原 `C:\Users\***\AppData\Local\Android\Sdk` 入口重建，恢复两个桥接的原字节，34 个 native 库、原 C++ runtime、16 KiB 对齐及 JNI 校验全部通过。没有改锁文件、替换 APK 条目或升级依赖；首次拒绝包未安装，失败日志保留。

## 新包真机结果

同一修复主包、同一第二版测试包、同一素材下，以下三条完整流程通过。每条包含 10 个播放器布局采样、两次真实列表往返，以及后台播放 5 秒、暂停采样 3 秒后返回。横屏 content 均为 2400×1080、X=0，FIT 视频为 1920×1080、X=240；有左侧黑边的采样中，左 111 px 全区域的白色像素比例均为 0。列表仍正确绘制浅色背景，播放器控件继续收到安全区域 insets。

| 引擎 / 记录 | 窗口、白条、页面往返 | 后台暂停 / 返回位置 |
| --- | --- | --- |
| Exo `exo-cutout-fixed-v2-02` | 通过 | 7057 / 7085 ms |
| mpv `libmpv-cutout-fixed-v2-01` | 通过 | 7003 / 7166 ms |
| VLC `vlc-cutout-fixed-v2-02` | 通过 | 6908 / 6938 ms |

位置断言仍为差值小于 200 ms，没有放宽阈值。mpv 这轮的 163 ms 变化也保留，不代表第一阶段的短后台偏移已被修复。截图和几何汇总为 `window-results.json`，直接对比前后 APK 的 `native-byte-preservation.json` 证明全部 34 个 native 条目字节相同。

Exo 首次新包记录 `exo-cutout-fixed-v2-01` 在重建后因画面亮度采样 2/25 失败。实际截图仍有视频条纹及 2.042 秒时码，所有控件也一起变暗；content 全幅和 X=240 均正确，左刘海白色比例为 0。`exo-first-window-history.txt:124-131` 的系统日志确认：16:50:34.724 收到 `powerPress`，34.726 开始进入休眠，34.895/35.083 执行两次截图，35.156 屏幕关闭。它属于屏幕进入休眠期间的无效画面取样，不能归因于 Exo 黑帧；没有推断是谁触发了电源键事件。后续复测使用同一主包、同一测试包、同一断言完整通过。首轮原始失败保持不变，不能把采集器失败数直接当成依赖缺陷数。

验收范围是这台 Android 15 手机的已记录稳定状态，包含左侧刘海横屏和竖屏；没有声称覆盖所有 ROM、反向横屏、每个动画帧，或页面切换与重建精确同时发生的竞态。

## 收尾与保留

本阶段 9 份原始记录均按 receipt 校验归档，删除手机上 11 个本阶段拥有的结果/素材/暂存路径。修复主包保留安装，测试包恢复到原 SHA `2f48a9b717e4e84ab1ee1c9f4949667f6fb9d3bc2a95a47a36029498062470fd`。数据库完整性、schema、所有表行列和持久偏好前后核对全部通过，`changedFiles=[]`，偏好 SHA 仍为 `9eab1f8d05da0697e0963a4cbc66a622db05018edcd613c2466a1cbde29e3dfb`。检查范围为数据库和 `files/datastore`，不包括正常生成的运行缓存。

设备工作结束时再次确认亮屏，随后恢复原自动息屏 *** ms、插电常亮 ***；读回记录为 `keep-awake-restored.json`。独立视觉抽查确认重建、PiP、页面往返和后台返回的左侧白条消失；左侧仍有系统自身的灰色手势提示条，不属于此白底缺陷。

证据为 `verified-device-archives.json`、`device-cleanup.json`、`data-preservation.json`。三依赖审查的分项结论见[三引擎接入与比较条件复核](2026-09-20-three-engine-audit.md)，本 UI 修复不代表其中字幕或时钟边界已经消失。
