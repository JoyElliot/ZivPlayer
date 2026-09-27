# 2026-09-27 层级转场、预测返回与设置页验证

工作分支 `home-feishu-style`，初始提交 `***`。参考项目为 `fang-feishu-app-class2-group1` 的 `8676095d`。使用独立预览包 `io.github.joyelliot.zivplayer.homepreview`，临时测试放在忽略目录 `build/home-review/tests`，不纳入产品提交。

## 第一项：公共层级容器

- `:ui:design-system-miuix:compileDebugKotlin` 离线通过，日志 `build/home-review/layer-compile.log`。
- 预览应用/测试 APK 构建、安装通过。Android 15 真机 `layer-device-final.log`：`OK (5 tests)`，12.6 秒，覆盖中间帧前景右移/父页缩小、两层保活及退场后销毁、保存后恢复多层与已提交返回、入场同帧返回、窗口圆角一致性。
- 首轮几何断言读到了图形变换外侧的测试标签，因此仍是未变换边界；将标签置于图形层内侧后测到实际形状，产品变换参数未改变。独立审查修正了“已知 Insets 中缺失角”应为直角的问题，并增加仅 Insets 变化时的刷新。完整分屏/外接显示器仍未设备验证。
- 此提交提供公共组件；业务设置接线在第三个提交完成。测试用例只存在于忽略目录，尚不代表完整设置导航或系统预测返回通过。
