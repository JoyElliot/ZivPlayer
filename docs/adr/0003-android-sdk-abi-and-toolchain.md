# ADR 0003: Android SDK, ABI, and toolchain baseline

- Status: Accepted
- Date: 2026-08-30

## Decision

The initial project baseline is:

| Item | Baseline |
| --- | --- |
| Namespace and application ID | `io.github.joyelliot.zivplayer` |
| Minimum SDK | 26 |
| Compile SDK | 37 |
| Target SDK | 37 |
| Initial packaged ABIs | `arm64-v8a`, `x86_64` |
| Gradle runtime JDK | 21 |
| Android Gradle Plugin | 9.3.2 |
| Gradle Wrapper | 9.6.1 |
| Kotlin and Compose compiler plugin | 2.4.0 |
| Android Build Tools | 37.0.0 |

This tuple follows the upstream stable MIUIX 0.9.3 build baseline except for
the Android Gradle Plugin. ZivPlayer uses AGP 9.3.2 because it contains the
upstream fix for Gradle's deprecated `Project` dependency notation used
internally by AGP 9.2.1. It remains a project-owned compatibility decision and
must pass repository builds; an upstream build file is not evidence that the
complete ZivPlayer dependency set is compatible.

The bootstrap libmpv AAR can be consumed without installing a local NDK. ADR
0010 now defines the inspected source-built libmpv pipeline: Linux, NDK 29,
Meson/Ninja, Autoconf/Automake/libtool/Make, `pkg-config`, and `ndk-build`. It
supersedes this ADR's earlier CMake 4.1.2 placeholder for that pipeline because
the selected upstream build does not use CMake.

## Consequences

Lower Android releases and 32-bit ABIs are outside the initial compatibility
scope. Adding `armeabi-v7a` later requires a separate size, performance, native
artifact, and device-test decision.
