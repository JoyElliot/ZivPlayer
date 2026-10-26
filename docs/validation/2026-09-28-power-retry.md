# 2026-09-28 功耗重试与常亮验证

用户今晚重新授权功耗验证，并特别要求先保证不会在测试中锁屏。9/26、9/27 均已安全收尾，旧会话保持原样。本轮使用全新的基线、输出目录、远端根与 guard，先验证重复性，通过完整独立审查后再执行原来未完成的有限软件清单。

## 今晚身份与常亮前置条件

- 工作目录 `F:\Git\ZivPlayer`；输出 `native/out/power-comparison-20260928`；远端 `/data/local/tmp/ziv-power-20260928`，阶段 `repeat28/software28`。
- 现有授权 ADB/mDNS 地址 `***`；硬件串号 `***`，marble/23049RAD8C。00:33 香港时间读回电量 93%、温度 31.1°C，物理放电、无外接供电、Awake、keyguard showing=false。
- 00:37–00:38 新鲜拉取当前已安装的主包和原测试包。主包 MD5 `b43ecf175a8db507a79664cd752cc732`，SHA-256 `0e017ceaf91a39fa71348e898ef06e0265ca31cd9a2ea44508e939f0f2962392`；原测试包 MD5 `b48f435ffaabf38d833786a11074bc1f`，SHA-256 `2f48a9b717e4e84ab1ee1c9f4949667f6fb9d3bc2a95a47a36029498062470fd`。设备哈希和拉取副本一致，未发现其他分支覆盖成不同二进制。
- 原设置：超时 ***ms、亮度 ***、模式 ***、插电常亮 ***、蓝牙 ***（状态 ***）、飞行模式 ***、Wi-Fi ***。不以旧夜备份作为恢复基线。
- 保存基线后将本轮系统超时设为 **86400000ms（24 小时）**。runner 先加载本轮 config.env，覆盖其 900000ms 默认值；正常重复阶段 hold 也使用本轮 86400000ms。
- 正式启动前必须通过 **至少 11 分钟的连续无输入闲置验证**，超过原来的 10 分钟超时。保留开始与结束的 dumpsys power/window policy；以设备实际 lastUserActivityTime 的年龄证明最终连续闲置 ≥660 秒。期间较早的活动刷新必须重新完整计时；休眠/唤醒时间必须始终未变，当前 Awake 且 showing=false、实际长超时仍有效。`screen-readiness.json` 绑定新基线、设备、时间与原文哈希；启动流程开始时再次核验，若这次读回相较合格证据出现新活动则拒绝。它证明截至读回时的闲置，不证明未来不会有输入；此后由设备端每轮门禁和逐点采样检查接管。
- 冷却阶段由设备端每最多 30 秒刷新 HOME 活动时间，并检查真实 Awake/未显示锁屏。冻结计量 APK 在播放窗口使用 `FLAG_KEEP_SCREEN_ON`，每秒检查 screenInteractive，稳态逐点检查焦点；任何不满足的结果不能纳入合格功耗。安全凭据、锁屏密码和系统安全策略不作变更。
- 上述是启动前实测及运行中保护，不把一次屏幕读回说成整晚已验证。正式测量的屏幕状态以结束后每轮完整 trace 为准。计量期间主机不额外查屏幕。

本轮计量测试 APK 仍为 SHA-256 `5a873752a394dd8b34baa4005f2a01fa65576e54e723148c0988c83c00be1d75`，只临时替换测试包。主 APK 不重新构建或安装。AVC/HEVC/AV1 三个既有 1080p24 素材经本轮独占暂存目录 ADB push 后，逐项核对临时与私有文件 SHA-256；保留 owned-paths-before，现有不同内容绝不覆盖。

## 测试与续测门槛

`repeat28` 为 18 轮 H.264 硬解，各引擎 n=6，固定三引擎六块 ABC、BCA、CAB、ACB、CBA、BAC。每轮至少冷却 90 秒、预热 90 秒、稳态 240 秒/241 点，开始温度 ≤36°C、运行 ≤40°C、物理放电。新夜保留原来全部协议、固定输入和预设 5% 门槛，不为通过而更改规则。

