# 2026-09-26 首页玻璃导航与媒体拆页验证

## 范围和环境

- 基线：`origin/main` 的 `***`；独立分支 `home-feishu-style`。
- 改动：仿飞书的页面标题、搜索入口、圆角列表、实时玻璃底栏与迷你播放器；视频、音频、最近播放、设置四个一级页面。
- Windows / JDK 21.0.11 / 仓库锁定的 Gradle、Android SDK 与依赖。应用最低版本调整到 API 33，决定见 [ADR 0019](../adr/0019-feishu-home-and-glass-navigation.md)。
- 真机：23049RAD8C，Android 15 / API 35，通过用户提供的无线 ADB 连接。
- 设备测试安装独立包 `io.github.joyelliot.zivplayer.homepreview`，避免覆盖既有应用、数据库及 SAF 授权。媒体列表和播放状态使用内存样例，没有读取用户媒体。

## 首轮主机检查

以下任务通过，执行参数包含 `--offline --max-workers=2 -Pkotlin.incremental=false`：

```text
:apps:android:assembleDebug
:apps:android:assembleDebugAndroidTest
:apps:android:testDebugUnitTest
:feature:library:testDebugUnitTest
:feature:player:testDebugUnitTest
:feature:settings:testDebugUnitTest
:apps:android:lintDebug
:feature:library:lintDebug
:feature:player:lintDebug
:feature:settings:lintDebug
:ui:design-system-miuix:lintDebug
```

- 单元测试：应用 10、媒体库 2、播放器 11，合计 23 项通过，无失败或跳过；设置模块没有单元测试用例。
- Lint：上述五个模块均无错误；应用 10 条 warning、播放器 3 条、设计系统 1 条，设置另有 1 条 hint。包含提高最低版本后的冗余 API 分支提示，没有把 warning 当作零。
- 预览测试源最初位于另一磁盘，切回标准测试编译时遇到 Kotlin 增量缓存关闭失败。临时源码移到当前 worktree 的忽略目录，并禁用此次构建的 Kotlin 增量缓存后，上述任务完整通过；项目编译配置未为此修改。
- 标准 APK 执行 `python -B tools/verify-android-artifact.py <apk>` 通过：20 个经来源收据核验的原生库、4 个锁定的 AndroidX 辅助库、16 个 DEX JNI 方法、16 KiB ZIP 对齐。
- 标准 debug APK SHA-256：`e6e428b16d31221244db1a47c30da0e9b4b40193ff6826deaeb7991a80ff776f`。使用已有 debug 原生输入，不是新的发布构建。

## 真机检查方法

首轮结果：`OK (17 tests)`，耗时 34.415 秒。预览 APK SHA-256：`68bed5af9a33ec129d5493831faae3d8c56cc41bcd7a2200efa3b5435859551d`。这轮只验证了静态布局与导航，不能证明点击、跟手和缩放交互已复刻；最终补验和新产物见下节。

临时 Gradle init script 仅覆盖预览 applicationId、测试 manifest 与测试源目录；临时 foreground rule 使用完整 Activity 类名启动独立包。脚本、样例和截图保存在忽略目录或系统临时目录，不加入产品提交。

原有 `LibraryHomeUiTest`、`PlayerUiContractTest`、`LaunchSmokeTest` 的 12 项契约检查，以及本轮 5 项临时回归检查覆盖：

- 视频和音频页的混合目录、搜索、收藏和播放全部均限制在当前类型；队列不混入另一种媒体。
- 视频和音频的筛选状态分开保存；从最近播放的迷你播放条进入播放器后返回原页；暂停按钮不触发页面跳转；设置入口可用。
- 浅色、深色与 1.5 倍字体下的真实背景采样、底栏和迷你播放条；列表最后一项可滚动到达并打开。
- 状态栏图标明暗与应用主题一致。截图检查发现深色主题图标对比不足后，已调整主题的系统栏外观同步，并加入临时设备断言。
- 原有隐藏文件、授权根保留、目录移除、超过 500 项单文件打开、字幕入口、迷你播放器按钮、最近记录和启动入口契约。

