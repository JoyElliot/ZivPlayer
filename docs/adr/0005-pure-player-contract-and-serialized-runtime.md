# ADR 0005: Pure player contract with a serialized runtime

- Status: Accepted
- Date: 2026-08-30

## Context

The playback engine is asynchronous: commands, native callbacks, queue changes,
and Android lifecycle events can race with one another. Exposing native or
Android types in the player contract would also prevent reuse outside the
initial Android application.

## Decision

- Split the player foundation into `core:model`, `core:player-api`, and
  `core:player-runtime`, with dependencies flowing in that order.
- Keep all three modules free of Android, Compose, Media3, JNI, and libmpv
  types and property names.
- Expose one immutable `StateFlow` snapshot as the authoritative state and a
  separate `Flow` of transient semantic events.
- Serialize commands and backend events through one runtime-owned actor. Only
  that actor may mutate playback state or call the backend.
- Assign every load a monotonically increasing generation. Platform adapters
  attach it only to callbacks they can correlate and drop uncorrelatable
  callbacks; core ignores events from obsolete generations.
- Serialize native seeks as one in-flight operation, coalesce newer targets,
  carry a seek generation on completion and position events, and preserve
  native callback order so a delayed position cannot restore a pre-seek value.
- Require the backend event flow to remain alive until shutdown. Unexpected
  completion or failure is a terminal transport error; the runtime never starts
  another load on a dead event collector.
- Require every accepted load to produce `Prepared` or `Failure` before a
  bounded readiness deadline. A missed deadline is a terminal reset error, and
  callbacks from that abandoned generation are ignored.
- Treat `ErrorRecovery.RESET` as a hard ownership boundary: the current
  session cannot load or control the backend again and its owner must close it
  before creating a fresh backend/session pair.
- Keep the published `available` capability set consistent with commands that
  the runtime will accept, including play/pause intent changes while loading
  and replay after an unprepared load was stopped.
- Model backend failures with ZivPlayer-owned error kinds and sanitized text;
  native exceptions and implementation-specific error names remain inside the
  platform adapter.
- Define `Stop` as unloading the active backend while retaining the logical
  queue and rewinding its position; a later `Play` creates a fresh load.
  `ClearQueue` additionally removes the logical queue.
- Make session shutdown idempotent and ensure the backend is closed exactly
  once after all commands admitted before shutdown have completed.

## Consequences

The core state machine can be verified with deterministic JVM tests and later
moved to Kotlin Multiplatform without carrying Android APIs with it. Platform
adapters must translate their callbacks and failures into the typed backend
port. The event flow is transient; consumers must reconstruct current state
from the snapshot rather than relying on replayed events.
