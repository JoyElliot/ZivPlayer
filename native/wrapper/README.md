# ZivPlayer libmpv JNI wrapper

This directory contains the source-only `libzivplayer_mpv.so` bridge specified
by ADR 0011. No backend factory selects it, and it is not a Gradle or release
input yet.

The wrapper is built separately against one per-ABI prefix whose provenance is
audited by the later build profile. Configure
the Android NDK 29.0.14206865 CMake toolchain with API 26, one of
`arm64-v8a`/`x86_64`, `-DCMAKE_BUILD_TYPE=Release`,
`-DANDROID_STL=c++_shared`, and
`-DZIVPLAYER_NATIVE_PREFIX=<absolute-prefix>`. The full NDK revision is read
from `source.properties`; CMake's shortened `CMAKE_ANDROID_NDK_VERSION` is not
used as provenance. Configuration rejects missing, directory, or symlinked mpv
and FFmpeg header paths plus `libmpv.so` and `libavcodec.so`. This is
only a shallow direct-input check: the additive wrapper profile must still bind
hashes, ABI identity, the complete nine-library stack, and ELF dependency
closure. The target registers the exact Kotlin table from `JNI_OnLoad`,
uses an explicit `libzivplayer_mpv.so` SONAME, and requests 16-KiB load
alignment. Those declarations still require verification on the resulting ELF.

The first wire exposes synchronous property getters only; asynchronous
`GET_PROPERTY_REPLY` payloads are not decoded. Native strings are converted to
Java UTF-16 with invalid UTF-8 replaced by U+FFFD, so the v1 metadata wire is
safe but not byte-preserving. Android `Surface` global references which have
successfully reached mpv's asynchronous `wid` option are retained until mpv is
fully terminated.

The next native-build profile must copy the resulting library into that same
prefix, audit ten libraries per ABI, validate this registration table against
the compiled Kotlin descriptors, and keep `ready=false` / `releaseInput=false`
until the full staging and device gates pass. Do not amend the historical
stack-only receipt or package this source tree directly from Gradle.

`jni-contract.toml` is the checked-in registration and event-buffer ABI.
Validate it against both implementations with:

```text
python native/tools/wrapper_contract_tool.py \
  --contract native/wrapper/jni-contract.toml \
  --cpp native/wrapper/zivplayer_mpv.cpp \
  --kotlin platform/libmpv-android/src/main/kotlin/io/github/joyelliot/zivplayer/platform/libmpv/MpvNativeBindings.kt \
  --cmake native/wrapper/CMakeLists.txt
```

This source check validates the top-level Kotlin object and its direct instance
methods, the guarded C++ definitions/signatures/registration path, exact buffer
constant sets, fixed 16-method cardinality, exact CMake target/source literals,
and Release final-name properties. Mutation tests cover scope and decoy attacks,
forbidden preprocessor directives, and C++ line splicing. The
validator permits only the wrapper's fixed system/audited-input include list and
rejects source `#define`, `#undef`, and conditional-compilation directives in
this JNI translation unit. It does not run CMake or prove the NDK/API/ABI/STL/
build-type/prefix gates, included-header or toolchain macro effects, wire use-site
semantics, compiled/R8 descriptors, JNI runtime behavior, or ELF properties;
those remain separate compiled and device gates.
