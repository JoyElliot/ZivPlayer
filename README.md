# ZivPlayer

ZivPlayer is an Android-first media player focused on broad format support,
high-quality rendering, and advanced subtitles.

## Project status

The host-side M0-M6 foundation is complete: architecture and licensing decisions are
recorded, the Gradle dependency supply chain is locked and verified, the
Android application builds with a MIUIX-backed Compose shell, and a pure
Kotlin player contract now drives a serialized runtime with core generation
filtering. A Media3 `MediaSessionService` owns that runtime and the single
libmpv backend; the activity connects only through a `MediaController` and
hands short-lived video `Surface` instances to the service.

Media opened through Android's document picker now receives a stable media
identity, is stored in a versioned Room database only when restart-safe URI
access is confirmed, and stays distinct from its queue-occurrence identity.
The playback service owns a serialized progress recorder that samples active
position and flushes pause, stop, completion, transition, reset, and shutdown
checkpoints without using transient file-descriptor paths as durable data.

The MIUIX-backed player screen now observes immutable state from a
generation-fenced, reconnecting MediaController. It exposes the single-item
playback commands currently implemented by the adapter: open, play/pause,
replay, stop, seek, speed, volume, and repeat-one. Restart-safe recent documents
can be reopened only after their current SAF grant is checked, incomplete
checkpoints resume at their recorded position, completed media starts at zero,
and forgetting history reports an orphaned grant instead of hiding it.

The reviewed bootstrap libmpv AAR is now packaged behind the Android adapter
module and connected to the application APK. JVM and Android build checks
cover state projection, command policy, reset recovery, Surface lease ordering,
and service manifest composition. A physical device or configured emulator is
still required to prove actual media output, foreground notification behavior,
Surface recreation, audio focus, native shutdown, Room behavior, document
provider permission persistence, and process-death reopening.

The bootstrap adapter fences callbacks only under a single-instance,
serialized stop/load policy. Its reviewed AAR discards event payloads and
playlist-entry identity, so this is not a release-grade stale-callback
guarantee. Native callbacks enter one ordered adapter event stream, and an
accepted load that never prepares or fails is terminated by a bounded runtime
readiness deadline instead of remaining in `LOADING` indefinitely. A reset
error makes that session non-reusable; its Android owner closes and recreates
the backend/session pair before accepting the next media item.

The application is written in Kotlin with Jetpack Compose and MIUIX. libmpv is
the only playback engine; AndroidX Media3 is reserved for Android media-session
and system-integration APIs.

M7 native supply-chain work has started. A 23-input source manifest locks the
complete reviewed mpv-android 2026-08-11 source closure by immutable revision,
byte count, SHA-256, license, and linkage role. A second byte-level manifest now
locks the Linux/amd64 builder roots: a digest-qualified Ubuntu OCI graph, five
Android/Python tool archives, the 2026-08-11 Ubuntu snapshot, 92 base-image dpkg
identities, 23 requested packages, nine signed resolver indexes, and the exact
102-package transitive `.deb` closure. The offline verifier rechecks the OCI
graph and rootfs `diff_id`, Ubuntu signatures, package/index bytes, solver
selection and its locked install order, and all 523 `Pre-Depends`/`Depends`
clauses.

The ignored cache has been prepared and independently verified in WSL. The
locked single-layer OCI base and exact 102-package APT closure can now be
materialized without Docker or Podman into a fresh ext4 root-only directory.
The offline installer disables APT sources, enters private mount/network/PID/
UTS/IPC namespaces, drops all capabilities before package scripts, applies a
locked seccomp policy and cgroup-v2 resource envelope, and publishes only after
canonical installed-state, evidence, tree, and durability verification.

