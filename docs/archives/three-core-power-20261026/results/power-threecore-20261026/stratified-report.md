# 功耗实验分层报告

更新时间：2026-10-25T19:47:47.655888+00:00；状态：completed。
已通过预检 9 轮；正式合格 24 轮。

每个完整 ABBA/BAAB 块才算一次重复；不把每秒采样当重复。跨温度或电量层的块仅逐块列出。

| 完整块 | A − B（mW） | 可在同层汇总 | 备注 |
|---|---:|---|---|
| n26avcmva:avc:b1 | 67.3 | True |  |
| n26avcmvb:avc:b1 | -44.4 | True |  |
| n26hevcmva:hevc:b1 | 66.5 | False | crosses_10pct_start_battery_strata |
| n26hevcmvb:hevc:b1 | 61.8 | True |  |
| n26vp9mva:vp9:b1 | 63.6 | True |  |
| n26vp9mvb:vp9:b1 | 58.6 | True |  |

avc-1080p24.mkv：libmpv@gpu-next − vlc = 11.5 mW；2 个同层完整块。
起始温度层 [33] °C，电量层 [90]%。

hevc-1080p24.mkv：libmpv@gpu-next − vlc = 61.8 mW；1 个同层完整块。
起始温度层 [33] °C，电量层 [80]%。

vp9-1080p24.mkv：libmpv@gpu-next − vlc = 61.1 mW；2 个同层完整块。
起始温度层 [33] °C，电量层 [80]%。

本轮同一 APK 的播放核心比较不能归因为依赖升级收益。温度、掉帧和画面质量证据需分别说明。
原 progress-report.json 为运行进度快照；定量结论以本分层报告及原始审计为准。
