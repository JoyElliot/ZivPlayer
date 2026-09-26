# ADR 0001: Android Kotlin application with an isolated MIUIX design system

- Status: Accepted
- Date: 2026-08-30
- Updated: 2026-09-26; [ADR 0019](0019-feishu-home-and-glass-navigation.md) raises the development app minimum to API 33 and adopts MIUIX blur for home navigation.

## Context

ZivPlayer is intended to become cross-platform, but the first deliverable is a
native Android application. Prematurely enabling Kotlin Multiplatform for the
entire repository would add source-set, plugin, and native-build constraints
before a second platform exists.

The selected UI library is the community `compose-miuix-ui` project. It is not
an official Xiaomi SDK and its public API is explicitly experimental.

## Decision

- Build the Android application with Kotlin and Jetpack Compose.
- Pin `compose-miuix-ui` to stable version `0.9.3` for the initial baseline.
- Consume the Android artifact through a dedicated design-system module.
- Feature modules use ZivPlayer-owned theme and component wrappers rather than
  importing MIUIX throughout the application.
- Keep domain and playback contracts free of Android and UI types so they can
  move to Kotlin Multiplatform when a real second platform starts.
- Do not support Android versions below API 26.

## Consequences

MIUIX upgrades are localized to one module, and a future replacement does not
rewrite feature logic. At the time of this initial decision, `miuix-blur` was
deferred because it requires API 33, while the navigation modules were changing
between the stable and release-candidate lines. [ADR 0019](0019-feishu-home-and-glass-navigation.md)
now adopts blur and raises the app/UI minimum to API 33.
