# ADR 0023：自定义旋转与真实系统方向交接

- 状态：已实现；追加 Xiaomi 真机交接修复，验证记录见文末
- 日期：2026-10-23
- 范围：`main` 播放器展示、Activity 方向与 PiP 协调

## 背景

ADR 0021 通过锁定 Activity 方向、在应用内旋转视频和控件来维持连续展示。用户要求动画完成后最终改变真实系统方向，尽量避免二次旋转、闪屏和播放中断。本决定替换 ADR 0021 的内部旋转方向策略，保留唯一播放器、唯一视频输出、双底栏外观和其他播放行为。

## 决定

- 展开时记录进入播放器的显示 rotation 和窗口横竖方向。当前媒体准备完成并取得有效显示比例后，横视频默认横屏、竖视频默认竖屏；显示比例考虑旋转元数据和非方形像素。未知尺寸不采用渲染用的 16:9 回退值决定方向。每次打开或队列项目更换重新判断，同一项目加载期间和加载后的手动转屏优先，方形视频保持入口方向。
- 旋转按钮在入口基准上转动 90°；反向按钮及收起完成后恢复入口方向，支持从横屏、反向竖屏进入。
- `PlayerWindowCanvas` 同时包裹首页、底栏、持久视频及播放器控件。它们共享入口逻辑坐标，首页安全区在本次展示期间保持，避免系统旋转时底栏重新布局、迷你终点换位。内部先完成动画，再经过一次绘制机会请求固定系统 orientation；这不是 SurfaceFlinger 已显示该帧的证明。
- 系统接管时，在同一测量路径中交换逻辑画布宽高，并把外层角度设为显示 rotation 差值的相反数。内部角度不重新执行一次归零动画；合成角度在正常交接端点为零。改变基准、窗口尺寸和 PiP 不改变 `PlayerVideoSurface` 调用位置或 View key。
- 对 rotation 早于尺寸、尺寸早于 rotation 的交付顺序，保留已接纳方向及逻辑尺寸，等待窗口轴向一致。分屏/自由窗口允许独立 resize。该策略不代表 configuration、View 测量与系统合成在公开 API 上具有原子性。
- 自定义动画已经完成旋转，因此使用 `ROTATION_ANIMATION_JUMPCUT`，并保留不透明全屏窗口条件。此前的 `SEAMLESS` 在 Xiaomi 上因导航栏换边被拒绝，回退成约 423 ms 的第二次系统旋转；不能把它当作强制无动画的开关。固定方向映射考虑自然横屏与 `config_reverseDefaultRotation`，资源不可访问时采用平台默认策略。不调用隐藏 Binder，不使用截图占位或另一个播放器。
- 新旋转取消旧动画；后台取消尚未提交的动画，回到前台继续最新意图。配置恢复保存方向基准。1.5 秒检查只用于识别系统未满足方向请求、允许正常退出，不用于安排动画交接或判断真实帧已经显示。

## 迷你条、控件与画中画