退出后独立复算稳态梯形积分，审查实际硬解、1920×1080、120Hz、完整 trace/时序、常亮/焦点/播放、网络事件与实际可获得的流量证据、温度/供电、电量层、APK/素材身份和六个完整三轮块。计算六个轮均值的样本 SD/CV/范围/前后半漂移及块内配对差；241 个相关采样点不算 241 次独立重复。

CV ≤5% 与前后半漂移绝对值 ≤5% 仅为操作性续测门槛。分别对照 9/26、9/27 同硬解均值及各自 SD、温度、电量、网络和输出路径，保留各夜分层。**±1SD 只作描述，不作为数据筛选、统计等效或准确度证明。** 不删除偏离值，不用硬解 SD 推断软件噪声，不合并历史夜均值。

9/27 的 18 轮播放/计量资格合格，但 VLC CV 7.20%、漂移 −7.31%，软件没有启动。首 VLC 有较多同期 Wi-Fi 报文，尚未证明因果。`networkStartBytes/networkEndBytes` 是 ip link 事件文件长度，不是收发流量字节；本轮不能据 0/0 声称网络安静。旧夜全部数据与失败标记保留。

只有控制器 `repeatability-review.json` continuationAllowed=true 且独立审查真实通过，才允许 `launch-software` 一次。`independent-review.json` 必须绑定 audit/review SHA，包含 historicalComparisonReviewed=true 和计划列出的完整 12 项布尔检查。任何失败须先分析现有证据并请求已核验身份的本轮 guard 恢复，确认实际退出与设置；不能无限 hold 或自动补跑整套硬解。

软件范围固定为 **3 个 AV1 短验证 + 40 正式轮**：remaining1 AVC Exo/mpv 的 ABBA+BAAB 共 8 轮；remaining2 AV1 Exo/mpv、AV1 mpv/VLC、HEVC Exo/mpv、AVC Exo/mpv 各 8 轮，共 32。该 40 轮逐项对应 9/26 ordinal 52–91：受观察干扰的 ordinal52 以新的完整块重测，不跨夜拼腿。不重测已合格 target24，不新增批次。9/27 软件未执行，不能把它误当已补完。

短验证每引擎 AV1 15+65 秒，不计正式功耗。首次正式剩余入组电量严格 >30%，入组一次锁定；之后任一轮前电量 <10% 不开始下一轮，并恢复熄屏。`REQUIRED_TARGET_SOFTWARE_ROWS=0` 指本轮没有新 target 阶段。VLC AV1 为间接软件分类；不安排固定 APK 无法充分分类的 VLC HEVC/H264。remaining1/remaining2 及各软件条件、电量层独立报告自身 SD、完整块和与 9/26 同条件的对照，保留失败、跳过和残缺记录。

## 执行与恢复

本轮控制器 `tools/power-retry-session.py`；不运行旧日期辅助脚本。紧急上限 **2026-09-29 01:00 香港时间**，正常完成或低电量更早恢复。无旧早晨取机截止，不沿用已退役 guard 的 9/28 01:00。

```powershell
python -B tools/power-retry-session.py prepare --output native/out/power-comparison-20260928 --serial *** --watchdog 2026-09-29T01:00:00+08:00
python -B tools/power-retry-session.py launch-repeatability --output native/out/power-comparison-20260928 --serial ***
# 仅真实退出后
python -B tools/collect-power-night.py --plan native/out/power-comparison-20260928/repeat28/run-plan.json --serial ***
python -B tools/power-retry-session.py review --output native/out/power-comparison-20260928
# 独立审查通过后仅一次
python -B tools/power-retry-session.py launch-software --output native/out/power-comparison-20260928 --serial ***
```

review/launch 共用本地 launch.lock，冻结后不改输入。有 launching_*、softwareLaunchAttemptUtc、远端目录或运行记录不得盲重试。若 guard 创建前的屏幕前置条件拒绝启动，此时尚无守护恢复：主机必须按新基线核对并修复本轮仍遗留的长超时，确认熄屏，不把 prepared 当运行或已恢复。

