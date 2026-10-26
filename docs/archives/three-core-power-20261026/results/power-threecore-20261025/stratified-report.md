# 功耗实验分层报告

更新时间：2026-10-25T05:25:28.639259+00:00；状态：completed。
已通过预检 12 轮；正式合格 51 轮。

每个完整 ABBA/BAAB 块才算一次重复；不把每秒采样当重复。跨温度或电量层的块仅逐块列出。

| 完整块 | A − B（mW） | 可在同层汇总 | 备注 |
|---|---:|---|---|
| n25hevcmea:hevc:b1 | -72.9 | False | crosses_1C_start_temperature_strata |
| n25hevcmeb:hevc:b1 | -88.9 | True |  |
| n25hevc10eva:hevc10:b1 | -0.2 | True |  |
| n25hevc10evb:hevc10:b1 | -14.3 | False | crosses_10pct_start_battery_strata |
| n25avceva:avc:b1 | 17.2 | True |  |
| n25avcevbr1:avc:b1 | -37.8 | True |  |
| n25vp9eva:vp9:b1 | 46.3 | False | crosses_1C_start_temperature_strata, crosses_10pct_start_battery_strata |
| n25vp9evb:vp9:b1 | 31.1 | True |  |
| n25hevceva:hevc:b1 | 6.0 | False | crosses_1C_start_temperature_strata |
| n25hevcevb:hevc:b1 | 0.3 | False | crosses_1C_start_temperature_strata, crosses_10pct_start_battery_strata |
| n25hevc10mea:hevc10:b1 | -41.4 | True |  |
| n25hevc10meb:hevc10:b1 | -55.1 | False | crosses_1C_start_temperature_strata |

hevc-1080p24.mkv：exo − libmpv@gpu-next = -88.9 mW；1 个同层完整块。
起始温度层 [35] °C，电量层 [80]%。

hevc10-1080p24.mkv：exo − vlc = -0.2 mW；1 个同层完整块。
起始温度层 [35] °C，电量层 [70]%。

avc-1080p24.mkv：exo − vlc = 17.2 mW；1 个同层完整块。
起始温度层 [35] °C，电量层 [60]%。

avc-1080p24.mkv：exo − vlc = -37.8 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [50]%。

vp9-1080p24.mkv：exo − vlc = 31.1 mW；1 个同层完整块。
起始温度层 [35] °C，电量层 [40]%。

hevc10-1080p24.mkv：exo − libmpv@gpu-next = -41.4 mW；1 个同层完整块。
起始温度层 [35] °C，电量层 [30]%。

本轮同一 APK 的播放核心比较不能归因为依赖升级收益。温度、掉帧和画面质量证据需分别说明。
原 progress-report.json 为运行进度快照；定量结论以本分层报告及原始审计为准。
