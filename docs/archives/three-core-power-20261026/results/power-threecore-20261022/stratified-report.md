# 功耗实验分层报告

更新时间：2026-10-22T09:11:11.617968+00:00；状态：completed。
已通过预检 12 轮；正式合格 48 轮。

每个完整 ABBA/BAAB 块才算一次重复；不把每秒采样当重复。跨温度或电量层的块仅逐块列出。

| 完整块 | A − B（mW） | 可在同层汇总 | 备注 |
|---|---:|---|---|
| avcmva:avc:b1 | 88.5 | False | crosses_1C_start_temperature_strata |
| avcmvb:avc:b1 | 49.1 | False | crosses_1C_start_temperature_strata |
| n22r1vp9mva:vp9:b1 | 59.7 | True |  |
| n22r1vp9mvb:vp9:b1 | 75.1 | False | crosses_10pct_start_battery_strata |
| n22r1hevcmea:hevc:b1 | -108.1 | True |  |
| n22r1hevcmeb:hevc:b1 | -80.3 | True |  |
| n22r1hevc10mea:hevc10:b1 | -66.4 | True |  |
| n22r1hevc10meb:hevc10:b1 | -73.6 | True |  |
| n22r1vp9mea:vp9:b1 | -83.5 | True |  |
| n22r1vp9meb:vp9:b1 | -73.7 | True |  |
| n22r1hevcmva:hevc:b1 | 49.8 | True |  |
| n22r1hevcmvb:hevc:b1 | 64.8 | True |  |

vp9-1080p24.mkv：libmpv@gpu-next − vlc = 59.7 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [90]%。

hevc-1080p24.mkv：exo − libmpv@gpu-next = -94.2 mW；2 个同层完整块。
起始温度层 [34] °C，电量层 [80]%。

hevc10-1080p24.mkv：exo − libmpv@gpu-next = -66.4 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [80]%。

hevc10-1080p24.mkv：exo − libmpv@gpu-next = -73.6 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [70]%。

vp9-1080p24.mkv：exo − libmpv@gpu-next = -78.6 mW；2 个同层完整块。
起始温度层 [34] °C，电量层 [70]%。

hevc-1080p24.mkv：libmpv@gpu-next − vlc = 49.8 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [70]%。

hevc-1080p24.mkv：libmpv@gpu-next − vlc = 64.8 mW；1 个同层完整块。
起始温度层 [33] °C，电量层 [60]%。

本轮同一 APK 的播放核心比较不能归因为依赖升级收益。温度、掉帧和画面质量证据需分别说明。
原 progress-report.json 为运行进度快照；定量结论以本分层报告及原始审计为准。