The retained hardened APT stage used by the current composition contains
12,449 entries and 709,614,523 file bytes; its tree/receipt SHA-256 values are
`5ed7511b...6f7e0` / `6a4929a9...b5cae`. Earlier pre-hardening reproducibility
measurements are superseded by these current bound values. The locked Android
SDK/NDK and Meson wheel can also be projected directly, without `sdkmanager`,
`pip`, or network access, into a fresh Linux/ext4 tree. The projection preserves
the NDK's audited symlinks and case-sensitive headers, validates the wheel
RECORD, and writes an independently verifiable receipt before atomic
publication. Two fresh complete-cache
inspection runs produced byte-identical receipts (SHA-256
`7f9bf9d6...6e544`) and the same 25,286-entry, 2,913,084,578-byte tree
(`dac18763...1c5a4`; projection digest `d5becfde...6ef3c`).

The locked source closure has also been materialized twice in symlink-preserving
mode on fresh WSL ext4 directories. Both runs produced the same 30,436-entry
tree (29,238 files, 1,196 directories, and two symlinks), tree SHA-256
`05a19a0f...46ee6`, and byte-identical receipt SHA-256
`e2ea14b6...23c04`. `native/native-build-profile.toml` now binds that source
manifest and the composed toolchain to an API-26, `arm64-v8a`/`x86_64`,
16-KiB libmpv-stack profile. Its two reviewed overlays remove ambient SDK/NDK
selection and accept only the two fixed `mpv` commands. The profile preflight
passed against the retained preserve-mode source tree and locked composition.
The preparation command now publishes a fresh root-owned ext4 workspace with an
independent source copy, applies the two locked overlays there, and creates
empty disjoint output, HOME, and temporary directories. Its independent
verification passed without changing the canonical source. It did not execute
either build command or compile native code.

These WSL runs are inspection evidence rather than accepted release
provenance. The SDK tree is deliberately marked as a standalone mountable
projection, not a release input: its accepted release-input binding to the
verified APT environment, accepted Android license evidence, an accepted
release-builder source/build run and artifact audit, a wrapper-inclusive native
receipt, SBOM, system notices, retention/corresponding-source bundles, and
bootstrap-AAR retirement remain pending.

## Baseline

- Namespace: `io.github.joyelliot.zivplayer`
- Minimum Android version: API 26
- Compile and target SDK: API 37
- Initial ABIs: `arm64-v8a` and `x86_64`
- UI: Jetpack Compose with `compose-miuix-ui`
- Playback engine: libmpv
- License expression: `GPL-3.0-or-later`

Architecture decisions are recorded in [`docs/adr`](docs/adr).

## Build

The repository will use its checked-in Gradle Wrapper. A global Gradle
installation is neither required nor supported as the project build entry
point.

```powershell
.\gradlew.bat :apps:android:assembleDebug
```

Dependency versions, locks, and verification metadata are committed so that
the same source revision resolves the same reviewed dependency set.

The native source manifest has a separate explicit cache gate:

```powershell
python native/tools/source_tool.py validate
python native/tools/source_tool.py fetch
python native/tools/source_tool.py verify-cache
# Windows archive/placement inspection only; never a native release input.
python native/tools/materialize_sources.py `
  --workspace native/out/workspace-windows `
  --link-mode portable-copy
python native/tools/materialize_sources.py `
  --workspace native/out/workspace-windows `
  --verify-workspace `
  --allow-portable-copy
python -m unittest discover -s native/tests -v
```

The Linux native builder must instead use the default symlink-preserving mode
and run `--verify-workspace` without the portable-copy exception. Verification
rehashes the locked cache and complete tree, compares every ordinary source
file with its locked archive bytes, and rejects missing or extra entries. The
receipt is an integrity record, not a signature or a substitute for a fresh
trusted build.

```sh
sudo python3 native/tools/materialize_sources.py \
  --workspace /var/tmp/zivplayer-native-source \
  --link-mode preserve
sudo python3 native/tools/materialize_sources.py \
  --workspace /var/tmp/zivplayer-native-source \
  --verify-workspace
