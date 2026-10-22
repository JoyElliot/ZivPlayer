# ADR 0021：连续视频展示、实时拖动与系统媒体音量

- 状态：已接受；实现与主机验证见 [验证记录](../validation/2026-10-18-continuous-player.md)，基础设备验收见 [真机记录](../validation/2026-10-18-continuous-player-device.md)，其他设备和性能边界仍需分别验证
- 日期：2026-10-18
- 范围：`home-feishu-style` 测试分支的 app、feature/player、设计系统、播放器命令和 libmpv 适配
- 边界：实验分支和整夜功耗手机不参与本轮修改、安装或测试

> 底栏外观现由 [ADR 0022](0022-selectable-home-dock.md) 补充：液态玻璃为默认，扁平底栏为可选的简洁方案；本记录的视频交接、预览和音量设计继续适用。
> 2026-10-23：[ADR 0023](0023-system-rotation-handoff.md) 替换下述内部转屏策略，改为自定义动画后交接到真实系统方向；本轮只做主机验证和静默安装。

## 背景

旧展示把视频组合在完整播放器与画中画之间迁移，迷你播放条显示抽帧封面；横屏按钮请求系统改变 Activity 方向。窗口迁移和 Surface 生命周期耦合，无法把收起、展开、转向和手势中断视为同一段连续运动。拖动进度仅在松手时精确 seek，所有请求都使用相同定位模式；音量滑条仅改变播放器内部增益。

本决定更新 ADR 0019 的玻璃底栏及视频交接方式，保留四个首页入口、目录队列、持久设置和 ADR 0020 的层级导航。Service、MediaSession、core actor 和 Surface lease 仍拥有原有职责。

## 视频与窗口

- `PersistentVideoStage` 在有媒体时保留唯一 `PlayerVideoSurface` 调用点。迷你、完整、旋转和 PiP 使用同一 TextureView；展示模式不再作为 View 或 Surface 绑定的 key。视频自然宽高比变化可以更新内容几何，正常播放切换不以动画驱动播放器重建。
- 视频用稳定的内容测量尺寸，通过图层的位移、缩放、旋转和圆角插值定位。主页保留布局，用真实迷你槽位作为收起终点。槽位在展开时也跟踪窗口和 Insets 变化，避免分屏缩放后飞向旧坐标。
- `PlayerPresentationState` 保存当前进度和弹簧速度；新手势从当前帧接管，释放后按进度与速度续接。迷你条和其视频可上滑展开，完整播放器顶部手柄可下拉收起；轻点与返回沿同一几何路径运行。
- 完整控制层只在展开稳定且非拖动时参与放置，避免透明控件拦截过渡中的手势。视频层与控制层使用同一旋转坐标，菜单和队列也在窗口内旋转；不再使用独立 Popup 承载这些面板。
- 横屏按钮改变内部展示角度，播放器展开期间锁定当前 Activity 方向。视频与控件旋转，不请求 sensor-landscape 硬切。手动按钮行为不等于新增物理姿态自动旋转；大屏系统是否尊重方向锁由 Android 决定。
- PiP 仍由系统管理：提供真实视频矩形作为 `sourceRectHint`，使用自动进入资格及 seamless resize；进入前隐藏控制层、回正视频方向。生命周期区分进入中的 PiP 与普通后台离开，短暂等待确认再应用关闭后台播放时的暂停策略。

应用只能保证这些展示路径不主动 pause、重建解码器或切换视频 Surface。系统 PiP 时间线、Activity 重建、硬件解码、厂商窗口策略和实际掉帧不能由源码检查保证。

## 拖动预览与定位

