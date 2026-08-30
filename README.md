# ZivPlayer

ZivPlayer is an Android-first media player focused on broad format support,
high-quality rendering, and advanced subtitles.

## Project status

The M0-M3 foundation is complete: architecture and licensing decisions are
recorded, the Gradle dependency supply chain is locked and verified, the
Android application builds with a MIUIX-backed Compose shell, and a pure
Kotlin player contract now drives a serialized runtime with core generation
filtering.

The reviewed libmpv AAR is present only behind the Android adapter module. It
is not yet connected to the application APK. Surface rendering, foreground
playback ownership, and real-device media validation begin in M4.

The bootstrap adapter fences callbacks only under a single-instance,
serialized stop/load policy. Its reviewed AAR discards event payloads and
playlist-entry identity, so this is not a release-grade stale-callback
guarantee. Native callbacks enter one ordered adapter event stream, and an
accepted load that never prepares or fails is terminated by a bounded runtime
readiness deadline instead of remaining in `LOADING` indefinitely. A reset
error makes that session non-reusable; its Android owner must close and
recreate the backend/session pair.

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

The local M0-M3 quality gate is:

```powershell
.\gradlew.bat -p build-logic build

.\gradlew.bat :core:model:test `
  :core:player-api:test `
  :core:player-runtime:test `
  :platform:libmpv-android:assembleDebug `
  :platform:libmpv-android:assembleRelease `
  :platform:libmpv-android:testDebugUnitTest `
  :platform:libmpv-android:lintDebug `
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
