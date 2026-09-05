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

The exact base-plus-APT stage is now reproducibly materialized and records its
194-package installed projection. This is build-environment provenance only:
it does not promote build-time Ubuntu/OpenJDK components into distributed app
dependencies, and it does not replace the still-pending generated system
license/notices inventory for the controlled builder.

The locked Android SDK/NDK and Meson wheel also have a reproducible standalone
builder projection. Its receipt inventories the 26 projected NOTICE/LICENSE/
COPYING-style builder files with archive-member mappings, but explicitly marks
that inventory as builder-tool retention only. It is not Android SDK license
acceptance evidence and is not the product notices bundle. Official package
metadata, real operator acceptance evidence, the source manifest's declared
license files, and the actual linked product graph remain separate gates.

The isolated composition receipt binds the builder root and tool projection for
a fixed runtime smoke only. It introduces no new distributed dependency and
does not change the standalone SDK receipt, accept a license, or complete any
notice/retention obligation. Its successful WSL inspection therefore leaves the
release SBOM, corresponding source, Android license evidence, and generated
product notices as explicit pending gates.

| Component | Intended use | Upstream license | Distribution note |
| --- | --- | --- | --- |
| AndroidX and Jetpack Compose | Android application and UI runtime | Apache-2.0 | Preserve notices required by resolved artifacts. |
| AndroidX DataStore 1.2.1 | Persistent player settings, including its native shared-counter helper | Apache-2.0 | The two ABI helper libraries come from the locked `datastore-core-android` AAR and are verified separately from the libmpv execution receipt. |
| Protocol Buffers runtime relocated by DataStore | Preferences serialization | BSD-3-Clause | Preserve the bundled `datastore-preferences-external-protobuf/LICENSE.txt`. |
| Okio and kotlinx.serialization | DataStore storage and serialization dependencies | Apache-2.0 | Exact transitive versions and artifacts remain locked and checksum-verified. |
| Kotlin and kotlinx.coroutines | Language tooling and asynchronous runtime | Apache-2.0 | Compiler tooling is build-time; runtime artifacts remain inventoried. |
| Kotlin Symbol Processing (KSP) | Build-time Room code generation | Apache-2.0 | Build-time only; pin and verify the Gradle plugin and processor artifacts. |
| compose-miuix-ui | MIUIX-inspired Compose design system | Apache-2.0 | Community project, not an official Xiaomi SDK. APIs are experimental. |
| mpv-android upstream sources | Locked build scripts and Android integration reference; Maven bootstrap retired | MIT for the reviewed subset | ZivPlayer now builds its own GPL-3.0-or-later JNI bridge. The complete locked mpv-android source also contains MPL-2.0/LGPL-3.0-or-later file-picker files, Apache-2.0 Gradle bootstrap code, and a Mozilla-derived CA bundle whose redistribution notice/license remains a release gate; the upstream wrapper license does not cover those files or the embedded native dependency stack. |
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
