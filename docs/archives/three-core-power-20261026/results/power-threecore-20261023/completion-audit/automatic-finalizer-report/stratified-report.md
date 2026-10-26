# 功耗实验分层报告

更新时间：2026-10-22T22:43:50.204714+00:00；状态：completed。
已通过预检 12 轮；正式合格 27 轮。

每个完整 ABBA/BAAB 块才算一次重复；不把每秒采样当重复。跨温度或电量层的块仅逐块列出。

| 完整块 | A − B（mW） | 可在同层汇总 | 备注 |
|---|---:|---|---|
| n23hevc10mva:hevc10:b1 | 54.5 | True |  |
| n23hevc10mvb:hevc10:b1 | 57.7 | True |  |
| n23avcmea:avc:b1 | -36.7 | False | crosses_10pct_start_battery_strata |
| n23avcmeb:avc:b1 | -40.9 | True |  |
| n23vp9mea:vp9:b1 | -66.2 | True |  |
| n23vp9meb:vp9:b1 | -61.6 | False | crosses_10pct_start_battery_strata |

hevc10-1080p24.mkv：libmpv@gpu-next − vlc = 56.1 mW；2 个同层完整块。
起始温度层 [35] °C，电量层 [50]%。

avc-1080p24.mkv：exo − libmpv@gpu-next = -40.9 mW；1 个同层完整块。
起始温度层 [35] °C，电量层 [40]%。

vp9-1080p24.mkv：exo − libmpv@gpu-next = -66.2 mW；1 个同层完整块。
起始温度层 [35] °C，电量层 [40]%。

本轮同一 APK 的播放核心比较不能归因为依赖升级收益。温度、掉帧和画面质量证据需分别说明。
原 progress-report.json 为运行进度快照；定量结论以本分层报告及原始审计为准。