启动后遵守 session-state.nextExitCheckNotBeforeUtc。之前只查本地时间/状态；到点只发现已授权设备并最少读取一次 activeRemote/exit.json。无退出则本地记观察时间等下次，不查询进度、屏幕、样本或诊断。必要提前取证须 collection.request/collection.grant.json 轮外窗口并保留干扰边界。最早观察不是完成证明。

收尾以本轮新 baseline 恢复原测试 APK、超时、蓝牙、亮度、模式和正常熄屏，并核验主 APK、插电常亮 0，保留用户后来主动改动。比较本轮 before/after 的逻辑数据库全部表/行/列与偏好；不清空应用，不覆盖用户数据。确认恢复后写 night-guard/night-complete，核验 owner/PID/进程组/会话/starttime，再发 USR1 使 guard 以 already_completed 退出；若 guard 已按 host_requested/deadline 自行恢复退出，保留真实退出原因，不能伪造。

完整归档整个远端根、两个阶段 app 结果和恢复/guard 证据；本地哈希通过后按两份 manifest 精确 label、owned-paths-before 和素材哈希清理明确本轮自建文件，保留所有本地原始/失败档案。报告实际完成/有效/未执行数量和原因，最终 state=completed 并暂停原心跳。

## 当前状态

**本轮已停止续测并完成手机恢复。repeat28 于香港时间 01:02:15–03:12:42 完成 18 轮，17 轮通过原播放/计量资格，第 13 轮 VLC 缺少结束解码器身份。CV/漂移均在 5% 内，但资格门槛失败，40 轮剩余软件及 3 轮短验证均未执行。** 完整回传后的审查与收尾证据见文末；以下保留启动过程记录。

00:49 的首次前检未通过无输入门槛：手机持续 Awake、未锁屏，sleep/wake 时间戳均未变化，但活动时间在约 00:45 刷新，检查时真实连续闲置仅 259.306 秒。此证据保留在 `screen-idle-attempt1-review.json` 与原始文本，不伪称通过。未启动任何正式队列或 guard。

初版未执行的 prepared 清单完整归档到 `prepared-revision1`。启动前修订闲置证据判定，允许从较早的活动刷新重新等待完整 660 秒；门槛、功耗协议和正式清单均未降低或改变。修订后的 13 项流程测试和 9 项屏幕/证据边界测试通过，包含 659999ms 拒绝、660000ms 接受、陈旧/改写证据拒绝、合格读回后新输入或 sleep/wake 变化拒绝。新版本已重新冻结，23 项计划/文件/guard/工具 SHA 检查通过；未修改任何正在执行的输入。

00:56:28 香港时间真实读回通过：设备最后活动距今 **679.774 秒（11 分 19.774 秒）**，超时 ***，Awake、showing=false，mLastWakeTime=71779284、mLastSleepTime=71546140 与 00:38 原始快照相同；电量 91%、温度 32.0°C、物理放电。初次通过的完整证据保留在 [screen-proof-005628](../../native/out/power-comparison-20260928/screen-proof-005628/screen-readiness.json)。一次早于额外 5 秒取证余量的本地调用在发出 ADB 前被拒绝，不构成新设备观察。

01:01:53 启动前重新获取新鲜读回：连续闲置 **1004.669 秒（16 分 44.669 秒）**，仍 Awake、未锁定、24 小时超时、无新的 sleep/wake，电量 91%、温度 31.9°C。记录见 [screen-readiness.json](../../native/out/power-comparison-20260928/screen-readiness.json)，SHA-256 `dc2b9369a4d926990a984448b96f97a8117b8de3d5ef81c7a49e4574d7b49f2b`。控制器随后又核验实时 power/policy 并保存 `repeat28/screen-before-launch.json`，22 项离线测试与主线程最终核验均通过。

同次 policy 原文还确认 `screenState=SCREEN_STATE_ON`、`mIsScreenOn=true`、`mLockScreenTimeout=86400000`、`mLockScreenTimerActive=false`，不是只依赖 Awake 字段。独立静态复核最后未发现本次启动阻断；其指出的快照到后续启动之间的时间间隔、readiness UTC 并非原始 dump 内时间戳等一般边界保留：本次采集时刻由实际工具调用与新文件记录核实，原始证据未以改写 UTC 冒充新鲜，后续状态由 runner/采样检查接管。该代码复核不替代 18 轮结束后的独立统计与资格审查。

