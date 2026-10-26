# 功耗实验分层报告

更新时间：2026-10-18T23:55:00.872075+00:00；状态：completed。
已通过预检 9 轮；正式合格 44 轮。

每个完整 ABBA/BAAB 块才算一次重复；不把每秒采样当重复。跨温度或电量层的块仅逐块列出。

| 完整块 | A − B（mW） | 可在同层汇总 | 备注 |
|---|---:|---|---|
| hevcme:hevc:b1 | -59.3 | False | crosses_1C_start_temperature_strata |
| hevcmv:hevc:b1 | 49.2 | False | crosses_10pct_start_battery_strata |
| hevcev:hevc:b1 | -8.5 | True |  |
| hevc10mv:hevc10:b1 | 71.9 | True |  |
| hevc10me:hevc10:b1 | -65.2 | False | crosses_1C_start_temperature_strata |
| hevc10ev:hevc10:b1 | -25.1 | False | crosses_10pct_start_battery_strata |
| vp9ev:vp9:b1 | -4.8 | True |  |
| vp9me:vp9:b1 | -93.1 | False | crosses_1C_start_temperature_strata |
| vp9mv:vp9:b1 | 66.3 | True |  |
| avcev:avc:b1 | -4.2 | True |  |
| avcme:avc:b1 | -73.5 | True |  |

hevc-1080p24.mkv：exo − vlc = -8.5 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [60]%。

hevc10-1080p24.mkv：libmpv@gpu-next − vlc = 71.9 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [60]%。

vp9-1080p24.mkv：exo − vlc = -4.8 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [50]%。

vp9-1080p24.mkv：libmpv@gpu-next − vlc = 66.3 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [50]%。

avc-1080p24.mkv：exo − vlc = -4.2 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [40]%。

avc-1080p24.mkv：exo − libmpv@gpu-next = -73.5 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [40]%。

本轮同一 APK 的播放核心比较不能归因为依赖升级收益。温度、掉帧和画面质量证据需分别说明。
原 progress-report.json 为运行进度快照；定量结论以本分层报告及原始审计为准。
