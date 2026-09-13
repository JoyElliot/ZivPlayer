# LibVLC 4 seek event bridge

This experiment uses the official `org.videolan.android:libvlc-all:4.0.0-eap29` AAR. It does not rebuild VLC. The C bridge resolves only the pinned public player APIs and retains the player until its time watcher is removed. `on_seek(non-null)` begins a seek; `on_seek(null)` completes it. Callbacks only enqueue Java work because VLC holds its timer lock during notification.

Gradle builds this directory through `platform/libmpv-android` with NDK 26.1.10909125 / CMake 3.22.1 for the two 64-bit ABIs. The accepted sources and outputs are pinned in `tools/libvlc4-experiment-lock.json`. The independent bridge lock does not modify the original libmpv staging receipt.

The host test compiles the actual bridge against deterministic JNI/VLC fixtures. Run from the repository root in a Linux environment with a C compiler and ASan/UBSan:

```sh
mkdir -p native/out/libvlc4-host-test
cp "$ANDROID_NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include/jni.h" native/out/libvlc4-host-test/
sh native/libvlc4-seek/tests/run-host.sh native/out/libvlc4-host-test
```

For WSL with the Windows NDK, copy that installation's standalone `jni.h` to the output directory instead. The test does not need a JVM, Android device or real VLC installation. It validates the bridge ownership/token logic and native-thread callback route; actual VLC timing, Android decoding and Surface presentation remain device checks.
