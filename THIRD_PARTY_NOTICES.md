# Third-party notices

This file tracks the dependency families intentionally approved for
ZivPlayer. Exact resolved versions, artifact checksums, source revisions, and
transitive components are generated and reviewed with the build lockfiles and
SBOM before distribution.

| Component | Intended use | Upstream license | Distribution note |
| --- | --- | --- | --- |
| AndroidX and Jetpack Compose | Android application and UI runtime | Apache-2.0 | Preserve notices required by resolved artifacts. |
| Kotlin and kotlinx.coroutines | Language tooling and asynchronous runtime | Apache-2.0 | Compiler tooling is build-time; runtime artifacts remain inventoried. |
| compose-miuix-ui | MIUIX-inspired Compose design system | Apache-2.0 | Community project, not an official Xiaomi SDK. APIs are experimental. |
| libmpv Android wrapper | JNI and Android integration bootstrap | MIT | The wrapper license does not cover the embedded native dependency stack. |
| mpv/libmpv | Playback engine | GPL-2.0-or-later in the selected full-feature build | Corresponding source and build configuration must accompany distributions. |
| FFmpeg | Demuxing and codecs | Depends on locked build flags; expected GPL-compatible build | `--enable-nonfree` is prohibited. Exact configuration must be published. |
| libass | ASS/SSA subtitle renderer | ISC | Preserve copyright and permission notice. |
| libplacebo | GPU rendering and color processing | LGPL-2.1-or-later | Preserve license and corresponding-source obligations for the chosen linkage. |

This inventory is not a substitute for the generated release SBOM or the
complete corresponding-source bundle.
