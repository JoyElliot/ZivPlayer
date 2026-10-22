# ADR 0023：自定义旋转与真实系统方向交接

- 状态：已实现；追加 Xiaomi 真机交接修复，验证记录见文末
- 日期：2026-10-23
- 范围：`home-feishu-style` 播放器展示、Activity 方向与 PiP 协调

## 背景

ADR 0021 通过锁定 Activity 方向、在应用内旋转视频和控件来维持连续展示。用户要求动画完成后最终改变真实系统方向，尽量避免二次旋转、闪屏和播放中断。本决定替换 ADR 0021 的内部旋转方向策略，保留唯一播放器、唯一视频输出、双底栏外观和其他播放行为。

## 决定

- 展开时记录进入播放器的显示 rotation 和窗口横竖方向。旋转按钮在这个基准上转动 90°；反向按钮、返回和收起恢复进入时的方向，支持从横屏、反向竖屏进入。
- `PlayerWindowCanvas` 只包裹持久视频及播放器控件，首页继续使用真实窗口的布局与 Insets。内部先完成弹簧动画，再经过一次绘制机会请求固定系统 orientation；这不是 SurfaceFlinger 已显示该帧的证明。
- 系统接管时，在同一测量路径中交换逻辑画布宽高，并把外层角度设为显示 rotation 差值的相反数。内部角度不重新执行一次归零动画；合成角度在正常交接端点为零。改变基准、窗口尺寸和 PiP 不改变 `PlayerVideoSurface` 调用位置或 View key。
- 对 rotation 早于尺寸、尺寸早于 rotation 的交付顺序，保留已接纳方向及逻辑尺寸，等待窗口轴向一致。分屏/自由窗口允许独立 resize。该策略不代表 configuration、View 测量与系统合成在公开 API 上具有原子性。
- 自定义动画已经完成旋转，因此使用 `ROTATION_ANIMATION_JUMPCUT`，并保留不透明全屏窗口条件。此前的 `SEAMLESS` 在 Xiaomi 上因导航栏换边被拒绝，回退成约 423 ms 的第二次系统旋转；不能把它当作强制无动画的开关。固定方向映射考虑自然横屏与 `config_reverseDefaultRotation`，资源不可访问时采用平台默认策略。不调用隐藏 Binder，不使用截图占位或另一个播放器。
- 新旋转取消旧动画；后台取消尚未提交的动画，回到前台继续最新意图。配置恢复保存方向基准。1.5 秒检查只用于识别系统未满足方向请求、允许正常退出，不用于安排动画交接或判断真实帧已经显示。

## 迷你条、控件与画中画

- 迷你视频槽位从实际 root 坐标转换到播放器逻辑画布；迷你端额外抵消外层旋转，保持视频方向和宽高比。两种底栏共用这一处理。
- 按钮返回及真实横屏中的下滑收起先恢复方向，再开始缩小到迷你条，避免让横屏首页在收起中突然重新排成竖屏。基准方向中的下拉及迷你条上滑继续跟随手指。向上手势不会误触收起；向上后反向下滑仍可交接给方向恢复。
- 控件、队列、音频占位和状态提示共用进入播放器时的逻辑安全区，按应用内动画角度映射。第一次旋转开始后保持该基准直到展示会话或逻辑尺寸改变，避免系统 Insets 与画布轴向分别更新时短暂把左边距映射到底边。恢复横屏时从已分发的 `rootWindowInsets` 初始化；API 35 起还检查 Insets 对应窗口尺寸，未就绪的快照只用于临时绘制，不永久锁定。顶部拖动条位于同一个补偿画布与旋转容器内。
- PiP 源矩形采用包含祖先图层变换的真实 window 坐标，再裁剪到可见窗口，避免 FILL 的屏外区域参与系统缩放。
- 应用内 PiP 按钮先把视频对齐当前真实窗口，再调用系统进入。自动 PiP 仅在最终目标和画面方向都已对齐时启用。
- 若旋转途中按 Home，系统离开不能等待后台的 Compose 帧时钟：在 `onUserLeaveHint` 内及时提交 PiP，并立即将视频方向对齐当前窗口。这个打断路径优先保证 PiP 进入与画面方向；不承诺与应用内按钮一样完整播放对齐动画。PiP 内隐藏的旋转状态更新到最终意图，退出时不复活旧的中间角度。

## 验证边界

JVM 测试检查四方向映射、自然横屏、反向策略、两种回调顺序、分屏尺寸、FIT/FILL/STRETCH 端点、迷你方向及 PiP 对齐条件。安全区回归检查两种 Insets/窗口更新顺序、反向旋转、横屏恢复、初始化未就绪和合法全零安全区。Compose 用例覆盖快速反向取消、系统接管不重播动画、PiP 打断、后台恢复和下滑接管；真实播放用例等待真实方向变化，再检查同一 TextureView/SurfaceTexture。

初次实现仅按当时授权做主机检查和静默安装，保留原始[验证记录](../validation/2026-10-23-system-rotation-handoff.md)。用户反馈二次旋转、随后反馈 UI 跳位并授权操控手机后，追加设备日志与录屏诊断，见[后续修复记录](../validation/2026-10-23-double-rotation-fix.md)。不把单机结果推广为所有 OEM、所有帧的零冻结或零闪屏保证；录屏投影自身也可能比物理显示晚一帧，必须结合窗口日志判断。

## 依据

- [Android rotationAnimation 与 SEAMLESS](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#ROTATION_ANIMATION_SEAMLESS)
- [Android JUMPCUT](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#ROTATION_ANIMATION_JUMPCUT)
- [WindowInsets 对应窗口尺寸](https://developer.android.com/reference/android/view/WindowInsets#getFrame())
- [Display rotation 的顺时针定义](https://developer.android.com/reference/android/view/Display#getRotation())
- [AOSP seamless 资格与方向策略](https://android.googlesource.com/platform/frameworks/base/+/99b01a65cc4c104933788b3143285ab6bae65827/services/core/java/com/android/server/wm/DisplayRotation.java#659)
- [AndroidX 固定方向映射实现](https://android.googlesource.com/platform/frameworks/support/+/a72ba200ed75af8859baa4b805e76573de8a2660/core/core/src/main/java/androidx/core/view/ScreenOrientation.java#87)
- [Android PiP 与自动进入的时序](https://developer.android.com/develop/ui/views/picture-in-picture)
