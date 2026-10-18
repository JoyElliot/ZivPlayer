# 2026-10-18：新版播放器真机验收

## 授权与最终安装状态

功耗实验结束后，用户授权在手机上安装新版并执行功能测试，并明确要求保留新版。仅使用 `home-feishu-style` 测试工作树；没有修改实验分支或消息干预实验线程。

设备：Xiaomi 23049RAD8C / marble，Android API 35。两个无线 ADB 地址经硬件 serial 与 boot ID 确认为同一台手机，命令始终显式选择同一个连接。

测试前备份原主 APK、原测试 APK，以及完整 `files`、`databases`、`app_vlc`、`app_dxmaker_cache`（372 份文件、1,399,060,480 字节 tar），记录 SHA-256；没有清应用数据。备份仅存于本机忽略目录。

最终两个包均保留新版，未回装旧 APK：

| 包 | 字节数 | 设备端核对的 SHA-256 |
| --- | ---: | --- |
| 主 APK | 292559972 | `5f3a471732b161d9edd626d83530c1070d99c7872387350c2000813bc1befab3` |
| 测试 APK | 10492735 | `a8e66bace93e6d69e7bcb1917054cd7557ea7a051157e783cee1700ef98de436` |

主 APK 与上一轮已审计产物一致，本轮代码变更均在 instrumentation、测试素材生成器与文档中。收尾已启动新版至视频首页。

## 最终设备结果

最终 instrumentation 为 **OK (16 tests)**，81.687 秒；15 个布局/交互合约用例与 1 个真实服务、JNI、解码器综合用例分别具有不同的证据范围：

| 组 | 数量 | 覆盖 |
| --- | ---: | --- |
| PlayerPresentationContinuityTest | 4 | TextureView 对象保留、弹簧中断、闲置滑条取消、拖回按下点持续预览 |
| FullscreenGestureTest | 10 | 单击/双击、水平/垂直滑动、长按倍速、菜单、锁定、滑条取消与媒体身份切换 |
| PlayerUiContractTest 指定方法 | 1 | 迷你条导航与播放按钮行为独立 |
| ContinuousPlaybackDeviceTest | 1 | 真实视频帧、同一 SurfaceTexture、内部旋转、迷你展开/收起/反向拖动、手动/自动系统 PiP、暂停预览与精确定位、系统媒体音量、暂停 Activity 重建 |

最终综合用例使用新增的 45 秒自生成 H.264/AAC 素材，640×360、24 fps、GOP 上限 250 帧。SHA-256 为 `84a556c3efa49511c151bbdbc9b18dcd9bce27cfd2ad92ac1347036c1c822a1b`，设备缓存读取的实际文件已取回并与记录核对；新文件名避免误用先前安装的测试 APK 缓存。

较长素材避免把 12 秒样片的片尾循环误当作窗口交接卡顿。最终从开始播放到主动暂停期间，`isPlaying` 没有变为 false；各交接点保留同一 TextureView 与 SurfaceTexture。迷你、完整、收起后的迷你、手动 PiP、自动 PiP 分别采样 5 次，均取得 5 个不同帧时间戳和 5 个不同像素散列。主代理另目视核对了系统 PiP、迷你视频与重建后的截图。

这些证明功能与采样窗口内的画面更新，不是逐帧呈现或无丢帧保证。Compose 合约测试使用虚拟时钟，不据此给自然动画帧率打分。启动时诊断确认 `decoder=mediacodec`、视频 640×360/24 fps；该时刻 droppedFrames=0、decoderDroppedFrames=0，但没有把单次诊断扩大为全程丢帧结论。

## 预览与音量观察

暂停时，手指未松开便观察到正向与反向定位的新像素；松手后完成精确定位且保持暂停。本次单样本观察：首个预览画面约 172 ms，松手到位置及新画面满足条件约 125 ms，最终位置 21,539 ms。计时包含测试注入、框架同步及轮询开销，不是通用基准；没有据此宣称任意视频能达到 20 fps 拖动预览。

这台手机提供 0–150 的媒体音量范围，因此走系统原生细档位，软件 gain 保持 1。应用音量调整实际写入系统 index=3；模拟音量上键的读回序列为 3、13，最终界面与系统同步。首按只唤出面板、随后才调整的行为得到系统调节请求日志佐证；[AOSP 音量面板抑制逻辑](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android15-release/services/core/java/com/android/server/audio/AudioService.java)提供对应实现线索。该设备未验证低档位设备的软件插值，后者仍只有主机策略测试覆盖。

## 测试修正与主机检查

- 为连续性测试接入既有 DeviceForegroundRule，处理小米系统拒绝冷启动后台测试 Activity 的环境条件。
- 综合测试在首次 Compose idle 和媒体轨道就绪后再播放；用真实帧像素及时间戳变化验证预览，不只检查控件或乐观位置。
- 单击隐藏控件的用例等待双击识别窗口及 120 ms 淡出结束，更新原来仅等待 400 ms 的假设。
- 音量测试记录独立按键尝试与实际读回，覆盖系统面板抑制首按的行为。

最终测试 APK 构建与 `lintDebug` 均通过（29 秒，344 项任务，15 执行、329 已最新）。Lint 仍为既有 0 error、8 warning。主程序源码未变，因此未重复运行上一轮已通过的 225 项 JVM 测试。

对应提交：`***`（前台规则，3 行）、`***`（淡出等待，3 行）、`***`（综合设备验收与长素材，352 行）。文档另行提交，继续遵守单次不超过 1000 行。

## 数据与环境收尾

测试结束后停止目标应用，恢复测试前数据库及 DataStore，逐文件校验后重新启动新版。原有 371 份用户数据文件散列一致，涵盖数据库、偏好、托管资源、实验记录与素材；差异仅为新版运行生成的 `files/profileInstalled` 优化标记及 3 份 `files/mpv/shader_*` 编译缓存，保留它们供新版使用。

亮度、亮度模式、屏幕超时、自动旋转、用户旋转、飞行模式、Wi-Fi、蓝牙、插电保持唤醒与三种系统动画比例均与开始时相同。媒体音量最终读回 0，与开始时一致。仅清理本轮创建并已归档的截图和 JSON，不删除原实验文件。Windows ADB 标准输入传输 tar 曾出现字节转换，恢复前改用 sync push 并验证设备端 SHA-256，再从设备本地解包；校验通过后才进行文件恢复。

证据位于当前工作树 `build/device-validation-20261018-102812/`：`final-device-suite.txt`、`continuous-device-result.json`、截图、实际测试素材、`before.json` / `after.json`、`before-file-manifest.json`、`user-data-comparison.json`、备份与实际测试 APK。失败尝试的记录保留；最终结果以 16 项全通过的合并运行及其 APK 身份为准。

本轮不覆盖全部编码/分辨率、HDR、蓝牙实际音频输出、粗档位设备插值、自然动画逐帧性能或新的功耗结论。
