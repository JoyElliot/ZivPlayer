# 2026-10-23：二次旋转与控件跳位修复

## 结果与范围

分支 `home-feishu-style`。用户先反馈真实系统转屏会旋转两次，授权必要时操控手机；第一次修复后反馈短暂闪烁，并明确是 UI 跳位、视频似乎没有闪烁。本轮定位并修复两个独立问题，安装 `0.1.0-dev.rotation4` / versionCode `5`。用户随后手测确认：“现在确实不闪了”。

仍然先完成应用内旋转，再切换真实系统方向。未修改实验工作树、依赖、三核心参数或底栏外观。未推送远端、未卸载或清除应用数据、未回装旧版本。初次交接实现的历史验证保留在 [2026-10-23-system-rotation-handoff.md](2026-10-23-system-rotation-handoff.md)，方案更新见 [ADR 0023](../adr/0023-system-rotation-handoff.md)。

## 原因与修复

### 系统额外旋转

Xiaomi `23049RAD8C` / `marble`、Android 15（API 35），序列号 `***`。旧方案请求 `ROTATION_ANIMATION_SEAMLESS`，但设备 WindowManager 记录 `nav bar changes sides, so not seamless.`，回退成约 423 ms 的系统旋转动画。应用自定义动画已经完成，因此视觉上旋转了两次；原始日志只有一次有效方向请求，Activity 与窗口未重建。

改为 `ROTATION_ANIMATION_JUMPCUT`，保留不透明全屏窗口条件。最终正向、反向录制分别只有一条有效的系统方向变化，未再出现旧日志中的 `ScreenRotationAnimationImpl`。MIUI 通用的 `onRotationAnimationStart/End` 回调仍存在，不能仅凭回调名称判断二次旋转。

### UI 安全区提前更新

探针构建的同一次正向转屏记录：

| 时间 | 状态 |
| --- | --- |
| 00:42:46.793 | Insets `[0,111,0,0]`，映射 turn `1` |
| 00:42:47.538 | Insets 已为 `[111,0,0,0]`，映射仍是 turn `1` |
| 00:42:47.579 | 新窗口 `2400×1080` 与画布补偿被接纳 |
| 00:42:47.604 | Insets 映射更新为 turn `0` |

安全边距由上方移到左方，早于画布方向更新；旧映射短暂把新左边距映射成下边距，控件随之跳动。顶部拖动条还位于补偿画布外，系统接管时会换到另一条物理边。

修复为：在展开播放器的逻辑坐标中保存入口安全区，第一次旋转后保持到本次展示结束或真实逻辑尺寸改变，只随应用内旋转角度映射。系统 Insets 与窗口轴向的交付先后不再改变控制区位置。顶部拖动条也进入同一个补偿画布与旋转容器。

横屏恢复时不能锁住 Compose 尚未初始化的全零 Insets：优先用已分发的 `rootWindowInsets`，API 35 起检查其 frame 与当前窗口尺寸是否对应；未就绪的快照只临时绘制，后续可以补正。已就绪且确实全零的安全区仍合法。临时探针日志已从最终源码移除。

## 主机验证

Windows JDK 21、SDK 37、既有缓存，离线运行：

```powershell
.\gradlew.bat :apps:android:testDebugUnitTest :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest :apps:android:lintDebug --offline --no-daemon --console=plain
```

- `final-validation.log`：成功，43 秒，366 项任务，18 项执行。
- app JVM 测试 **38 项全部通过**，失败、错误、跳过均为 0；本轮新增 9 项安全区测试。
- Lint **0 error、0 fatal、9 warning**，未新增警告。
- APK 审计通过：20 个原生库、4 个锁定 AndroidX helper、16 个 DEX JNI 方法及 16 KiB ZIP 对齐。
- 签名验证通过，证书 SHA-256 保持 `3a27207ed1817859b019edfa3226aa2ac84d8ab0961b8c3a3681a940c75d9cc7`。
- 独立只读审查复核了 Insets 初始化、往返交接和 PiP 尺寸变化路径，未发现阻断问题。

## 设备验证

