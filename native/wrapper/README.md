# ZivPlayer libmpv JNI wrapper

This directory contains the source-only `libzivplayer_mpv.so` bridge specified
by ADR 0011. No backend factory selects it, and it is not a Gradle or release
input yet. The additive build profile now prepares these sources, but that
preparation is not compiled or release evidence.

`native/native-wrapper-build-profile.toml` snapshots six exact inputs into the
host `<build-workspace>/wrapper` tree. The accepted wrapper-specific namespace
probe binds that tree read-only at `/build/wrapper`; the future build executor
must preserve that contract. Its locked execution path is
Android NDK 29.0.14206865 `ndk-build` through `Android.mk` and
`Application.mk`, with API 26, `arm64-v8a`/`x86_64`, shared libc++, Release,
and 16-KiB linker settings. It builds separately against each audited stack
prefix and installs `libzivplayer_mpv.so` beside the existing nine libraries.

`CMakeLists.txt` is retained as a shallow source-contract/reference input. It
records the same NDK/API/ABI/STL/build-type/prefix restrictions and direct mpv
and FFmpeg inputs, but the selected profile does not run CMake. Neither build
file is provenance by itself: the eventual executor and compiled audit must
prove the exact input hashes, ABI identity, full dependency closure, explicit
SONAME, 16-KiB LOAD alignment, and JNI exports on the resulting ELF.

The first wire exposes synchronous property getters only; asynchronous
`GET_PROPERTY_REPLY` payloads are not decoded. Native strings are converted to
Java UTF-16 with invalid UTF-8 replaced by U+FFFD, so the v1 metadata wire is
safe but not byte-preserving. Android `Surface` global references which have
successfully reached mpv's asynchronous `wid` option are retained until mpv is
fully terminated.

The additive profile, verified preparation, and a non-building wrapper namespace
probe now exist; the build runner and compiled audit do not. The accepted probe
policy SHA-256 is
`e38767eb0e8e095364d13040a9ce3f49479f9147e1a2740d4f81860c496d1647`
and its exact transcript SHA-256 is
`e182d70ac1a76b00f7f3a622835c23621ba3df4654a339b945f66094f959dc9f`.
The preparation receipt therefore remains `buildExecuted=false`, `ready=false`,
and `releaseInput=false`. The next build executor must audit ten libraries per
ABI (20 in total), validate this registration table against the compiled Kotlin
descriptors, and keep `ready=false` / `releaseInput=false` until all staging,
Gradle, compliance, and device gates pass. Do not amend the historical
stack-only receipt or package this source tree directly from Gradle.

`jni-contract.toml` is the checked-in registration and event-buffer ABI.
Validate it against both implementations with:

```text
python3 native/tools/wrapper_contract_tool.py \
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
those remain separate `ndk-build`, compiled-audit, and device gates.
