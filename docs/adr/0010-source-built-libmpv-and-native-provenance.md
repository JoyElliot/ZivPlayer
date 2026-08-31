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

`native/tools/materialize_sources.py` is the offline bridge from that cache to
an upstream-shaped build workspace. It verifies the complete cache before any
output write, pre-scans every archive member, rejects path/link escape,
case/Unicode collisions, sparse or special entries, and applies only the
parent/destination placements declared by the manifest. The complete closure
is assembled in one random sibling staging directory, license paths are
rechecked, and the final workspace is published only by rename; an existing
workspace is never replaced.

Archive bytes are first copied into a digest-verified temporary snapshot, so
the bytes extracted are the bytes that passed the manifest lock. Generated
timestamps are normalized to nanosecond precision. Bounded TAR metadata reads
and a bounded decompressed stream apply before Python's GNU longname or PAX
parsers can allocate their payloads. Per-archive and global entry/byte limits,
link-expansion accounting, and cross-archive path/type/case/Unicode collision
checks run before extraction. The materialization receipt records the manifest
digest, source identities, destinations, Android tuple, link mode, and a tree
digest covering entry types, paths, modes, file bytes, and symbolic-link
targets. The verifier rechecks the locked cache, receipt, complete tree, and
link topology derived from the locked archives. It also compares every
ordinary materialized file with its archive bytes and rejects missing, extra,
type-changed, or permission-drifted entries on the canonical POSIX builder.

`preserve` is the only canonical mode for a Linux native build. `portable-copy`
exists solely to inspect the locked closure on a non-canonical path when the
host, notably Windows, cannot create the two safe upstream symbolic links; the
verifier rejects its receipt
unless the inspection-only exception and a non-canonical workspace path are
explicit. Windows inspection records observed modes without claiming POSIX
archive-mode parity. The materializer never interprets `.gitmodules` as
authority to fetch a source.

The repository, cache, output parent, and process identity are trusted. A
receipt is an integrity record, not an authentication signature against a
concurrent process running as the same user. Release jobs therefore materialize
and verify a fresh workspace under an exclusive controlled builder. If the
final rename succeeds but syncing its parent directory fails, the published
workspace is retained and reported as durability-unconfirmed; an operator must
verify it and then deliberately keep or remove it rather than blindly retry.

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
source tool.

`native/toolchain-manifest.toml` now locks the next boundary by bytes. It binds
the source-manifest digest and Android tuple to a four-object linux/amd64 Ubuntu
OCI graph, five Android/Python tool archives, Ubuntu snapshot
`20260811T000000Z`, the 92-package base dpkg projection, 23 APT roots, nine
signed resolver indexes, and 102 exact transitive `.deb` files. The descriptive
Ubuntu tag is not build authority; the linux/amd64 manifest digest in
`baseImage.buildReference` is. The manifest also makes the native build's
network, repository, package-index, floating-reference, and nonfree
prohibitions explicit.

NASM `2.16.01-1build1` is a direct root because the selected FFmpeg x86_64
configuration probes NASM by default. The NDK-bundled Yasm is not treated as
an implicit substitute that would require an unrecorded configure override.

`native/tools/toolchain_tool.py` independently checks root bytes, OCI
descriptors and rootfs `diff_id`, the base status/keyring projection, signed
InRelease bytes and locked signer, exact Packages/`.deb` sets, each archive's
internal Debian control identity, solver identity set, Debian versioned
dependency semantics, and reachability from the declared roots.
The solver transcript bytes and their 102-entry install/configure order are
also locked explicitly so a later offline installer cannot silently substitute
a different action order while preserving only the final package set.
`native/toolchain/prepare-apt-cache.sh` is an explicit networked
preparation operation on the pinned Ubuntu apt/dpkg/gpgv tuple. It stages and
verifies a new ignored cache and refuses replacement; it is not permitted in
the offline build phase.

This closes builder-root selection and APT-closure ambiguity, not the installed
container. The environment status therefore remains
`roots-and-apt-locked-container-pending`: the roots must still be installed in
a fresh controlled Linux filesystem without network access, followed by a
recorded final filesystem/tool projection and accepted Android license and
redistribution inputs. A successful build on an arbitrary workstation or
floating hosted runner is not release provenance. Windows and the currently
configured WSL environment are evidence/inspection environments, not accepted
native release builders.

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

ZivPlayer now has independently verifiable native source and builder-input
baselines. Complete local source and toolchain/APT caches can be prepared
without trusting mutable Git branches, floating container tags, or moving APT
repositories, and the release-input gate fails closed while the installed
container and compliance evidence are absent. This closes source, root-object,
and package-closure selection ambiguity but not binary reproducibility, JNI
correctness, device playback, or license-package gates. The current
public-release decision therefore remains No-Go until the remaining native and
device milestones pass.
