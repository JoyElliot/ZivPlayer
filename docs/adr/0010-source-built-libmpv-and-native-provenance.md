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

`native/tools/rootfs_tool.py` is the offline bridge from the locked OCI base
layer to a filesystem tree. It supports only the selected single-layer image,
requires Linux root and ext4, rejects whiteouts and non-regular filesystem
objects, normalizes locked metadata, keeps staging and the published root
`0700 root:root`, and publishes only to an absent destination by an atomic
no-replace rename. Its canonical receipt binds the exact manifest byte
snapshot, OCI layer identity, and a tree digest over paths, entry types,
permissions, owners, sizes, file hashes, and link text. This deliberately does
not invoke Docker/Podman, install APT packages, extract Android tools, or claim
an installed-container projection.

`native/tools/environment_tool.py` is the next offline bridge. It re-verifies
all locks, materializes the base into fresh ext4 staging, and installs the exact
102-package closure in locked order with APT sources disabled. Package scripts
run in private mount/network/PID/UTS/IPC namespaces with an exact mount set,
loopback-only networking, an empty read-only `/proc/keys`, immutable inputs,
zero Linux capabilities, `no_new_privs`, and a locked seccomp filter. The
complete installer process tree is bounded by a dedicated cgroup-v2 domain and
hard process resource limits. Cleanup proves that the cgroup is empty and that
no mounts remain before the tree can be published.

The installed-stage receipt binds the exact 194-package dpkg projection,
installer/helper snapshots, stdout/stderr, sandbox policy, deterministic
generated-file policy, and a canonical tree digest. Java cacerts timestamps are
normalized without changing aliases or DER certificates; ldconfig's auxiliary
inode cache is removed while its runtime cache is retained; only the two
tzdata wall-clock transcript lines are canonicalized. Paths, hard links,
owners, modes, safe user xattrs, timestamps, file bytes, and symlink targets are
rechecked under explicit resource budgets. Publication is a no-replace rename
after filesystem synchronization, followed by output-parent synchronization.

The retained hardened stage used by the current composition contains 12,449
entries and 709,614,523 file bytes. Its tree SHA-256 is
`5ed7511bcb6f9d9cc5a2a966f54495b243135a2f9de469e1e7a4c5850406f7e0`, and its
receipt SHA-256 is
`6a4929a97403cf8058a3b02c36dc5ba5d30e262f44472520b3b8061ede9b5cae`.
Earlier pre-hardening WSL reproducibility measurements are superseded by these
current bound values. This closes the current offline base-plus-APT installation
ambiguity under the stated trusted-host boundary, but not the installed
container or cross-run binary reproducibility.

`native/tools/sdk_tool.py` separately closes the archive-to-filesystem
ambiguity for the locked Android SDK/NDK and Meson wheel. It verifies immutable
archive snapshots and ZIP metadata/content, Android package identities, the
NDK's bounded relative symlink graph and explicit case-only header pairs, and
the Meson source-wheel RECORD. It then writes a root-owned, timestamp-normalized
Linux/ext4 projection for `/opt/zivplayer/toolchain`, retains the locked wheel,
adds a fixed `python3.12 -I -S -B` Meson launcher, and atomically publishes only
after a second archive, projection-record, tree, helper, and receipt check. It
does not invoke `sdkmanager`, `pip`, or the network.

Two fresh complete-cache WSL inspection materializations produced identical
25,286-entry trees with 2,913,084,578 file bytes. Their installed-tree SHA-256
was `dac187631c910c5e7bb10c20573d8c7b8accc1051077d22a6d11a5707c11c5a4`,
their projection SHA-256 was
`d5becfde012c856be4ed5eb04215e233a8a6e8ff3d860198c45c9f197fd6ef3c`, and
their byte-identical receipt SHA-256 was
`7f9bf9d66185c63d60a41e451146717be2794f95dc73a675618da7c09fd6e544`.
The repeated output was removed after comparison; the retained inspection tree
was separately reverified against the locked archives and current helpers.

The SDK receipt intentionally declares a standalone mountable projection with
`aptEnvironmentBound=false` and `releaseInput=false`. Its source wheel RECORD
proves the original wheel members before `.data/data` remapping; a separate
projection digest proves the installed paths. Its legal inventory is only the
projected builder-tool subset. It neither generates `package.xml` nor proves
Android license acceptance or product redistribution obligations.

