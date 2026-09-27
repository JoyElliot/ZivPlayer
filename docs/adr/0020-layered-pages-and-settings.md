# ADR 0020：可复用层级页面与设置导航

- 状态：已接受
- 日期：2026-09-27
- 范围：设计系统的页面栈、系统预测性返回、设置界面

## 背景

用户要求沿用 FangFeishu 的页面层叠体验，将层级动画、预测性返回、设置页重写分别提交。参考固定为 `8676095d` 的正式 Android 实现：`FfPageStack.kt`、`StackNavTransitions.kt`、`StackDetailContainer.kt` 和 `DisplayUtils.kt`。

## 决定

- `ZivPageStackState` 管理与业务无关的字符串路由和稳定条目 ID，`ZivPageStack` 负责全窗口层叠。调用方把完整父页（包括底栏）放入 root，将详情作为 page。不引入新的播放器或数据层所有者。
- 采用参考项目的 320 ms 横向覆盖；父层缩放至 0.965、左移 8% 屏宽，每层独立的黑色遮罩最大 alpha 为 0.45。父层保持不透明，不让更深页面透过整个内容层。进入首帧同步设为屏外，退出结束才销毁页面，向下层返回不会重新创建其组合。
- 读取当前窗口四个物理圆角，布局或 Insets 变化时刷新；不覆盖 Compose 的 Insets 监听。尚未取得窗口 Insets 时参考 framework 圆角尺寸，完全缺失回退 28 dp；已取得 Insets 而某角为空时视为该角不与窗口相交，使用直角，避免分屏误用整块屏幕的圆角。与参考项目只处理左侧圆角相比，此处两侧均匹配当前窗口，适用于返回途中露出的卡片边缘。
- 覆盖页以下的内容不暴露可交互无障碍节点，并阻止穿透触摸；导航时清除输入焦点。过渡期间阻止重复 push，pop 可以打断尚未结束的入场；同一个退出只提交一次。保存状态保留每层路由、条目身份及内容状态，已提交但尚在退场的页不在恢复后复活。
- 共享状态和渲染组件先独立提交，系统返回适配随后单独接入，再让设置分类详情使用同一个完整窗口宿主。

## 来源与验证

层叠视觉结构取自 FangFeishu 的 Apache-2.0 实现，保留原声明与打包通知。平台圆角接口见 [Android rounded corners](https://developer.android.com/develop/ui/views/layout/insets/rounded-corners)。具体检查与边界记录在 [验证记录](../validation/2026-09-27-layered-settings.md)。
