# Third-party notices

This file tracks the dependency families intentionally approved for
ZivPlayer. Exact resolved versions, artifact checksums, source revisions, and
transitive components are generated and reviewed with the build lockfiles and
SBOM before distribution.

The intended native source closure is now locked in
[`native/source-manifest.toml`](native/source-manifest.toml). It records exact
archive bytes, revisions, licenses, linkage roles, and upstream submodules, but
does not replace the release SBOM, generated notices, or corresponding-source
bundle.

The builder-root and Ubuntu package closure is separately locked in
[`native/toolchain-manifest.toml`](native/toolchain-manifest.toml), including
the signed resolver indexes and exact `.deb` bytes. That supply-chain lock is
not a license inventory for the installed build environment and does not make
the pending Android license files, system notices, retention bundle, release
SBOM, or corresponding-source package complete.

| Component | Intended use | Upstream license | Distribution note |
| --- | --- | --- | --- |
| AndroidX and Jetpack Compose | Android application and UI runtime | Apache-2.0 | Preserve notices required by resolved artifacts. |
| Kotlin and kotlinx.coroutines | Language tooling and asynchronous runtime | Apache-2.0 | Compiler tooling is build-time; runtime artifacts remain inventoried. |
| Kotlin Symbol Processing (KSP) | Build-time Room code generation | Apache-2.0 | Build-time only; pin and verify the Gradle plugin and processor artifacts. |
| compose-miuix-ui | MIUIX-inspired Compose design system | Apache-2.0 | Community project, not an official Xiaomi SDK. APIs are experimental. |
| libmpv Android wrapper | JNI and Android integration bootstrap | MIT for the intended wrapper subset | The complete locked mpv-android application source also contains MPL-2.0/LGPL-3.0-or-later file-picker files, Apache-2.0 Gradle bootstrap code, and a Mozilla-derived CA bundle whose redistribution notice/license remains a release gate; the wrapper license does not cover those files or the embedded native dependency stack. |
| mpv/libmpv | Playback engine | GPL-2.0-or-later in the selected GPL-compatible libmpv build | Corresponding source and build configuration must accompany distributions. |
| FFmpeg | Demuxing and codecs | GPL-3.0-or-later in the selected GPL + version3 build | `--enable-nonfree` is prohibited. Exact configuration must be published. |
| libass | ASS/SSA subtitle renderer | ISC AND Unlicense for the selected source set | Preserve the ISC notice and the compiled wyhash Unlicense notice. |
| libplacebo | GPU rendering and color processing | LGPL-2.1-or-later | Preserve license and corresponding-source obligations for the chosen linkage. |
| dav1d | AV1 decoding used by FFmpeg | BSD-2-Clause | Preserve copyright and license text. |
| mbedTLS | TLS used by curl and FFmpeg | Apache-2.0 OR GPL-2.0-or-later | Preserve the selected license and notices; it is statically linked in the reviewed graph. |
| curl | Network transport used by libmpv | curl | Preserve the curl license and copyright notice. |
| libxml2 | XML support used by FFmpeg and fontconfig | MIT | Preserve copyright and permission notice. |
| FreeType, Fontconfig, FriBidi, HarfBuzz, libunibreak | Font discovery, rasterization, shaping, bidi, and line breaking | FTL/GPL, MIT AND HPND, LGPL, MIT, Zlib respectively | Preserve every component's own notice; the reviewed graph links these statically. |
| Lua | libmpv scripting runtime | MIT | Preserve copyright and permission notice. |
| gas-preprocessor | Build-time FFmpeg assembly helper | GPL-2.0-or-later | Build-time only; ZivPlayer replaces the upstream floating fetch with a full commit pin. |
| libplacebo and FreeType submodules | Build helpers, generated/source inputs, and disabled upstream source closure | Component-specific permissive licenses | Exact commits and whether each input is built or disabled are recorded in the native manifest. |

This inventory is not a substitute for the generated release SBOM or the
complete corresponding-source bundle.
