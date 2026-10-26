# LibVLC 4 seek event bridge

This experiment uses the official `org.videolan.android:libvlc-all:4.0.0-eap29` AAR. It does not rebuild VLC. The C bridge resolves only pinned public player APIs and retains the player until its time watcher is removed. Video/playing seeks require `on_seek(non-null)`, `on_seek(null)`, then a valid timer update; a PCR reset can send the null event before the new clock value. Paused audio has no output update, so its native seek-end acknowledges the logical cursor. Timer points are copied using the pinned public struct and read through `time_point_interpolate`, with the pause date retained. Updates can come from input/demux as well as output and are not independent proof of a physical displayed frame. Callbacks only enqueue Java work because VLC holds its timer lock during notification.

Fresh paused players do not guarantee a first time point or an initialized video output. For a resumed position or a recreated paused Surface, the Kotlin backend starts decoding with volume zero, seeks after Playing, requests pause after the real seek time point, and publishes readiness/restores volume only after Paused. Ordinary seeks keep the same actual time-point completion rule and bounded timeout. The diagnostic next-frame request was removed: requesting it before an output exists did not resolve startup and introduced an unnecessary extra-step race. No player API is called from a native timer callback; the 100 ms watcher period stays unchanged.

Gradle builds this directory through `platform/libmpv-android` with NDK 26.1.10909125 / CMake 3.22.1 for the two 64-bit ABIs. The accepted sources and outputs are pinned in `tools/libvlc4-experiment-lock.json`. The independent bridge lock does not modify the original libmpv staging receipt.

The host test compiles the actual bridge against deterministic JNI/VLC fixtures. Run from the repository root in a Linux environment with a C compiler and ASan/UBSan:

```sh
mkdir -p native/out/libvlc4-host-test
cp "$ANDROID_NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include/jni.h" native/out/libvlc4-host-test/
sh native/libvlc4-seek/tests/run-host.sh native/out/libvlc4-host-test
```

For WSL with the Windows NDK, copy that installation's standalone `jni.h` to the output directory instead. The test does not need a JVM, Android device or real VLC installation. It validates the bridge ownership/token logic and native-thread callback route; actual VLC timing, Android decoding and Surface presentation remain device checks.

Pinned contracts: [public seek/time watcher](https://github.com/videolan/vlc/blob/c84ef1eda4f14f8b57a5b266b51c843cf51ed28b/include/vlc/libvlc_media_player.h#L3124) and [paused timer update condition](https://github.com/videolan/vlc/blob/c84ef1eda4f14f8b57a5b266b51c843cf51ed28b/src/player/timer.c#L109).