- guard owner `1eb9788780c44fd5a2763099b9bad3eb`，PID/进程组/会话 `32344`，starttime `9427519`；runner PID/进程组/会话 `451`，starttime `9428224`，启动身份均已核验。
- `started.json.screenTimeout=***` 和 bluetooth=*** 记录供收尾恢复的原设置；测试配置为 SCREEN_TIMEOUT_MS=86400000，不能把 started 的原设置字段误认成当前计量超时。
- 首次最早退出观察 **03:37:14 香港时间**，UTC `2026-09-27T19:37:14.974962+00:00`；此前不访问手机。不是预计完成保证。
- repeat28 计划 SHA-256 `d97cd3d99c169b86a9f01d60978dfe387ed5c6e05e7fb7ea8a6d356744eaedce`；software28 `1f2022fb759b1287504e10494d56c5426c0f49e57b2d80e0f589246095c43c25`；新控制器 `826fd62b3bd83fca92d3702818240a1022e7f9aaac925b99c0a5711075b94336`。
- 原心跳“核对今夜播放器功耗结果”已更新到本轮根、控制器、最早观察时间、9/29 紧急上限及新恢复基线，并恢复 ACTIVE。之后无额外手机查询；只在偏差审核并切阶段、完成、实质失败或需要用户操作时通知。

## 退出后的独立复核

03:44 首次到观察窗口后，通过 ADB/mDNS 找到原授权设备，只读 exit.json 确认已于 03:12:42 `manifest_completed`；随后才回传。没有在计量中回传数据或查询屏幕。18 轮全部完成、无跳过，无自动补跑；结束电量 81%，本次没有因低电量停止。

**防锁屏检查本轮通过。** 原始 18 条 trace 共 5958 点（每轮 90 个预热点、241 个稳态点），采样时 screenInteractive/isPlaying 均为 true；4338 个稳态点全部有窗口焦点、1920×1080、120.00001Hz。温度 32.0–33.2°C、电量 81–91%，持续物理放电、无外接电源，无热状态告警。稳态首尾积分跨度 240000–240002ms，稳态采样间隔 996–1004ms；raw 的 steadyActualMs 为 240064–240094ms，包含最后一点之后的函数开销，与积分跨度不是同一字段。逐点证据证明被采样时刻状态，不能声称具有无限时间分辨率。

独立从原始 JSONL 复算积分，与 collector 结果最大差约 1.59×10⁻¹²mW；主线程另用 40 位 Decimal 从对应 samples 复算、并逐点核对 JSONL，最大差 1.60×10⁻¹²mW。SD 另用两两差平方恒等式计算，均与六个轮均值的样本 SD 一致。该一致性验证计算过程，不证明电池传感器绝对准确度。

### 唯一播放/计量资格失败与网络限制

第 13 轮 `repeat28-repeat5-b1-avc-automatic-1-vlc` 的唯一期望外标记为 `decoder_changed`。开始诊断是 `c2.qti.avc.decoder`，结束诊断缺少 decoder 字段。冻结测试代码第 321 行直接比较开始与结束字符串；`augmentVlcDiagnostics` 在结束的进程日志中找不到唯一匹配组件时不写该字段。开始 codec 日志 9971 字节/55 行，有当前 PID 625 的硬解组件 Stats；结束日志只剩 4789 字节/29 行，没有组件创建或硬解 Stats 匹配。**现有证据支持“结束身份取证不足”，不能据此断言播放器真正从硬解切到软解，也无法确定日志缺失的底层原因。** 显示帧仍由 2159 增至 7929、播放推进 240000ms；这不能补足解码器一致性证明。

因此保留该轮全部原始数据及 1180.58mW 数值，但不计入合格功耗证据。`actualHardwareDecoders=false` 与 `all18Qualified=false` 在独立审查中如实保留；起始硬解确认不能冒充全轮端点一致性。其余 17 轮通过原播放/计量门槛，不能把不合格轮删除后以 n=5 放行原来 n=6 的续测条件。

