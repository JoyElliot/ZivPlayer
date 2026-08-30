# ADR 0006: The bootstrap libmpv AAR is not a release artifact

- Status: Accepted
- Date: 2026-08-30

## Context

The Maven Central artifact `dev.jdtech.mpv:libmpv:1.0.0` is useful for early
Android integration. Its reviewed AAR has SHA-256
`df146592480fc8418415a06b1f1a1d6318b0088e21f52254b0e9a82b61ca8fa2`,
declares minimum SDK 26, and contains native libraries for four Android ABIs.
The wrapper is MIT-licensed, but the AAR also embeds mpv, FFmpeg, and their
native dependency stack, which are not described by that wrapper license.

The wrapper also narrows the native API: command and property write failures
are only logged, end-of-file callback details are discarded, observer
callbacks arrive on a native thread, and `destroy()` is not idempotent at the
Kotlin layer. Event payloads and playlist-entry identity are also discarded.

## Decision

- Use version 1.0.0 only as a locked bootstrap artifact behind
  `platform:libmpv-android`.
- Package only `arm64-v8a` and `x86_64` in the initial application even though
  the upstream AAR contains four ABIs.
- Never expose the wrapper, raw event IDs, property names, or native thread to
  core or feature modules.
- Treat one adapter instance as the sole owner of create/init/callback/destroy
  ordering and invoke destroy at most once.
- Do not ship a public release from this binary. Before release, build the
  wrapper, libmpv, FFmpeg, and the native dependency stack from locked source
  revisions in ZivPlayer CI, preserve detailed failure events, and publish the
  complete corresponding-source and license bundle.

## Consequences

M3 can compile and test the typed boundary, and M4 can use the artifact for
device playback bring-up. Successful bootstrap playback is not evidence that
the native release supply chain, error fidelity, HDR path, or license package
is complete. The bootstrap adapter can fence generations only under its single
instance and serialized stop/load assumptions; this is not release-grade
callback attribution. The source-built wrapper must expose native entry IDs,
end reasons, and operation results before public release.