第一轮整组 17 项中，旧的搜索用例出现一次 `queue == null`。其等待谓词仅匹配搜索文字并等待旧行消失，可能把输入框内容当成已完成的结果；后台计算期间播放按钮仍禁用。原用例独立重跑通过。最终临时副本增加“播放全部已启用”的等待条件；产品代码继续禁止播放过期投影，仓库中的原测试未修改。

## 液态交互移植后的最终补验

用户指出首轮交互缺失后，底栏改为移植参考项目的实际动画与玻璃采样实现，参数和来源记录在 ADR 0019。按相同环境完成应用、测试 APK 构建、23 项 JVM 测试及相关模块 Lint；最终原生库、JNI、16 KiB 对齐核验继续通过。APK 内已确认包含 Apache-2.0 正文和来源通知。

- 最终标准 APK SHA-256：`e2faec385028b6b7096f8e7a25ef4187440d02b69e5a83641323b5f6eff7f608`。
- 最终共存预览 APK SHA-256：`728d1dad3d973746607c44399eb0f372f2fdec12692ab16ea0c94cc9cc6af12b`。
- 最终真机整组：`OK (24 tests)`，50.033 秒；包括首轮 17 项及 7 项液态交互检查。日志为忽略目录外的 `device-motion-final2.log`（日志文件本身被 Git 忽略）。
- 动态检查冻结 Compose 时钟采样点击中间帧，并分步注入 down / move / up / cancel：验证途中位移与放大、按住拖动时不提前切页、释放后缩回、取消后恢复原页、快速反向切换、RTL、外部导航不产生伪点击，以及按住时发生外部导航后松手不会覆盖新页面。
- 无障碍点击包含重复点击当前 Tab；底栏只有四个有作用的点击节点。初次临时计数断言误把未组合的 LazyColumn 行也计入，修正为只检查底栏子节点后整组通过。
- 1080 × 2400 真机的测量示例：静止胶囊约 221 × 154 px，按住稳定后约 319 × 223 px，松手恢复 221 × 154 px。拖动后的底边约 2351 px，未越出屏幕；修复前曾达到 2412 px，本轮已按参考预留扩张空间。
- 关键帧和操作录屏保存在 `build/home-review/artifacts/`。录屏使用真实生产导航组件和内存示例列表，展示连续点击、按住、往返拖动及释放；未使用用户媒体。动效脚本独立于上述 24 项断言用例。

## 验证边界

- 本轮覆盖首页界面、导航与内存媒体投影，不代表真实媒体解码、长时间播放、后台策略、画中画或功耗回归全部通过。
- 真机仅覆盖 Android 15 的 arm64 设备；API 33/34、平板、折叠屏、横屏和 x86_64 运行未验证。
- API 26–32 不再属于此开发应用的支持范围。构建与原生校验通过不等于其他 Android 版本已通过视觉验收。

## 队列与历史页面迭代

- 首页第三个入口改为队列，上方当前播放列表、下方播放历史；保留历史续播和移除记录操作，选择队列条目留在队列页。
- 独立预览包和测试 APK 构建通过；Android 15 真机 `OK (7 tests)`，覆盖两项临时队列检查与原有五项 PlayerUiContractTest。
- 历史最后一项的菜单检查先滚动到列表末尾，使按钮完整越过浮层；只判定行部分可见不足以保证其右侧按钮未被底栏覆盖。修正临时操作后整组通过。日志 `build/home-review/queue-device2.log`，临时测试未纳入提交。


## 紧凑叠接浮卡迭代（2026-09-27）

- 上方播放卡片收窄 32 dp、普通字号高 48 dp，与下方 64 dp 导航胶囊叠接 8 dp；普通字号总高约 104 dp。右侧仅保留播放/暂停。
- 进度绘制在上方卡片的顶部圆角描边内，不参与布局高度；播放区在叠接处绘制于导航上方，导航手势仍限定在原底栏。
- 预览构建及 Android 15 真机 `OK (20 tests)`：7 项液态动效、5 项首页外观/导航、3 项队列及浮卡几何、5 项原播放器契约。检查进度有无时高度一致、上下卡片大小及叠接关系、暂停不导航，浅色/深色/大字体截图。
- 日志 `build/home-review/compact-device.log`，临时测试未纳入提交。真实缩略图在下一点补充。
