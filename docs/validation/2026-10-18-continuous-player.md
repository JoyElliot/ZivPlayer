# 2026-10-18：连续视频播放器与现代控件验证

> 后续已完成 [真机功能验收](2026-10-18-continuous-player-device.md)，并按用户要求保留新版安装；以下保留当时的主机阶段记录。

## 工作边界

仅修改 `F:/Git/ZivPlayer-home-feishu-style` 的 `home-feishu-style` 分支。基线为 `***`，先保存工作区补丁、文件快照和散列，再将此前未提交的依赖升级按模块提交。没有推送远端。

本轮没有访问实验工作区内容，没有连接、安装、测试或调整手机，也没有向正在进行功耗测试的线程发送消息或干预其进程。历史依赖升级记录中的真机结果属于此前工作，不能视为这次交互重写的设备验收。

本代码线仍使用 libmpv 播放；Media3 是会话接口，不是新加入的 Exo 播放器。三核心实验代码没有合入。

## 实现结果

- 视频保留一个 TextureView 展示入口，迷你条实时播放。展开、收起、上滑跟手和横屏使用同一几何模型；弹簧可被新手势接管。窗口缩放期间持续刷新迷你终点。
- 播放器控制、菜单和队列在同一旋转坐标内；完整控制层在动画期间不接收触摸。系统 PiP 接收视频源矩形，并区分进入中的窗口交接与普通后台暂停。
- 进度拖动实时发送关键帧预览，松手精确定位；合并过期目标，防止请求堆积。普通点击直接精确定位。位置变化不重建整个队列和轨道列表。
- 控制系统媒体音量；可取得可靠设备曲线时在粗档位内做有界衰减补间，系统拒绝或路由变化时回到实际档位。硬件键仍交给系统处理。
- 主要导航、卡片、按钮、开关、滑条和播放器面板改为扁平样式。播放/暂停加入可反转的旋转形变，保留焦点、触摸区域和进度语义。

设计依据、生命周期和能力边界详见 [ADR 0021](../adr/0021-continuous-player-presentation.md)。

## 主机验证

环境：Windows、JDK 21.0.11、Android SDK `D:/DevTools/Android/Sdk`，复用既有 Gradle 缓存；构建输出与原生 staging 均位于当前测试工作树。

最终命令未带 `--write-locks` 或写验证元数据参数，严格使用已提交依赖锁和校验值：

```powershell
$env:JAVA_HOME='C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot'
$env:ANDROID_HOME='D:\DevTools\Android\Sdk'
$env:GRADLE_USER_HOME='D:\DevTools\BuildCache\Gradle\user-home'
.\gradlew.bat :core:model:test :core:media-api:test :core:player-api:test :core:player-runtime:test :data:media-android:testDebugUnitTest :platform:libmpv-android:testDebugUnitTest :platform:playback-android:testDebugUnitTest :feature:library:testDebugUnitTest :feature:player:testDebugUnitTest :feature:settings:testDebugUnitTest :apps:android:testDebugUnitTest :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest :apps:android:lintDebug --offline --no-daemon --console=plain
python tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk --report build/overnight-modernization/apk-audit.json
```

最终构建成功，52 秒、402 个任务（98 执行、18 使用缓存、286 已是最新）。复用缓存不等于每项测试重新执行；下面是本次任务解析并确认的测试结果。225 项测试无失败、错误或跳过。

| 模块 | 测试数 |
| --- | ---: |
| `core/model` | 3 |
| `core/media-api` | 9 |
| `core/player-api` | 6 |
| `core/player-runtime` | 34 |
| `data/media-android` | 20 |
| `platform/libmpv-android` | 72 |
| `platform/playback-android` | 50 |
| `feature/library` | 2 |
| `feature/player` | 11 |
| `feature/settings` | 0 |
| `apps/android` | 18 |

`feature/settings` 无测试源码。新增主机用例涉及 1000 次 seek 风暴合并、同目标精度升级、播放意图、控制器重置、音量曲线边界和几何连续性。

Lint 为 **0 error、8 warning**：PiP 静态检查 1 项、既有 Modifier 默认值 1 项、资源目录版本限定 2 项、toUri 建议 4 项。PiP 参数中已经有条件配置的 auto-enter 与 source rectangle，仍记录 Lint 的原始警告，没有用 suppression 隐藏它。构建过程曾暴露受限的 `dispatchKeyEvent` 及新面板 Activity 依赖在 AndroidTest Lint 配置中的锁文件缺项，均修正后才执行上面的最终严格检查。

