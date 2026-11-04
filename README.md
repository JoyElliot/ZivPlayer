# ZivPlayer

ZivPlayer is an Android-first media player focused on broad format support,
high-quality rendering, and advanced subtitles.

Android 播放内核已切换到 Media3 ExoPlayer，保留现有媒体库、队列与交互。
迁移设计与字幕兼容范围见 [ADR 0024](docs/adr/0024-exoplayer-playback.md)，
真机功能回归和未验收边界见 [dev.exo4 验证记录](docs/validation/2026-11-04-exoplayer-device.md)。

## 这是一个尚未开发完成的项目，请暂时不要构建作为日常使用

## License

ZivPlayer is licensed under the GNU General Public License, version 3 or (at
your option) any later version. See [`LICENSE`](LICENSE). Source files created
for this project use the SPDX identifier `GPL-3.0-or-later`.

Bundled and linked third-party components retain their own copyright and
license terms. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