- `SeekPrecision.KEYFRAME` 是交互预览，`EXACT` 是点击、松手和普通定位的默认语义。libmpv 分别映射 `absolute+keyframes` 与 `absolute+exact`；不因预览临时暂停或切换渲染器。
- UI 超过触摸阈值后持续预览，包括拖回起点附近；点击和键盘调整直接提交一次精确定位。取消已开始的预览时整理最后目标，移除闲置滑条不触发别人的 seek。
- 控制器只保留一个执行中请求和一个最新待处理目标，预览发送间隔至少 40 ms，最终精确请求不受该节流。core 也合并排队定位；同一位置的 KEYFRAME 后必须执行 EXACT，不能仅按时间戳去重。
- 自定义 Session 命令限制为同 UID 控制器，携带媒体 ID、安装代次和队列位置；服务在队列/操作闸门后再次校验身份，过期拖动不能跳转新媒体。
- 位置轮询只更新时间；定位与 buffering 事件不重新构造整个队列和轨道列表。列表仅在对应 timeline、metadata、track、item 或可用命令变化后刷新。
- 拖动期间抑制加载转圈，普通 buffering 延迟 350 ms 再显示，避免短暂状态闪烁；这不代表隐藏或消除了真实解码延迟。

40 ms 是请求节流上限，不是 25 fps 承诺。关键帧预览受 GOP 密度、索引、数据读取和硬解刷新成本约束；长 GOP 视频可能仍只显示稀疏帧。松手精确定位仍需要解码目标前的帧。本轮不引入另一路缩略图解码器，不用未经测量的全局 cache 或画质参数换取表面速度。

## 音量

- 音量手势和菜单控制 `AudioManager.STREAM_MUSIC`。软件增益仅在系统相邻档位之间衰减，不超过 1，不突破系统安全音量限制。
- 系统少于 50 个档位，且公开 API 能取得单一预期输出类型及有效 dB 曲线时，在相邻档位的 dB 值之间插值，并适配后端整数百分比精度。预期设备不是实际 AudioTrack 路由的证明。
- 没有可靠曲线时保留系统档位；最低一档可从静音平滑进入，不假设所有设备的音量曲线是线性的。系统已提供较密档位时使用其原生档位。
- 每次写入后读回实际档位，系统拒绝、固定音量或安全限制时显示实际状态。硬件音量键、外部档位变化及输出设备变化使补间增益归一，避免继续沿用旧路由衰减。公开 `onKeyUp` 观察按键，不拦截系统音量处理。
- 只有 Activity 开始期间轮询音量，250 ms 读档位、最多每秒查询预期路由，另监听设备变化；离开后停止轮询。

## 视觉组件

保留 Compose/MIUIX 的既有依赖基础，使用源代码内的 Compose 图形和状态动画统一扁平导航、卡片、主按钮、开关、滑条和播放器面板。底栏不再持续捕获背景、折射或绘制玻璃高光。播放/暂停图标用旋转与路径形变衔接，按下缩放和键盘焦点保持反馈；滑条保留 48 dp 触摸区域和可访问的进度语义。

## 验证与验收边界

主机测试覆盖 seek 合并、精度升级、播放/暂停意图、请求代次、音量单调性及几何端点。新增 Compose instrumentation 用例覆盖 TextureView 对象保留、弹簧中断、滑条取消与拖回起点；本轮仅编译这些用例，不执行设备测试。

后续空闲设备必须验收：有声视频迷你/全屏往返，手势半途反向，横屏菜单与队列触摸，旋转时进入/退出 PiP，多窗口 resize 后收起，不同 GOP/编码的预览帧率与松手定位延迟，耳机/蓝牙/安全音量及硬件键，TalkBack/键盘、暂停状态和系统动画关闭。功耗结论需要独立的新基线，不能与另一线程正在运行的实验混合。

## 参考

- [TextureView](https://developer.android.com/reference/android/view/TextureView)、[Compose 图层变换](https://developer.android.com/develop/ui/compose/graphics/draw/modifiers)
- [PiP 进入](https://developer.android.com/develop/ui/compose/system/pip-enter)、[PiP 参数](https://developer.android.com/reference/android/app/PictureInPictureParams.Builder)
- [AudioManager](https://developer.android.com/reference/android/media/AudioManager)
- [mpv 手册：seek 与精确定位](https://mpv.io/manual/stable/)
