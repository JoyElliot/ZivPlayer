# 功耗实验分层报告

更新时间：2026-10-18T02:18:28.437093+00:00；状态：completed。
已通过预检 4 轮；正式合格 56 轮。

每个完整 ABBA/BAAB 块才算一次重复；不把每秒采样当重复。跨温度或电量层的块仅逐块列出。

| 完整块 | A − B（mW） | 可在同层汇总 | 备注 |
|---|---:|---|---|
| avcmainar2:avc:b1 | 56.9 | True |  |
| avcmainar2:avc:b2 | 79.7 | True |  |
| avcmainbr2:avc:b1 | 37.3 | True |  |
| avcmainbr2:avc:b2 | 27.2 | False | crosses_10pct_start_battery_strata |
| hevcr2:hevc:b1 | 27.2 | True |  |
| hevcr2:hevc:b2 | 38.0 | True |  |
| hevc10r2:hevc10:b1 | 33.0 | True |  |
| hevc10r2:hevc10:b2 | 63.3 | False | crosses_10pct_start_battery_strata |
| vp9r2:vp9:b1 | 20.6 | True |  |
| vp9r2:vp9:b2 | 26.5 | True |  |
| exoavcr2:avc:b1 | -66.0 | True |  |
| exoavcr2:avc:b2 | -85.4 | False | crosses_10pct_start_battery_strata |
| vlcavcr2:avc:b1 | 51.4 | True |  |
| vlcavcr2:avc:b2 | 93.8 | True |  |

avc-1080p24.mkv：libmpv@gpu − libmpv@gpu-next = 58.0 mW；3 个同层完整块。
起始温度层 [34] °C，电量层 [70]%。

hevc-1080p24.mkv：libmpv@gpu − libmpv@gpu-next = 32.6 mW；2 个同层完整块。
起始温度层 [34] °C，电量层 [60]%。

hevc10-1080p24.mkv：libmpv@gpu − libmpv@gpu-next = 33.0 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [60]%。

vp9-1080p24.mkv：libmpv@gpu − libmpv@gpu-next = 23.6 mW；2 个同层完整块。
起始温度层 [34] °C，电量层 [50]%。

avc-1080p24.mkv：exo − libmpv@gpu-next = -66.0 mW；1 个同层完整块。
起始温度层 [34] °C，电量层 [50]%。

avc-1080p24.mkv：libmpv@gpu-next − vlc = 72.6 mW；2 个同层完整块。
起始温度层 [34] °C，电量层 [40]%。

同一新 APK 的渲染器比较不能归因为依赖升级收益。温度、掉帧和画面质量证据需分别说明。
原 progress-report.json 为运行进度快照；定量结论以本分层报告及原始审计为准。
