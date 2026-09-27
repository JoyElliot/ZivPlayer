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

## 第三项：设置分层与最终接入（9 月 28 日完成）

- 设置首页改为七个分类入口，字体管理位于字幕详情的下一层。独立对照 HEAD 核验了全部原设置项、范围、回调和资源操作，未发现迁移遗漏；配置持久化与播放引擎未改动。保留原导入忙碌保护及删除按钮的 `!busy` 条件；原 ViewModel 的删除操作自身不置 busy，本次未扩大为资源并发改造。
- 详情栈包裹整个首页与底栏，播放器/画中画沿用原“保留组合、停止放置”的结构。接线审查发现从仍在滑动的设置页打开详情可能使父页继续切走，已在打开分类时固定到设置分页，并增加对应回归。
- `settings-navigation-regression.log`：公共层级/预测返回 9 项，加首页横向翻页、独立筛选/滚动、播放器返回/画中画、诊断返回等 11 项，`OK (20 tests)`，53.709 秒。
- `settings-device-final.log`：新设置层级 11 项，`OK (11 tests)`，29.492 秒。覆盖所有分类、字幕→字体→逐层返回、父页滚动与节点身份保留、底栏隔离、预测取消/完成、保存后恢复、全屏返回优先级、诊断在预测时暂停及取消后恢复、翻页中打开分类、通用/视频设置回调、字体选择/导入忙碌、Shader 顺序/数量限制与资源删除按钮。
- 新设置测试首轮为 9 项通过、2 项诊断入口失败：`performScrollTo` 只滚入视口，入口中心仍被悬浮底栏挡住。临时测试改为测量底栏边界并继续滚动到其上方，再真实点击；产品代码未因这两项失败改动。
- `settings-standard-checks.log`：标准包 `assembleDebug`、应用/设置/设计系统 `lintDebug` 离线通过，30 秒。设置无 Lint 问题，应用 10 项和设计系统 1 项既有警告、0 错误。预览与测试 APK 最终构建也通过；测试源仍只在忽略目录。
- `tools/verify-android-artifact.py` 校验标准 APK 通过：20 个审计原生库、4 个锁定 AndroidX 辅助库、16 KiB ZIP 对齐及 16 个 DEX JNI 方法。报告 `settings-artifact-verification.json`，APK SHA-256 `e03a4489350cd520338d4f2fc0bbc7ac8f970940b295c29e5688e4abd1fcee3b`。
- Android 15 手机处于系统手势导航（`navigation_mode=2`）。在真实预览应用内分别从左、右屏幕边缘滑动返回，均拍到松手前前景卡片跟随、父页缩小/压暗的中间帧，松手后字体页仅返回字幕页，再通过顶部返回恢复设置首页与底栏。取消/恢复由上述设备上的 AndroidX 分发测试验证，未把普通返回键当作预测手势证据。
- 录屏：`build/home-review/artifacts/settings-layered-motion.mp4`，540×1200，17.53 秒；截图 `settings-os-left-middle.png`、`settings-os-right-middle.png`、`settings-os-left-returned.png`、`settings-os-root.png`。浅色布局和三级预测截图在 `artifacts/settings/`；最终 APK 为 `artifacts/ZivPlayer-home-preview.apk`（已安装独立包）和 `artifacts/ZivPlayer-debug.apk`（标准包）。
- 真机临时开启的充电常亮已恢复原值 `stay_on_while_plugged_in=***`。完整分屏/外接显示器、实体旋转后圆角变化与外部文件选择器跨进程恢复未做真机覆盖；没有将这些边界计作已通过。
