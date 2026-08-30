# ADR 0009: Single-item playback UI and controller-owned state

- Status: Accepted
- Date: 2026-08-31

## Context

The M0-M5 foundation can open one document, render through a service-owned
libmpv instance, expose Media3 system controls, and persist local history and
progress. The Compose screen still shows only a video surface and an open
button. It neither observes ongoing `MediaController` state nor exposes the
typed commands already supported by the playback adapter.

The current Android adapter intentionally accepts one media item. It does not
project track descriptors or implement queue editing, track selection, or
subtitle controls. The first useful player screen must represent those limits
honestly while keeping Media3 and MIUIX out of feature logic.

## Decision

- `feature:player` owns immutable, Android-free UI models, formatting policy,
  and the stateless player screen. It receives state and callbacks and does not
  import Media3 or MIUIX directly.
- The application-scoped side of the Activity ViewModel owns one
  application-context `MediaController`. A stable `Player.Listener` snapshots
  metadata, status, commands, duration, repeat, speed, and volume on the main
  application looper. A lightweight ticker samples position while connected
  because Media3 has no continuous position callback.
- Controller connection failure and disconnection are visible UI states. A
  disconnected controller is terminal and is replaced through a bounded,
  generation-fenced reconnect loop. Stale futures and listeners cannot publish
  state after a newer connection wins.
- Every UI action checks the currently advertised Media3 command before
  calling the controller. The first screen exposes open, play/pause/replay,
  stop, seek, playback speed, volume, and repeat-off/repeat-one. Queue editing,
  next/previous, repeat-all presentation, shuffle, track selection, subtitles,
  chapters, fullscreen, picture-in-picture, and video tuning are deferred.
- The direct `SurfaceView` path and service-owned Surface lease remain
  unchanged. Compose presents status and metadata around the surface but never
  owns a native player or treats a Surface as durable state.
- The recent section contains only documents explicitly opened through the
  Storage Access Framework. Reopening a row goes through
  `MediaDocumentRegistrar` again so current provider access is checked. An
  incomplete checkpoint supplies the single item's start position; a completed
  checkpoint starts from zero. A revoked or missing document is a recoverable
  UI error, not proof of database corruption.
- Forgetting history uses the registrar result so an orphaned persisted grant
  can be reported rather than hidden. Session-only selections never appear as
  restart-safe history.
- MIUIX remains isolated behind ZivPlayer design-system wrappers. Controls
  expose text, enabled state, range semantics, and touch targets suitable for
  Compose accessibility tests without adding another UI toolkit or icon
  dependency.

## Consequences

The screen follows one authoritative path from the core session through the
Media3 controller to immutable Compose state. Activity recreation preserves
the controller, while a real disconnect produces an explicit reconnecting
state instead of leaving a one-shot listener suspended.

Host-side tests can cover formatting and UI-state policy, and existing Compose
instrumentation dependencies can cover labels, enabled actions, and slider
semantics. A device or configured emulator remains required to prove real
Surface presentation, controller reconnection, SAF reopening and revocation,
checkpoint resume after process death, foreground notification behavior, audio
focus, and accessibility at large font scales.