```

The separate native toolchain lock is validated and cached explicitly. `fetch`
downloads only the locked artifact/OCI roots; APT preparation is a distinct
networked pre-build step on the pinned Ubuntu preparation environment. The
actual native build must remain offline.

```sh
python3 native/tools/toolchain_tool.py validate
python3 native/tools/toolchain_tool.py fetch
python3 native/tools/toolchain_tool.py verify-roots
sudo /bin/sh native/toolchain/prepare-apt-cache.sh
python3 native/tools/toolchain_tool.py verify-cache
python3 native/tools/rootfs_tool.py preflight
sudo python3 native/tools/rootfs_tool.py materialize-base
sudo python3 native/tools/rootfs_tool.py verify-base
sudo python3 native/tools/environment_tool.py materialize-apt \
  --output /var/tmp/zivplayer-toolchain-apt
sudo python3 native/tools/environment_tool.py verify-apt \
  --rootfs /var/tmp/zivplayer-toolchain-apt
python3 native/tools/sdk_tool.py preflight
sudo python3 native/tools/sdk_tool.py materialize \
  --output /var/tmp/zivplayer-sdk-projection
sudo python3 native/tools/sdk_tool.py verify \
  --root /var/tmp/zivplayer-sdk-projection
sudo python3 native/tools/composition_tool.py preflight
sudo python3 native/tools/composition_tool.py compose-and-smoke
sudo python3 native/tools/composition_tool.py verify
python3 native/tools/native_build_tool.py validate
sudo python3 native/tools/native_build_tool.py preflight
python3 native/tools/native_executor_tool.py validate
python3 native/tools/native_build_executor_tool.py validate
python3 native/tools/toolchain_tool.py check-lock
```

The retained fourth inspection workspace named below is consumed and must not
be passed to `prepare`, `verify-preparation`, `probe`, `verify-inputs`, or
`execute` again. Commands that create or consume another one-shot workspace
are intentionally omitted from this reusable verification list; a later
attempt must use a new absent path and an explicitly reviewed execution plan.

The preparation script refuses to replace an existing ignored APT cache. Move
or remove that cache deliberately before regenerating it. `check-lock` is the
fail-closed release-input gate and currently exits with code 3 after byte
verification because the installed container and compliance bundles are still
pending. `verify-apt-cache`, `verify-cache`, and `check-lock` require Linux's
canonical `/usr/bin/gpgv` and `/usr/bin/dpkg-deb`; run them inside the
controlled Linux builder or WSL for inspection, not as native Windows commands.
The rootfs commands require an ext4 output and never replace an existing
destination. `rootfs_tool.py` handles the locked Ubuntu base layer;
`environment_tool.py` builds and verifies the offline installed APT stage. The
latter is Linux-root-only and also requires the locked cgroup-v2 controllers.
`sdk_tool.py` creates a separate root-only projection for the fixed
`/opt/zivplayer/toolchain` mount point. `composition_tool.py` leaves both trees
standalone and immutable, but binds them read-only in an ephemeral private
namespace for a fixed runtime profile. That profile checks the exact mount set,
loopback-only networking, zero capabilities, `no_new_privs`, the locked seccomp
filter and cgroup cleanup, then compiles API-26 `arm64` and `x86_64` shared
objects and verifies 16 KiB ELF LOAD alignment. Its no-replace receipt remains
`ready=false` and `releaseInput=false`: it does not generate `package.xml`,
accept Android licenses, complete notices/retention, build libmpv, or prove a
device release. Consequently `check-lock` intentionally remains closed.

`native_build_tool.py validate` checks the immutable profile/manifests and
overlay replacement bytes. Its Linux-root-only `preflight` additionally
requires a verified preserve-mode ext4 source workspace, checks the untouched
upstream overlay origins, rehashes the two exact NDK `libc++_shared.so` runtime
inputs, and re-verifies the composition receipt. `prepare` repeats those gates,
copies every ordinary source file to an independent private workspace while
preserving the two locked symlink texts, rejects hardlinks and nested mounts,
applies the overlays atomically, normalizes metadata, and publishes only after
a canonical preparation receipt passes verification. `verify-preparation`
recomputes that contract from the current locked inputs and published tree.
`native_executor_tool.py validate` checks a separate exact namespace-probe
policy, its build-profile binding, and four byte-locked launcher/namespace/
probe/seccomp helpers. Its Linux-root-only `probe` command pins and re-verifies
the immutable inputs, reserves pidfd capacity, applies the locked cgroup and
process limits, enters the private namespace with an empty inherited
environment plus fixed variables, and accepts only the exact six-record probe
transcript. The policy keeps build commands, artifact staging, build receipts,
readiness, and release input explicitly disabled. The probe itself never
authorizes build-command execution, artifact staging, ELF audit, JNI wrapper,
Gradle integration, or a build receipt.

The initial preparation of the fourth WSL inspection workspace at
`/var/tmp/zivplayer-native-build-298845ca4076-weak-audit` produced a
5,378-byte receipt with SHA-256
`e51e2e706c3488feabbe1b5482b4d0433db8c823a6a659f2b2483b25b5ff7116`.
The prepared post-overlay source retains 30,436 entries and has tree SHA-256
`72678d1096e844777a6bb7008fee5e3dd1690c0612a6854cdb293b10001d1602`.
An independent `verify-preparation` run passed. The receipt explicitly records
`buildExecuted=false`, `ready=false`, and `releaseInput=false`.

The canonical executor then passed the namespace-only WSL inspection probe
with policy SHA-256
`24a42f725bc174d41a7434d57c7078dd1f02a2cc27963202a8638909cb7276ce`
and transcript SHA-256
`fd251aeb116fd8ced374df5c9887af8dcc3bde03002b4968181421a1682b9f81`.
The accepted run left no executor cgroup or process; the output, HOME, and
temporary trees remained empty. It did not execute a compiler or `buildall.sh`.

The first real closed-loop attempt consumed the older
`/var/tmp/zivplayer-native-build` workspace under policy
`d0cf43cedfa73a41f4a4cd0aebe144321bbd01c2d7431c7035551d87f472e329`.
Its retained attempt marker has SHA-256
`f6e7fb1e87737c0361565f0e24a5cf43086201a2fc72a80b7f227986cb3931c8`.
The arm64 build completed mbedTLS and dav1d, then Meson 1.11 stopped at
`libxml2/meson.build:19` because the optional `git describe` command was absent
from the closed PATH. The failure left the output tree empty, published no
build receipt, and left no executor process or cgroup; the consumed workspace
remains intact for diagnosis.

The locked `buildall.sh` overlay now installs and re-verifies a 19-byte,
root-owned, mode-0500, single-link `git` stub whose only result is exit 127.
This allows optional source-snapshot version probes to fall back without adding
ambient Git or network access, while any genuinely required Git operation
still fails closed. That fix produced profile SHA-256
`ac17ad61199c3761d5cc484f5ec258a55912981f2d1af83ce7587ee91e013915`.

The second one-shot attempt consumed
`/var/tmp/zivplayer-native-build-ac17ad61199c` under build policy
`b1b0f9499bf7452ca5228f16e2371209cfc5fb7c1f58f7299d427164fee8302d`.
Its retained mode-0600 attempt marker has SHA-256
`87c30c328a24e0a4a517f4302d8267b2c82fa371b4ebcb79ae1ff9b305557af3`.
The Git stub worked and arm64 passed libxml2, FFmpeg, FreeType, fontconfig,
FriBidi, and HarfBuzz. It then stopped while installing libunibreak because
the relative `INSTALL=install` was rewritten to the nonexistent `../install`
inside its recursive Automake subdirectory. x86_64 was not started; output
remained empty, no build receipt was published, and process/cgroup cleanup
completed. The consumed workspace remains intact and was not rerun.

The path overlay now pins `INSTALL=/usr/bin/install`, the absolute GNU
coreutils binary inside the locked APT tree. A direct Autoconf probe confirmed
that both root and nested Makefiles preserve this absolute path. The current
profile SHA-256 is
`298845ca407684b0ec073036a78602972cbf971d8b9642a19476bf1c1f6ad4cc`.

The third one-shot attempt consumed
`/var/tmp/zivplayer-native-build-298845ca4076` under build policy
`eb671c17e47b4fc34b2d1974d0b5cbcb7fe8d24975b46100f606f61f559b91a3`.
Its retained mode-0600 attempt marker has SHA-256
`681a0de50104d6a02378d7df13e5fd56e026d4e3cc924de1f8993b8a622aba91`.
Both ABI commands completed and staging published the exact 18-library output
set (243,407,008 regular-file bytes), proving the Git and recursive-install
repairs. The post-build API-26 audit then stopped at the first AArch64 DSO
because the then-current policy required the weak undefined `memfd_create`
probe from NDK 29 compiler-rt to resolve through the API-26 closure. The
workspace retains those non-release staged artifacts, but no build receipt or
temporary receipt was published and all executor processes and cgroups were
removed.

The retained ELF evidence identifies `memfd_create` as
`NOTYPE WEAK DEFAULT UND` with
only `R_AARCH64_GLOB_DAT` in six AArch64 DSOs. The locked compiler-rt resolver
loads that GOT entry and branches away when it is zero; the Android 8.1 linker
contract zero-fills this unresolved weak relocation on API 26. The revised
audit therefore still resolves every strong undefined symbol through the
version-aware staged/API-26 closure, but permits
`NOTYPE WEAK DEFAULT UND memfd_create` only in AArch64 `libavcodec.so`,
`libavfilter.so`, `libavformat.so`, `libavutil.so`, `libmpv.so`, and
`libswscale.so`, and only with the exact `R_AARCH64_GLOB_DAT` relocation set.
Each accepted case is recorded per artifact; any other library, symbol type,
unresolved weak symbol, or relocation remains an error.

The refreshed closed-loop inspection-build contract remains separate from the
accepted probe policy. `native-build-executor-policy.toml` binds the exact
profile, preparation/composition receipts, accepted probe policy/transcript,
one-shot workspace state, two-command order, bounded logs, exact artifact
allowlist, API-26/ELF/16-KiB audits, and a canonical non-release build receipt.
Its policy SHA-256 is
`f9d7c4afe5cdf3ff701a9281c4ba92134a3c21512fb8f18e8ecc1addef9feafc`.
The tool exposes read-only `validate` and Linux-root `verify-inputs`, plus an
explicit one-shot `execute` that implements the complete marker, two-command
lifecycle, staging, audit, and non-release receipt transaction. Before the
fourth workspace was consumed, the complete WSL input check passed with
preparation receipt
`e51e2e706c3488feabbe1b5482b4d0433db8c823a6a659f2b2483b25b5ff7116`,
composition receipt
`e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989`,
and resolved ELF-tool digest
`5104576a3518575cf1887c2afa9249bbd0dc175cb9dc0f2af0d430fe0cb20bbe`.

The fourth one-shot attempt then completed successfully under the current
policy. Its canonical 1,434-byte, mode-0600 attempt marker has SHA-256
`44d94fe0c293b3480d316ca4603fc8e4842cb995a9967bcc0ad84bcf66854015`.
Both ABI commands ran in their fixed order and exited successfully; staging
published exactly 18 regular libraries totalling 243,407,008 bytes. The
complete structural audit passed, including the version-aware API-26 strong
symbol closure, 16-KiB load alignment, dependency/SONAME checks, and the six
exact AArch64 weak-reference records described above. The resulting canonical
72,674-byte non-release receipt has SHA-256
`7d5dc92f4d4ccd55f2340814d6bce071133560178f294c907a86f251dabd4d3e`
and records `buildExecuted=true`, `artifactStaged=true`, and
`artifactAudited=true`, while retaining `ready=false` and
`releaseInput=false`. All 18 outputs are byte-identical to the third attempt's
retained output, and post-run checks found no temporary receipt, executor
process, or cgroup. The receipt's exact pending blockers remain
`pending-source-wrapper`, `pending-offline-closure`,
`pending-actual-build-graph`, and `pending-release-gate`; these include the
accepted release-builder and compliance work still required for publication.

The source-side replacement is now checked in without changing that receipt or
selecting it in the running backend. `SourceMpvClient` owns one event thread and
a positive opaque token; an unexpected pump exit fails the active generation
with RESET.
The `libzivplayer_mpv` source declares 16 guarded methods registered from
`JNI_OnLoad`, copies supported event fields through a fixed primitive-buffer
ABI, preserves raw identifiers, and retains every Surface accepted by
asynchronous `wid` handling until final mpv termination. The executable
JNI-contract validator checks the top-level Kotlin object/direct instance
descriptors, fixed 16-method cardinality, guarded C++ definitions and scoped
`JNI_OnLoad` registration, exact wire-constant sets, CMake target/source
literals, and Release final-name properties; it does not run CMake or validate
its input gates, wire use sites, compiled/R8 identity, included-header or
toolchain macro effects, ELF metadata, or runtime behavior. This source has passed
JVM lifecycle tests and two-ABI syntax checks only: it is not yet built with the
locked NDK, not staged into Gradle, not selected by `LibmpvBackend`, and not
device or release evidence. The historical receipt therefore correctly
continues to report `pending-source-wrapper` until a new additive
wrapper-inclusive profile rebuilds and audits all ten libraries per ABI.

Two final WSL inspection runs of the fixed composition profile produced
byte-identical receipts with SHA-256
`e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989`.
The smoke transcript SHA-256 was
`c491134a712d88a1d0d76e96eb39494a623cb61fb818ad962e286a112d19e60c`.
These are inspection results under an exclusive root-controlled host boundary,
not accepted release provenance.

See [`native/README.md`](native/README.md),
[`ADR 0010`](docs/adr/0010-source-built-libmpv-and-native-provenance.md), and
[`ADR 0011`](docs/adr/0011-typed-per-instance-libmpv-jni-bridge.md). The native
cache is ignored; source identity remains reviewable in the committed manifest.

The local M0-M6 quality gate is:

```powershell
.\gradlew.bat -p build-logic build

