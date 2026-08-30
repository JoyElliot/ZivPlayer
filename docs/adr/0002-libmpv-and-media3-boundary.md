# ADR 0002: libmpv is the playback engine; Media3 is a system adapter

- Status: Accepted
- Date: 2026-08-30

## Context

The product requires broad format support, high-quality GPU rendering, HDR
processing, and complete ASS/SSA subtitle behavior. Running two independent
playback engines would duplicate state, codec stacks, and failure modes.

## Decision

- libmpv is the only playback engine.
- Do not include `media3-exoplayer`.
- Use AndroidX Media3 Session APIs only for media controls, notifications,
  headset/system commands, and foreground playback service integration.
- Adapt the typed ZivPlayer session to Media3 through a custom
  `SimpleBasePlayer` implementation.
- A single playback service owns the mpv handle, event pump, render context,
  and native shutdown sequence. Activities, composables, and view models never
  own native player instances.
- Keep `Surface`, `SurfaceView`, Media3, JNI, and mpv property names outside the
  pure Kotlin player contracts.

## Consequences

The first playback milestone must validate Surface attach/detach, rotation,
process lifecycle, and event ordering. `mediacodec_embed` is not the default
video output because it bypasses the subtitle, OSD, and filter path required by
the product.