`native/tools/composition_tool.py` provides a separate ephemeral binding rather
than modifying either standalone receipt. It re-verifies and pins both trees,
their receipt bytes, and every executed helper; creates fresh private mount,
network, PID, UTS, and IPC namespaces; pivots onto the read-only APT root; and
binds the SDK projection read-only at `/opt/zivplayer/toolchain`. Its exact mount
inventory permits writable tmpfs only for declared transient paths. The fixed
profile requires loopback with no routes, PID 1, zero capabilities,
`no_new_privs`, the pinned seccomp program, hard resource limits, and a bounded
cgroup whose descendants are drained before success. The nested-namespace
denial is defense-in-depth evidence from the combined no-capability and seccomp
boundary, not an attribution to either mechanism alone.

The runtime profile checks Python/Meson/Ninja/pkg-config, JDK and Android package
identities, then compiles temporary API-26 `arm64` and `x86_64` shared objects
with the NDK 29 compilers and rejects any ELF LOAD alignment other than 16 KiB.
It emits a canonical, no-replace receipt through a pinned parent-directory file
descriptor. The receipt explicitly remains `ready=false` and
`releaseInput=false` because this is a toolchain smoke, not a libmpv build.

Two final WSL inspection runs produced byte-identical 9,562-byte receipts with
SHA-256
`e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989`.
The composition digest was
`11173e513a3fc0348d5780b60def6a340d140980d4b40bd0bf74fdfd9b5cd51a` and the
canonical smoke transcript SHA-256 was
`c491134a712d88a1d0d76e96eb39494a623cb61fb818ad962e286a112d19e60c`.
The receipt binds APT receipt/tree hashes
`6a4929a97403cf8058a3b02c36dc5ba5d30e262f44472520b3b8061ede9b5cae` /
`5ed7511bcb6f9d9cc5a2a966f54495b243135a2f9de469e1e7a4c5850406f7e0`
and SDK receipt/tree/projection hashes
`7f9bf9d66185c63d60a41e451146717be2794f95dc73a675618da7c09fd6e544` /
`dac187631c910c5e7bb10c20573d8c7b8accc1051077d22a6d11a5707c11c5a4` /
`d5becfde012c856be4ed5eb04215e233a8a6e8ff3d860198c45c9f197fd6ef3c`.
These results assume the ADR's exclusive trusted root-controlled builder
boundary; they do not claim safety against a concurrent hostile root process,
and WSL remains an inspection rather than release-provenance environment.

### Native build profile, inspection preflight, and preparation

`native/native-build-profile.toml` now locks the first libmpv-stack execution
contract without claiming that the stack has been built. It binds the source
and toolchain manifest byte digests, upstream revision, API 26, the two selected
ABIs, 16 KiB page-size policy, fixed tool versions and logical mounts, two exact
command vectors, the output allowlist, the NDK runtime-library bytes, and the
original/replacement hashes and applied modes of two reviewed upstream-script
overlays.

The canonical preserve-mode source is an immutable input. Each attempted build
must use a fresh writable copy at `/build/source`, a fresh output at
`/build/output`, an isolated HOME at `/build/home`, and a separate temporary
tree at `/build/tmp`. All four paths are disjoint. This is not merely defensive:
the selected upstream scripts generate Libass Autotools files and build or
reconfigure Lua and mbedTLS inside their source directories. Binding the
canonical materialization read-only as the build CWD would therefore contradict
the reviewed pipeline.

The `buildall.sh` overlay accepts only the exact `--arch arm64 mpv` and
`--arch x86_64 mpv` invocations, selects the API-26 NDK tools, retains the
16 KiB linker maximum-page-size flag, and refuses reuse of an ABI prefix. The
`path.sh` overlay removes ambient SDK/NDK discovery, fixes the composed
toolchain paths, verifies the pinned `gas-preprocessor.pl`, and fixes HOME,
temporary/XDG paths, locale, time, source epoch, parallelism, and relevant
wrapper/configuration variables. The future namespace executor must still
enforce the profile's empty inherited environment and no-network policy; the
scripts alone are not a sandbox.

The first output contract contains eight source-built libraries per ABI
(`libavcodec.so`, `libavdevice.so`, `libavfilter.so`, `libavformat.so`,
`libavutil.so`, `libmpv.so`, `libswresample.so`, and `libswscale.so`) plus the
byte-locked NDK `libc++_shared.so`. It intentionally excludes `libplayer.so`.
The existing bootstrap API is handle-based under `dev.jdtech.mpv`, while the
locked upstream JNI wrapper uses a different `is.xyz.mpv` global/static
contract; that wrapper cannot be relabeled or silently accepted as the release
bridge.