.\gradlew.bat :core:model:test `
  :core:media-api:test `
  :core:player-api:test `
  :core:player-runtime:test `
  :data:media-android:assembleDebug `
  :data:media-android:assembleRelease `
  :data:media-android:testDebugUnitTest `
  :data:media-android:lintDebug `
  :feature:player:assembleDebug `
  :feature:player:assembleRelease `
  :feature:player:testDebugUnitTest `
  :feature:player:lintDebug `
  :platform:libmpv-android:assembleDebug `
  :platform:libmpv-android:assembleRelease `
  :platform:libmpv-android:testDebugUnitTest `
  :platform:libmpv-android:lintDebug `
  :platform:playback-android:assembleDebug `
  :platform:playback-android:assembleRelease `
  :platform:playback-android:testDebugUnitTest `
  :platform:playback-android:lintDebug `
  :ui:design-system-miuix:assembleDebug `
  :ui:design-system-miuix:assembleRelease `
  :ui:design-system-miuix:lintDebug `
  :apps:android:assembleDebug `
  :apps:android:assembleRelease `
  :apps:android:compileDebugAndroidTestKotlin `
  :apps:android:testDebugUnitTest `
  :apps:android:lintDebug
```

Running the instrumented Compose and document-contract tests themselves requires
a connected Android device or configured emulator. Dependency lock and checksum
regeneration is an explicit dependency-review operation, not part of an ordinary
build.

## License

ZivPlayer is licensed under the GNU General Public License, version 3 or (at
your option) any later version. See [`LICENSE`](LICENSE). Source files created
for this project use the SPDX identifier `GPL-3.0-or-later`.

Bundled and linked third-party components retain their own copyright and
license terms. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