- 迷你视频槽位相对同一个逻辑画布上报位置，包含底栏祖先图层的缩放、平移；外层系统方向补偿不进入槽位坐标。两种底栏共用这一处理。
- 返回和下滑收起使用同一展示进度插值位置、尺寸和角度，横屏画面直接旋转缩到入口首页的迷你条。到达迷你端点后才把隐藏的展开角度归零并请求系统恢复方向；画布补偿保持同一画面位置，不额外播放一段转回竖屏的动画。上滑展开、途中反向及重新点击继续从当前进度运动。
- 展示生命周期与首页输入分开管理。关闭意图成立、进度不大于 0.5 且未进入 PiP 时，显露的首页立即接收滚动、点击和返回键；剩余弹簧尾部与系统方向回执继续维护画布，不再用透明全屏层阻断输入。视频本身的命中区域仍跟随其图层变换，可以重新接管动画。
- 连续进度只在视频/触摸目标的图层变换与遮罩绘制回调中读取；组合层观察迷你端点、展开端点、控件可见和首页可操作等离散状态。固定视频输出尺寸，不因缩放动画逐帧重组和测量承载布局。首页保持绘制，仅一层黑色遮罩渐隐，避免重复的整页透明度合成。
- 同一逻辑画布尺寸下，首页与控件的 Insets 包装器保持身份稳定，在 getter 中读取最新方向、角度及基准持有状态，避免系统换轴使静态 CompositionLocal 提供的整棵首页失效。隐藏控件保留组合和状态，在旋转 Layout 的测量策略内停止对子树的测量；重新显示时使用当前约束。门控不能只放在外层 layout modifier，因为内部布局仍可能独立失效并重新测量。
- 控件、队列、音频占位和状态提示共用进入播放器时的逻辑安全区，按应用内动画角度映射。第一次旋转开始后保持该基准直到展示会话或逻辑尺寸改变，避免系统 Insets 与画布轴向分别更新时短暂把左边距映射到底边。恢复横屏时从已分发的 `rootWindowInsets` 初始化；API 35 起还检查 Insets 对应窗口尺寸，未就绪的快照只用于临时绘制，不永久锁定。顶部拖动条位于同一个补偿画布与旋转容器内。
- PiP 源矩形采用包含祖先图层变换的真实 window 坐标，再裁剪到可见窗口，避免 FILL 的屏外区域参与系统缩放。
- 动画期间只更新本地 PiP 源矩形，完整展开并与窗口对齐后再发布系统参数；显式进入 PiP 时读取最新矩形。缓存设备能力、播放/暂停 RemoteAction 及 PendingIntent，避免位移缩放每帧重复分配并访问系统服务。
- 应用内 PiP 按钮先把视频对齐当前真实窗口，再调用系统进入。自动 PiP 仅在完整展开、最终目标和实际画面方向都已对齐时启用。PiP 打断收起时先恢复完整展示，不能把正在缩小旋转的半展开矩形作为已对齐画面。
- 若旋转途中按 Home，系统离开不能等待后台的 Compose 帧时钟：在 `onUserLeaveHint` 内及时提交 PiP，并立即将视频方向对齐当前窗口。这个打断路径优先保证 PiP 进入与画面方向；不承诺与应用内按钮一样完整播放对齐动画。PiP 内隐藏的旋转状态更新到最终意图，退出时不复活旧的中间角度。

## 验证边界

JVM 测试检查四方向映射、自然横屏、反向策略、两种回调顺序、分屏尺寸、FIT/FILL/STRETCH 端点、迷你方向及 PiP 对齐条件。安全区回归检查两种 Insets/窗口更新顺序、反向旋转、横屏恢复、初始化未就绪和合法全零安全区。Compose 用例覆盖快速反向取消、系统接管不重播动画、PiP 打断、后台恢复和下滑接管；真实播放用例等待真实方向变化，再检查同一 TextureView/SurfaceTexture。

初次实现仅按当时授权做主机检查和静默安装，保留原始[验证记录](../validation/2026-10-23-system-rotation-handoff.md)。用户反馈二次旋转、随后反馈 UI 跳位并授权操控手机后，追加设备日志与录屏诊断，见[后续修复记录](../validation/2026-10-23-double-rotation-fix.md)。不把单机结果推广为所有 OEM、所有帧的零冻结或零闪屏保证；录屏投影自身也可能比物理显示晚一帧，必须结合窗口日志判断。

自动方向与同步收起的增量验证另记于 [2026-10-23-automatic-orientation-collapse](../validation/2026-10-23-automatic-orientation-collapse.md)。此前真机通过的结果只代表当时构建，不作为新路径已经通过设备验收的证据。

收起过程的帧耗时、输入提前恢复与系统交接布局优化见 [2026-10-23-collapse-performance](../validation/2026-10-23-collapse-performance.md)，包含修复前后同场景测量及实际播放回归。

## 依据

- [Android rotationAnimation 与 SEAMLESS](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#ROTATION_ANIMATION_SEAMLESS)
- [Android JUMPCUT](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#ROTATION_ANIMATION_JUMPCUT)
- [WindowInsets 对应窗口尺寸](https://developer.android.com/reference/android/view/WindowInsets#getFrame())
- [Display rotation 的顺时针定义](https://developer.android.com/reference/android/view/Display#getRotation())
- [AOSP seamless 资格与方向策略](https://android.googlesource.com/platform/frameworks/base/+/99b01a65cc4c104933788b3143285ab6bae65827/services/core/java/com/android/server/wm/DisplayRotation.java#659)
- [AndroidX 固定方向映射实现](https://android.googlesource.com/platform/frameworks/support/+/a72ba200ed75af8859baa4b805e76573de8a2660/core/core/src/main/java/androidx/core/view/ScreenOrientation.java#87)
- [Android PiP 与自动进入的时序](https://developer.android.com/develop/ui/views/picture-in-picture)