最终主包及测试 APK 安装后执行：

```bash
adb -s <device> shell am instrument -w -r \
  -e continuousDeviceValidation true \
  -e class io.github.joyelliot.zivplayer.PlayerRotationHandoffTest,io.github.joyelliot.zivplayer.ContinuousPlaybackDeviceTest \
  io.github.joyelliot.zivplayer.test/androidx.test.runner.AndroidJUnitRunner
```

`final-instrumentation.txt`：**OK (8 tests)**，49.883 秒。7 项动画/手势合约测试与 1 项真实播放测试全部通过，没有跳过。早先一次测试被 Xiaomi 后台 Activity 启动限制挡住，随后由操作方强制停止；修复测试宿主的前台启动规则后，最终测试正常完成。该早期人为停止不作为应用崩溃证据。

真实播放测试覆盖实际方向往返后同一 `TextureView` / `SurfaceTexture`、迷你条拖动/展开、手动及自动 PiP、暂停拖动预览、系统音量及 Activity 恢复。`continuous-device-result.json` 记录：

- 交接期间 `playingStateInterruptionsDuringHandoffs = 0`。
- mini、expanded、收起后 mini、PiP、自动 PiP 各采样到 5 个不同帧时间戳与像素 hash。
- 暂停拖动首个呈现预览 191 ms，释放到最终位置 158 ms；这是当前素材/核心的单次回归观测，不是三核心性能排名。
- 本次使用 libmpv；没有重跑三核心功耗实验。

最终 `final-forward` / `final-reverse` 录屏及窗口日志确认系统方向 `0→1→0`，Activity `330f9fb`、窗口 `555010e`、实例 `MainActivity@c61bbd0` 保持一致。控件可见的正向交接逐帧归一化到 `2400×1080` 后，20 个可比较采样帧的进度条蓝色像素中位 Y 均为 `849`，顶部拖动条同步转动，没有旧安全区跳位。反向录屏也检查了控件回到入口位置。

录屏固定为 `1080×2400`，其虚拟显示投影比物理显示更新晚约一帧；其中一帧出现不完整裁剪，不能拿它单独判断物理屏幕闪烁，也未将其纳入控件坐标比较。这是录屏证据的局限，不是全部物理显示帧无异常的证明。用户随后独立手测确认不再闪烁。

测试前停止目标应用并备份当前 `files/datastore` 与 `databases`；最终 instrumentation 完成后恢复了这份即时备份，再进行手动转屏录制。偏好和系统媒体音量由设备测试 `finally` 恢复。未用更早快照覆盖用户后续操作。发现用户已手动接管后停止手机操作；最终保持新版安装及用户当前界面。横屏顶部拖动条的完整实机下拉路径未另行完成；其位置由录屏、旋转映射及现有拖动测试核验。

## 构建身份与提交

- 包名：`io.github.joyelliot.zivplayer`
- 版本：`0.1.0-dev.rotation4` / `5`
- 冻结产物：`build/double-rotation-20261023/ZivPlayer-rotation4-20261023.apk`
- 大小：`293293303` 字节
- SHA-256：`734baeb4894023b5ddd94e1ddfd31ef3c3ad4cca04277bbf9a7e71510b3e5b71`
- 源码提交：`***`，之后仅补文档；安装后设备 `base.apk` hash 与冻结 APK 相同。
- 安装时间：2026-10-23 00:56:18（设备时间，Asia/Taipei），覆盖安装返回 `Success`。

| 提交 | 内容 | 增删行数 |
| --- | --- | ---: |
| `***` | 系统转屏改用 JUMPCUT，避免第二次旋转 | 6 |
| `***` | 控件安全区、顶部拖动条及初始化回归测试 | 173 |
| `***` | 测试宿主前台启动规则 | 5 |
| `***` | 新版构建标识 | 4 |

全部原始日志、录屏、探针证据、备份、坐标分析、APK 与安装记录均位于忽略目录 `build/double-rotation-20261023/`；临时文件不进入 Git。当前手机结果不能推广为所有 OEM、所有 Android 版本的零冻结、零闪屏保证。
