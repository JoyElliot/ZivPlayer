# 2026-09-27 层级转场、预测返回与设置页验证

工作分支 `home-feishu-style`，初始提交 `***`。参考项目为 `fang-feishu-app-class2-group1` 的 `8676095d`。使用独立预览包 `io.github.joyelliot.zivplayer.homepreview`，临时测试放在忽略目录 `build/home-review/tests`，不纳入产品提交。

## 第一项：公共层级容器

- `:ui:design-system-miuix:compileDebugKotlin` 离线通过，日志 `build/home-review/layer-compile.log`。
- 预览应用/测试 APK 构建、安装通过。Android 15 真机 `layer-device-final.log`：`OK (5 tests)`，12.6 秒，覆盖中间帧前景右移/父页缩小、两层保活及退场后销毁、保存后恢复多层与已提交返回、入场同帧返回、窗口圆角一致性。
- 首轮几何断言读到了图形变换外侧的测试标签，因此仍是未变换边界；将标签置于图形层内侧后测到实际形状，产品变换参数未改变。独立审查修正了“已知 Insets 中缺失角”应为直角的问题，并增加仅 Insets 变化时的刷新。完整分屏/外接显示器仍未设备验证。
- 此提交提供公共组件；业务设置接线在第三个提交完成。测试用例只存在于忽略目录，尚不代表完整设置导航或系统预测返回通过。

## 第二项：公共预测性返回

- 独立的 `ZivPredictiveBackHandler` 绑定同一页面栈。应用显式开启 Android 返回回调，设计系统使用既有 Activity Compose 1.13.0，并约束 Core 与应用的 1.19.0 基线一致；离线重新生成受影响的四个库模块锁。使用限定 AndroidX 坐标的 `--update-locks`，保留 Kotlin ABI 检查工具等无关版本。
- 预览构建/安装通过。以真实 Activity `OnBackPressedDispatcher` 注入开始、左右边缘进度、取消与完成；共 9 个独立用例（含第一项 5 项回归）。`predictive-device-final.log` 为 8 项通过、1 项临时恢复测试失败，20.644 秒；修正测试的恢复时钟后，剩余单项在 `predictive-restore-device.log` 通过，4.145 秒。
- 取消检查确认前景位置与父页缩放都恢复、路由未删除；完成检查确认从当下 0.4 覆盖进度续接退出且重复返回只弹一层；禁用处理器会恢复覆盖页并忽略该手势随后到达的完成事件。保存未完成手势后恢复为原页面。
- 临时 StateRestorationTester 在冻结帧时钟时没有真正拆除重建组合，最初读到旧 0.25 进度；开启时钟后再模拟恢复，并断言新旧状态对象不同，检查通过。禁用用例也补齐了旧手势完成，再发送新的普通返回，避免把旧完成误作第二次返回。两项修正都未改产品行为。
- 以上通过的是设备上的 AndroidX 系统返回分发链；真实屏幕边缘手势与最终设置层级接入在下一项检查。独立状态审查未发现确定的误弹栈或取消恢复缺陷；AndroidX 启用标志的同帧更新窗口仍遵循其平台时序，不宣称禁用前尚未重组的旧回调绝不运行。