`native/tools/native_build_tool.py validate` checks the profile, both bound
manifests, and overlay replacement bytes without executing a build. Its
Linux-root-only `preflight` additionally re-verifies the preserve-mode source
workspace on ext4, the untouched upstream bytes/modes at both overlay
destinations, the exact per-ABI NDK `libc++_shared.so` inputs, and the existing
toolchain-composition receipt. The Linux-root-only `prepare` command repeats
those gates, creates a fresh independent root-owned source copy, preserves the
locked symlink texts while rejecting hardlinks and nested mounts, atomically
applies both overlays only to that copy, and creates disjoint empty output,
HOME, and temporary trees. It then publishes the complete workspace with
no-replace semantics only after its canonical preparation receipt verifies.
`verify-preparation` independently recomputes the receipt from the current
locked inputs and published tree.

Two fresh WSL ext4 preserve-mode source materializations produced identical
30,436-entry trees (29,238 files, 1,196 directories, and two symlinks), tree
SHA-256
`05a19a0fc8d11c88903c663783ebca00c0201f656fa54b580ab47a75fa746ee6`,
and byte-identical receipt SHA-256
`e2ea14b6eea0c2f692a321853473f04dad2f1f3199d017bfffc733a858523c04`.
The retained tree passed the above preflight with profile SHA-256
`538fe37887840c4e23acdb189d3464878dd006dfc42f6e4c4565490f88252413`.
These are inspection results only. No native library was built, and WSL remains
outside accepted release provenance.

One complete WSL preparation published `/var/tmp/zivplayer-native-build` with a
5,378-byte receipt whose SHA-256 is
`eff4993d2c1a079564f8da458eb2fc3bb7ee234716d8ce2380ee4c5e59de49f5`.
The post-overlay source retained 30,436 entries and 408,191,312 regular-file
bytes; its tree SHA-256 is
`bdabef1dc6032963422aad7576ab8ea45a5f5393d5a304b5273f94fbaabb2d2d`.
An independent verification run passed, and the canonical source/overlay-origin
hashes remained unchanged. This receipt is only a preparation record:
`buildExecuted=false`, `ready=false`, and `releaseInput=false`. The executor,
compilation, output staging, ELF/JNI audits, Gradle integration, compliance
bundles, and native-build receipt remain pending. This mechanism assumes the
exclusive trusted root-controlled builder boundary stated above; it is not a
defense against a concurrent hostile root process.

The executor isolation contract is intentionally stored separately in
`native/native-executor-policy.toml`, so locking it does not invalidate the
existing preparation receipt. Its first phase is `namespace-probe` and binds
the exact native-build profile SHA-256, logical mount paths, empty inherited
environment, process/cgroup/filesystem limits, namespace properties, and the
byte digests of the namespace, probe, and seccomp helpers. The schema fixes
`buildCommands=false`, `artifactStaging=false`, `buildReceipt=false`,
`ready=false`, and `releaseInput=false`; changing any of those values is not an
extension of this phase.

The locked namespace design uses the APT stage only as an overlay lower,
creates `/build` in the ephemeral upper rather than modifying the APT tree,
binds separately pinned source/output/HOME/TMP directories, remounts the
composed root read-only, pivots away from and detaches the host root, then drops
all capabilities and applies the existing seccomp helper. The probe is limited
to mount, environment, network, privilege, descriptor, and write-protection
checks. `native/tools/native_executor_tool.py validate` currently performs only
static policy/profile/helper validation: it has no launch or build subcommand,
no accepted executor-probe result has been recorded, and implementation-only
helper checks have not executed a compiler or upstream build command.

The environment status therefore remains
`roots-and-apt-locked-container-pending`: accepted Android license files,
redistribution inputs, an accepted release-builder materialization/run, and the
actual native build/audits are still required. A successful build on an
arbitrary workstation or floating hosted runner is not release provenance.
Windows and the currently configured WSL environment are evidence/inspection
environments, not accepted native release builders.

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
baselines, a reproducible offline base-plus-APT materialization stage, and a
separate reproducible Android/Python tool projection whose fixed read-only
composition has passed an isolated inspection smoke. A byte-locked API-26
libmpv-stack profile, preserve-source preflight, and verified independent
prepared workspace, plus a closed namespace-probe policy, now make the next
execution boundary explicit without treating preparation or static validation
as a build result.
Complete local source and toolchain/APT caches can be prepared without trusting
mutable Git branches, floating container tags, or moving APT repositories, and
the release-input gate fails closed while compliance, canonical native-build,
and redistribution evidence are absent. This closes source, root-object, package-
closure, offline APT installed-state, standalone SDK projection, and fixed
toolchain-composition ambiguity under the trusted builder boundary, but not
binary reproducibility, JNI correctness, device playback, or license-package
gates. The current
public-release decision therefore remains No-Go until the remaining native and
device milestones pass.
