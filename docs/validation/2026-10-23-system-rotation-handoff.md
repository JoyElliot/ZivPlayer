# 2026-10-23：真实系统方向交接

## 修改

分支 `home-feishu-style`。旋转按钮现在先执行自定义动画，再请求真实系统方向；窗口接管时通过稳定逻辑画布与反向补偿保留视频和控件位置，不追加归零旋转动画。返回和横屏下滑先恢复进入时方向，再收起到原底栏。液态玻璃与简洁底栏设置保持不变。

协调了 PiP 按钮、自动进入、旋转中按 Home、后台取消和恢复，裁剪 FILL 模式的 PiP 源矩形。完整方案及平台边界见 [ADR 0023](../adr/0023-system-rotation-handoff.md)。未修改实验分支、依赖、播放核心和原生参数。

## 主机检查

Windows JDK 21、SDK 37、既有 Gradle 缓存，严格离线运行：

```powershell
.\gradlew.bat :core:model:test :data:media-android:testDebugUnitTest :apps:android:testDebugUnitTest :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest :apps:android:lintDebug --offline --no-daemon --console=plain
```

本次从 WSL 调用 Windows `cmd.exe /d /c` 执行 wrapper。最终检查为 `validation-final-2.log`，成功，374 项任务，15 项执行、359 项已最新。测试总计 **54 项**：model 3、data 22、app 29，失败/错误/跳过均为 0。app 新增 11 项几何与方向策略测试。

Lint **0 error、0 fatal、9 warning**。其中 1 项新增提示来自查询系统 `config_reverseDefaultRotation` 资源；这是兼容设备默认方向映射的有意使用，查询失败有默认回退。其余 8 项为原有提示。

新增 `PlayerRotationHandoffTest` 的 7 项 Compose 用例已编译进测试 APK，覆盖反向取消、无二次动画、PiP 中断、后台恢复、下滑接管和 Home 强制离开。**没有执行 instrumentation，也没有安装测试 APK。** 既有真实播放用例已由“Activity 方向不变”改为等待真实方向往返，并继续检查 TextureView/SurfaceTexture 身份。

APK 审计使用 WSL 路径显式指定缓存：

```bash
GRADLE_USER_HOME=/mnt/d/DevTools/BuildCache/Gradle/user-home python3 tools/verify-android-artifact.py apps/android/build/outputs/apk/debug/android-debug.apk --report build/system-rotation-20261022/apk-audit.json
```

20 个受审计原生库、4 个锁定 AndroidX helper、16 个 DEX JNI 方法与 16 KiB 对齐均通过。签名验证通过，仍使用既有 Android Debug 证书，SHA-256 为 `3a27207ed1817859b019edfa3226aa2ac84d8ab0961b8c3a3681a940c75d9cc7`；未修改密钥或原生收据的 `releaseInput=false`。

## 构建身份

- 包名：`io.github.joyelliot.zivplayer`
- 版本：`0.1.0-dev.rotation1` / versionCode `2`（设备安装前为 `0.1.0-dev` / `1`）
- 冻结产物：`build/system-rotation-20261022/ZivPlayer-system-rotation-20261023.apk`
- 大小：`292660280` 字节
- SHA-256：`962a902d822ec5a588065f89c15959d803b426d31a5ae75c6495ed66dde7d521`
- 构建源码：`***`；之后只补充文档和安装记录

源码提交按功能拆分，增加与删除行数合计均小于 1000：

| 提交 | 内容 | 文本行变动 |
| --- | --- | ---: |
| `***` | 方向映射、画布补偿与几何测试 | 275 |
| `***` | Activity 方向交接、收起/PiP 协调及交互测试 | 584 |
| `***` | 新构建版本标识 | 4 |

## 静默安装结果

2026-10-23 00:17（Asia/Taipei）完成安装及核对。设备为 Xiaomi `23049RAD8C` / `marble`，序列号 `***`。只执行 `adb install -r` 覆盖更新，返回 `Success`；安装后包版本为 `0.1.0-dev.rotation1` / `2`，设备 `base.apk` 的 SHA-256 与上述冻结产物完全相同。

没有卸载、清数据、回装旧 APK、安装测试 APK或启动应用。设备读取仅用于确认安装目标、安装前后版本和已安装 APK 身份；未做播放或 UI 调试。结构化记录为 `build/system-rotation-20261022/installation.json`。

## 验收边界

用户要求本轮不做真机调试，只静默安装新版。主机验证不能证明 OEM 系统转屏的实际冻结时间、动画观感或零丢帧。未启动应用、注入触摸或按键、调整设备设置、播放测试视频或回装旧版本；历史真机结果不作为这次方向交接的验收。

日志、APK 审计、签名、包元数据、测试汇总及安装结果保存在忽略目录 `build/system-rotation-20261022/`。代码提交留在本地，未推送远端。