APK 检查确认：20 个受审计原生库、4 个锁定 AndroidX helper、16 个 DEX JNI 方法、ZIP/ELF 16 KiB 对齐及库字节一致性。运行时 ART 注册与视频输出仍需设备验证，原生收据 `releaseInput=false`，产物是 debug APK。

新增 `PlayerPresentationContinuityTest` 的 4 个 Compose 用例已经编译进测试 APK，**没有运行**：对象保留用例只有 TextureView 对象身份，不证明真实视频帧连续，也不能替代系统 PiP 往返。

## 产物

代码对应提交 `***`；后续仅补充本记录与 ADR。

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `ZivPlayer-modern-player-20261018.apk` | 292559972 | `5f3a471732b161d9edd626d83530c1070d99c7872387350c2000813bc1befab3` |
| `ZivPlayer-modern-player-tests-20261018.apk` | 3083243 | `6c0ba171da5f052e88f05dfcabbe7ba425bb603aa823e7cb74fbd9868968f8eb` |

主包与测试包保存在当前工作树的忽略目录 `build/overnight-modernization/`。同目录包含 `artifacts.json`、`apk-audit.json`、`test-summary.json`、`commit-audit.json`、最终构建日志 `final-strict-validation-2.log` 及最初工作区快照。这些本机证据和二进制不纳入 Git。

## 提交索引

按新增加删除的文本行数计数，每次都不超过 1000 行；Wrapper JAR 是二进制，单独按文件记录，不伪装为文本行。前 12 次提交整理既有依赖升级，后续为本轮实现与回归。最终文档提交也执行同样的行数检查。

| 提交 | 变更行数 | 内容 |
| --- | ---: | --- |
| `***` | 545 | build(verification): 固定Kotlin升级依赖校验值 |
| `***` | 546 | build(verification): 固定Compose 与 MIUIX升级依赖校验值 |
| `***` | 362 | build(verification): 固定Android 构建工具升级依赖校验值 |
| `***` | 455 | build(verification): 固定AndroidX 与辅助工具升级依赖校验值 |
| `***` | 630 | build(deps): 升级 Gradle Kotlin 与统一组件版本 |
| `***` | 818 | build(android): 更新应用与播放平台依赖锁 |
| `***` | 427 | build(core): 更新核心与媒体数据依赖锁 |
| `***` | 778 | build(player): 更新媒体库与播放器界面依赖锁 |
| `***` | 778 | build(ui): 更新设置与设计系统依赖锁 |
| `***` | 260 | build(native): 更新 mpv 原生闭包与执行策略 |
| `***` | 76 | feat(libmpv): 默认启用 gpu-next 并记录渲染诊断 |
| `***` | 118 | docs(validation): 记录依赖升级与离线验证结果 |
| `***` | 128 | feat(player): 区分快速拖动预览与最终精确定位 |
| `***` | 206 | feat(player): 实时预览拖动目标并限制请求积压 |
| `***` | 213 | feat(audio): 系统媒体音量与有界细分插值 |
| `***` | 11 | fix(audio): 使用公开按键回调并保持细分音量单调 |
| `***` | 27 | perf(player): 避免拖动时重复重建队列和轨道列表 |
| `***` | 318 | feat(ui): 统一扁平控件与可中断的播放状态动画 |
| `***` | 258 | feat(player): 建立持久视频层与可接管的展示动画 |
| `***` | 174 | feat(player): 将菜单和队列移入可旋转的窗口控制层 |
| `***` | 229 | feat(player): 连续交接迷你播放横屏与系统画中画 |
| `***` | 120 | test(player): 覆盖视频层保留动画打断和预览回拖 |

## 尚待设备验收

本轮没有可据以声称“零掉帧”“秒切”或“20 fps 以上”的设备测量。预览速度仍受 GOP、解码器与读取路径影响；系统 PiP 动画和大屏方向策略也由系统参与。此次实现去除了应用主动迁移 Surface 和旧请求排队的来源，实际观感需要后续专用空闲设备确认。

设备空闲后依次检查：连续播放中往返迷你/完整窗口、上滑反向打断、横屏菜单与安全区、PiP 进入/返回/关闭、分屏 resize 后收起、暂停状态定位、多种编码与长 GOP 快速拖动、系统安全音量/蓝牙切换/硬件按键、TalkBack 与键盘焦点。原夜间功耗实验的 APK 和环境继续保持独立，本记录不生成或覆盖任何功耗结论。
