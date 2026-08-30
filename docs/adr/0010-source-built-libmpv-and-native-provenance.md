# ADR 0010: Source-built libmpv and native provenance baseline

- Status: Accepted
- Date: 2026-08-31

## Context

ADR 0006 permits `dev.jdtech.mpv:libmpv:1.0.0` only as a bootstrap artifact.
Its wrapper license and locked AAR checksum do not identify the source,
configuration, licenses, or corresponding-source obligations of the embedded
native stack. Its narrowed callbacks also cannot provide the entry identity,
end reasons, and operation results required by the release-grade player
boundary.

The mpv-android 2026-08-11 release is the closest reviewed upstream build
baseline. Its tag resolves to commit
`ad98fc97ff1d25e217389e7238a1abda8c13a6c4` and its release notes identify the
mpv, FFmpeg, dav1d, libass, and libplacebo revisions used for that release.
However, its download scripts clone several floating branches and fetch
`gas-preprocessor.pl` from `master`. The upstream project also builds an
application, not a reusable AAR, so ZivPlayer must adapt the Kotlin and JNI
wrapper rather than consume an upstream binary package.

The actual upstream native pipeline uses Meson/Ninja,
Autoconf/Automake/libtool/Make, and NDK `ndk-build`. It does not use CMake. The
earlier CMake 4.1.2 placeholder in ADR 0003 was made before this source pipeline
was inspected and is not an input to the selected libmpv build.

## Decision

### Source identity

`native/source-manifest.toml` is the canonical source lock. Its first schema
locks 23 byte-identical inputs selected from mpv-android 2026-08-11:

- mpv-android, mpv, FFmpeg, dav1d, libass, and libplacebo at full commits;
- mbedTLS, libxml2, fontconfig, FreeType, FriBidi, HarfBuzz, libunibreak, Lua,
  and curl at reviewed release archives or peeled tag commits;
- libplacebo and FreeType submodule commits, including disabled demo/Vulkan or
  otherwise unused submodules so the upstream source closure remains explicit;
  and
- `gas-preprocessor.pl` at
  `ac1836309c2e77023c228b7184485597286289d3`, replacing the upstream floating
  `master` fetch. This is a new reproducibility pin, not a claim that the
  historical upstream release recorded that revision.

Every cached input must match both the manifest byte count and SHA-256. A tag,
short hash, URL, or Git host alone is not an accepted identity. The standard
library-only `native/tools/source_tool.py` makes network access explicit:
`validate` and `verify-cache` are read-only and offline, while `fetch` writes
to the ignored cache by default, verifies a `.part` file, and atomically
renames it. An explicitly supplied custom cache path is the caller's chosen
write boundary.

### Android and ABI boundary

The source-built pipeline must build native code with Android API 26 and
package only `arm64-v8a` and `x86_64`. This deliberately raises the still
unmodified upstream native API 23 baseline to the application's minimum API
and does not add compatibility work for lower Android releases. The adaptation
has not yet been built or accepted.

The native linker baseline requires 16 KiB maximum page-size alignment and the
adapted JNI build must enable flexible page sizes. NDK behavior or flags alone
are not proof: every produced ELF must be inspected for LOAD alignment before
it can enter a release artifact.

### Toolchain boundary

Native provenance is Linux-only and starts with NDK `29.0.14206865`, Android
SDK Platform 36, Build Tools 36.0.0, and JDK 17 at the imported upstream wrapper
boundary. ZivPlayer's main Gradle build remains on its independently locked
JDK 21, compile/target SDK 37, AGP 9.3.2, Gradle 9.6.1, and Kotlin 2.4.0
baseline.

The source manifest records project-selected minimum tool versions. Where an
upstream component declares a floor, the selected minimum meets the highest
one in the source closure; Python 3.11 also satisfies this repository's TOML
source tool. Exact installed versions are not yet implied, and the environment
status remains `source-locked-container-pending`. Before accepting native binaries,
the build must additionally lock a Linux container/base-image digest, exact
system package revisions, Python, Meson, Ninja, JDK patch, and Android command
line tools. A successful build on an arbitrary workstation or floating hosted
runner is not release provenance. Windows and the currently configured WSL
environment are not accepted native release builders.

This decision supersedes only ADR 0003's CMake 4.1.2 placeholder for the libmpv
pipeline. It does not change the main Android build tuple recorded there.

### Linkage and license profile

The selected profile is a full GPL-compatible build:

- FFmpeg is configured with both GPL and version-3 features and is treated as
  GPL-3.0-or-later in the selected binary configuration;
- mpv/libmpv uses its default GPL profile;
- `--enable-nonfree` is prohibited; and
- libass, libplacebo, codecs, text/font libraries, TLS/XML libraries, Lua, and
  build-time submodules remain individually inventoried even when statically
  linked into another output.

Final notices must be generated from the actual build graph, not inferred only
from this intended dependency graph. Build flags, patches and their hashes,
per-ABI outputs, SBOM, licenses, notices, and complete corresponding source are
release inputs.

### Bootstrap retirement gate

The source lock does not authorize public release. The bootstrap Maven AAR may
remain for development while the source wrapper is integrated, but a public
artifact is blocked until all of the following are true:

1. both selected ABIs build from the locked sources and locked Linux toolchain;
2. the source wrapper provides typed entry identity, end reasons, command and
   property operation results, ordered callbacks, and idempotent shutdown;
3. JNI exports, ELF architecture, SONAME/NEEDED closure, page alignment, and
   hashes are audited;
4. corresponding source, exact build configuration, SBOM, licenses, and
   notices are packaged; and
5. dependency and APK inspection proves
   `dev.jdtech.mpv:libmpv:1.0.0` and its reviewed AAR hash are absent.

## Consequences

ZivPlayer now has an independently verifiable native source baseline and a
complete local offline cache can be prepared without trusting mutable Git
branches. This closes source-selection ambiguity but not binary
reproducibility, JNI correctness, device playback, or license-package gates.
The current public-release decision therefore remains No-Go until the remaining
native and device milestones pass.
