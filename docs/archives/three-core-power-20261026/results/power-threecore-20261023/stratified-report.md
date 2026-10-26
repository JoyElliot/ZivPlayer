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

收尾补充：本轮共采集 27 条合格正式腿，其中 24 条组成 6 个完整四腿块；另有 12 条功能预检。最后的 HEVC10 ExoPlayer/VLC 补充块仅完成 3 腿，第 4 腿在冷却期间遇到截止时间，未启动播放。这 3 腿已保留，但不构成完整重复，全部排除于功耗均值和配对结论。两个后续可选追加块未启动；其余时间不足的互补组整组跳过。原状态为 pending 的两个可选追加块只是未运行，本轮已终止，不会继续启动。HEVC 仅有功能预检，未取得完整正式功耗数据。
