# ZivPlayer

ZivPlayer is an Android-first media player focused on broad format support,
high-quality rendering, and advanced subtitles.

## Project status

The host-side M0-M4 foundation is complete: architecture and licensing decisions are
recorded, the Gradle dependency supply chain is locked and verified, the
Android application builds with a MIUIX-backed Compose shell, and a pure
Kotlin player contract now drives a serialized runtime with core generation
filtering. A Media3 `MediaSessionService` owns that runtime and the single
libmpv backend; the activity connects only through a `MediaController` and
hands short-lived video `Surface` instances to the service.

The reviewed bootstrap libmpv AAR is now packaged behind the Android adapter
module and connected to the application APK. JVM and Android build checks
cover state projection, command policy, reset recovery, Surface lease ordering,
and service manifest composition. A physical device or configured emulator is
still required to prove actual media output, foreground notification behavior,
Surface recreation, audio focus, and native shutdown.

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

The local M0-M4 quality gate is:

```powershell
.\gradlew.bat -p build-logic build

.\gradlew.bat :core:model:test `
  :core:player-api:test `
  :core:player-runtime:test `
  :platform:libmpv-android:assembleDebug `
  :platform:libmpv-android:assembleRelease `
  :platform:libmpv-android:testDebugUnitTest `
  :platform:libmpv-android:lintDebug `
  :platform:playback-android:assembleDebug `
  :platform:playback-android:assembleRelease `
  :platform:playback-android:testDebugUnitTest `
  :platform:playback-android:lintDebug `
  :apps:android:assembleDebug `
  :apps:android:assembleRelease `
  :apps:android:compileDebugAndroidTestKotlin `
  :apps:android:testDebugUnitTest `
  :apps:android:lintDebug
```

Running the instrumented smoke test itself requires a connected Android device
or configured emulator. Dependency lock and checksum regeneration is an
explicit dependency-review operation, not part of an ordinary build.

## License

ZivPlayer is licensed under the GNU General Public License, version 3 or (at
your option) any later version. See [`LICENSE`](LICENSE). Source files created
for this project use the SPDX identifier `GPL-3.0-or-later`.

Bundled and linked third-party components retain their own copyright and
license terms. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