另有**第 14 轮 mpv 的设备级网络负载标记**：Wi-Fi 收包增量 118675、发包 10727，其余 17 轮分别为收包 1011–1204、发包 780–992。相同 SSID/AP/频段不等于相同流量。该轮 1267.68mW 数值并不突出，仍按原资格保留并明确标记网络混杂；不临时发明流量阈值或按结果删值。计数覆盖整轮控制窗口，不能确认发生在稳态的哪一秒，也没有应用归因或流量字节数，不能声称完整主机网络资格已全部通过或证明影响了功耗。`networkEvidenceReviewed=true` 仅表示证据已审阅。

本轮链路事件及错误文件均为 0 字节；`networkStartBytes/EndBytes` 只是该事件文件的长度，不是流量计数。所有“网络安静”“依赖导致功耗变化”的因果推断均未成立。测量是播放时的整机电池功耗，包含屏幕、系统、音频、网络及测量开销。

### 轮间 SD 与完整区组

下表保留每引擎完整六轮，**VLC 含不合格第 13 轮，因此是全采集诊断统计，不能作为六轮合格硬解结论**。每引擎 n=6，不以 241 个相关点扩充重复数。

| 引擎 | 均值 ± 样本 SD mW | CV | 范围 mW | 前后半漂移 | 自身 1SD 外 |
| --- | --- | --- | --- | --- | --- |
| Exo | 1158.90 ± 14.36 | 1.24% | 1137.43–1174.61 | −1.51% | 2/6 |
| mpv（含网络标记） | 1270.48 ± 7.20 | 0.57% | 1259.85–1281.35 | +0.30% | 2/6 |
| VLC（含资格失败） | 1164.90 ± 11.95 | 1.03% | 1149.14–1180.58 | +0.35% | 2/6 |

只看原资格通过的 VLC 五轮：1161.76 ± 10.23mW、CV 0.88%、范围 1149.14–1172.72mW。这是另列的描述性子集，不是用来重新通过门槛。Exo/mpv 的六轮均值也保留第 5 块的合格单腿；mpv 第 14 轮网络标记随记录保留。

六个三引擎区组均完成采集，但只有第 1、2、3、4、6 块全员合格。汇总器的四轮 ABBA 专用 `complete=false` 不能误读为三个引擎缺轮；这里按冻结 comparisonBlock 独立复核。下表为第一引擎减第二引擎，±为区组差样本 SD。

| 配对 | 全六块诊断均值 ± SD mW | 五个完整合格块均值 ± SD mW | 六块顺序差值 mW |
| --- | --- | --- | --- |
| Exo−mpv | −111.58 ± 13.00 | −109.47 ± 13.34 | −99.32, −98.54, −104.81, −114.08, −122.14, −130.60 |
| mpv−VLC | +105.58 ± 15.29 | +109.28 ± 13.77 | +101.20, +105.38, +110.56, +132.21, +87.10*, +97.02 |
| Exo−VLC | −6.00 ± 22.59 | −0.19 ± 19.62 | +1.88, +6.84, +5.75, +18.14, −35.05*, −33.57 |

星号涉及失败 VLC，仅作诊断。第 5 块 Exo/mpv 两个合格单腿继续保留，但不与别夜拼成完整三引擎块；五个合格块统计也不能修复原六块续测门槛。三对差共用同一批轮次，不重复计数。

### 与前两夜分别对照

以下以完整六轮采集统计作诊断比较；VLC 当前夜有一轮资格失败，9/27 有变异性失败，均保留标记。旧 SD / 新 SD 内指均值绝对差分别与各夜自身 SD 比较，仅为描述。

| 参照夜 → 9/28 | 引擎 | 均值变化 mW / % | 旧 SD / 新 SD 内 |
| --- | --- | --- | --- |
| 9/26 → 9/28 | Exo | −131.49 / −10.19% | 外 / 外 |
| 9/26 → 9/28 | mpv | −113.34 / −8.19% | 外 / 外 |
| 9/26 → 9/28 | VLC | −109.26 / −8.58% | 外 / 外 |
| 9/27 → 9/28 | Exo | +6.37 / +0.55% | 内 / 内 |
| 9/27 → 9/28 | mpv | +19.75 / +1.58% | 外 / 外 |
| 9/27 → 9/28 | VLC | −17.07 / −1.44% | 内 / 外 |

