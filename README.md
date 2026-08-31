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

Two fresh WSL inspection builds from the same locked inputs produced identical
12,449-entry trees (`b584b9cf...f10426`) and identical canonical receipts
(`4ece2c1c...aeb91`). The locked Android SDK/NDK and Meson wheel can now also be
projected directly, without `sdkmanager`, `pip`, or network access, into a
fresh Linux/ext4 tree. The projection preserves the NDK's audited symlinks and
case-sensitive headers, validates the wheel RECORD, and writes an independently
verifiable receipt before atomic publication. Two fresh complete-cache
inspection runs produced byte-identical receipts (SHA-256
`7f9bf9d6...6e544`) and the same 25,286-entry, 2,913,084,578-byte tree
(`dac18763...1c5a4`; projection digest `d5becfde...6ef3c`).

These WSL runs are inspection evidence rather than accepted release
provenance. The SDK tree is deliberately marked as a standalone mountable
projection, not a release input: binding it to the verified APT environment,
accepted Android license evidence, canonical Linux source materialization,
source-built wrapper and libraries, ELF audit, SBOM, system notices, retention/
corresponding-source bundles, and bootstrap-AAR retirement remain pending.

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
python3 native/tools/toolchain_tool.py check-lock
```

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

Two final WSL inspection runs of the fixed composition profile produced
byte-identical receipts with SHA-256
`e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989`.
The smoke transcript SHA-256 was
`c491134a712d88a1d0d76e96eb39494a623cb61fb818ad962e286a112d19e60c`.
These are inspection results under an exclusive root-controlled host boundary,
not accepted release provenance.

See [`native/README.md`](native/README.md) and
[`ADR 0010`](docs/adr/0010-source-built-libmpv-and-native-provenance.md). The
native cache is ignored; source identity remains reviewable in the committed
manifest.

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
