# ADR 0022：可选底栏外观与共享视频交接

- 状态：已接受
- 日期：2026-10-18
- 范围：`home-feishu-style` 的底栏、外观偏好和视频缩略区域

## 决定

恢复 [ADR 0019](0019-feishu-home-and-glass-navigation.md) 的液态玻璃导航和上方半包围播放卡片为默认方案。以 `***` 定型的卡片尺寸、折射、开放描边、顶部进度和共同弹簧变换为依据；保留 [ADR 0021](0021-continuous-player-presentation.md) 的扁平底栏，作为“简洁”选项。其他页面、播放控件、导航结构和播放能力沿用当前实现。

入口为“设置 → 播放与外观 → 底栏样式”。独立的 `NavigationStyle` 偏好持久化为 `navigation-style`；缺失、未知或错误类型的值均回退到液态玻璃。升级无需清空偏好、迁移数据库或重置播放选项。恢复默认也选择液态玻璃。

两种底栏共用 `MiniPlayer` 内容，视频槽位只报告几何，不创建视频视图。`PersistentVideoStage` 继续保持在底栏与设置页面之外的唯一调用点，展开、收起、跟手、旋转和 PiP 沿用同一 Surface 的交接路径。玻璃槽位恢复为 56 × 32 dp / 12 dp 圆角，简洁保留 56 × 36 dp / 8 dp 圆角。

槽位通过 `onLayoutRectChanged(0, 0)` 接收祖先图层缩放、平移后的根坐标矩形；取消默认 debounce，避免视频位置只在卡片布局变化时更新。玻璃卡片与导航使用不受系统栏显隐影响的底部间距，配合宿主已保留的系统 Insets，保持展开和收起终点稳定。相关 API 和变换处理参见 [AndroidX 回调实现](https://github.com/androidx/androidx/blob/androidx-main/compose/ui/ui/src/commonMain/kotlin/androidx/compose/ui/layout/OnLayoutRectChangedModifier.kt)及 [矩形变换实现](https://github.com/androidx/androidx/blob/androidx-main/compose/ui/ui/src/commonMain/kotlin/androidx/compose/ui/spatial/RectManager.kt)。

仅液态玻璃方案捕获页面背景；玻璃层及持久视频层均不进入被采样的背景，避免递归捕获。简洁方案不安装背景捕获 modifier。

## 验证边界

本轮按用户要求只做主机测试、编译和静态检查，随后覆盖安装新版，不启动应用、不操作播放、不执行 instrumentation。新增 Compose 用例用于后续验证两种底栏切换时的 TextureView 保留，以及祖先图层只改变位移/缩放时的视频槽位更新；编译通过不代表已在设备上执行。历史的 16 项设备结果属于此前版本，不能作为本次玻璃方案的真机验收。

具体构建、测试和安装身份见 [验证记录](../validation/2026-10-18-dock-styles.md)。