9/26 三引擎均值±SD分别为1290.38±19.56、1383.82±18.15、1274.16±13.69mW；9/27分别1152.53±15.24、1250.72±12.37、1181.97±85.05mW。9/26稳态33.3–34.4°C、78–89%；9/27为32.5–35.9°C、86–95%；本夜32.0–33.2°C、81–91%。设备、APK、素材和 current-app 路径一致，但温度/电量/设备网络负载不同。较小的同夜 SD 不会消除跨夜偏移；不合并三夜均值，不把本轮全采集 VLC SD 用作软件条件的误差范围。

### 数据纳入与未执行项

本夜 18 轮原始记录全保留。17 轮按原播放/计量资格收入分层证据清单，其中第 14 轮附网络标记；15 轮属于五个完整合格三引擎块，第 5 块的 2 个合格单腿只作补充描述。第 13 轮单列为保留但不合格。加上此前 188 轮，统一清单为 **205 轮**：191 轮完整块比较/重复性（35 个四轮块、17 个三引擎块）、6 桥接、8 筛查/未配对描述；52 组自身均值/SD、29 组区组差。它不是205轮均通过相同稳定性或安静网络门槛，也不构成跨夜合并许可。

软件完成 **0/40**，AV1 短验证 **0/3**；remaining1 的 8 轮、remaining2 的 32 轮全部未执行，原因是重复性资格门槛失败。没有启动软件目录、软件启动尝试或新队列，不是电量不足或短验证失败。全部 43 项逐项保留在 [not-executed-review.json](../../native/out/power-comparison-20260928/software28/not-executed-review.json)。

## 恢复、归档与结束

03:46:41 核实 guard owner、PID/进程组/会话/starttime 后发送 USR1。guard 于03:46:52以真实 `deadline_recovery / host_requested / cleanupSucceeded=true` 退出；03:51读回确认超时***、蓝牙***（状态 ***）、亮度***/模式***、插电常亮***及正常睡眠，主APK与原测试APK的SHA256/MD5均匹配新基线，主应用和守护/runner进程组已不存在。随后仅写 night-complete 并归档，未将真实退出伪造为 already_completed，也未重启守护。

本轮 before/after 用户数据比较：数据库完整性、全部8张表的行/列及结构、所有保存偏好均一致；没有清空应用或覆盖用户备份。整个远端根327个文件、全部72个应用结果文件与用户数据归档完成；逐项校验本地tar及设备文件哈希后，按精确manifest和新建路径记录完成清理，删除3个本轮自建素材，共836802358字节。所有本地原始、失败、旧夜与准备期证据保留。

| 本地归档 | 字节数 | SHA-256 |
| --- | ---: | --- |
| all-app-power-results.tar | 7494656 | ba6163193415cbcc084ade463aedd093e515bc1fa92851aa037e937193ebd200 |
| all-device-directories.tar | 99713536 | 314f2165a873d146afee1720ca878aff40a947d0658ab5c4d97a5acd73aa5b4f |

证据：[独立完整审查](../../native/out/power-comparison-20260928/repeat28/independent-review.json)、[逐轮高精度复算](../../native/out/power-comparison-20260928/repeat28/independent-numerical-review.json)、[最新统一清单](../../native/out/power-comparison-20260928/combined-data-review.json)、[恢复读回](../../native/out/power-comparison-20260928/restoration-verification.json)、[用户数据比较](../../native/out/power-comparison-20260928/data-preservation.json)、[档案清单](../../native/out/power-comparison-20260928/archive-manifest.json)、[精确清理结果](../../native/out/power-comparison-20260928/cleanup-verification.json)。

本轮 `session-state.phase=completed` 表示会话已安全收尾，`requestedSoftwareGoalFulfilled=false` 明确保留40轮正式软件未完成。心跳已通过应用工具暂停，并读回本地配置确认 PAUSED、原提示/时间表/线程绑定未变；guard不会留到次日再作用于手机。[最终离线验证](../../native/out/power-comparison-20260928/final-verification.json)核对归档、原始结果副本、冻结输入、失败保留、独立统计引用、完整块计数、报告链接及关闭状态。
