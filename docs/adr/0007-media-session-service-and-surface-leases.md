# ADR 0007: MediaSessionService owns playback and render leases

- Status: Accepted
- Date: 2026-08-31

## Context

The pure player runtime requires one serialized backend owner, while Android
system controls require a Media3 `Player` hosted in a foreground-capable media
service. Android `Surface` instances are short-lived across view recreation
and can be destroyed after a replacement is already attached. The bootstrap
libmpv wrapper also invokes observers on its native event thread and does not
report Surface attach, detach, command, or property-write failures.

## Decision

- `platform:playback-android` owns the Android playback composition root:
  `LibmpvBackend`, `DefaultPlayerSession`, the custom `SimpleBasePlayer`, and
  `MediaSession` are created by one `MediaSessionService`.
- An Activity-scoped ViewModel connects with one application-context
  `MediaController` and retains it across configuration recreation. Activities,
  composables, features, and the application object never construct or retain
  a native mpv handle.
- Media3 remains a system adapter. Only commands implemented by the typed
  ZivPlayer contract are advertised, and `media3-exoplayer` remains excluded.
- Every mutable Media3 handler future passes through one adapter operation
  gate. Media replacement, reset rebuild, Surface lease changes, and shutdown
  therefore cannot overlap even when `SimpleBasePlayer` has several pending
  futures; ordinary queued work is rejected after shutdown begins.
- A Storage Access Framework `content://` selection is resolved inside the
  service to a read-only process file descriptor. The descriptor remains open
  for the active item while libmpv borrows `fd://<n>` and is closed only after
  replacement, reset, or shutdown. Reopening `/proc/self/fd/<n>` can fail Android
  access checks even when the provider granted a readable descriptor; `fdclose://`
  would incorrectly transfer ownership to mpv and prevent safe replay.
- A direct `Surface` reaches the service through Media3's video-output command.
  Media3 1.11's experimental legacy Surface handling remains enabled because
  its modern Binder path wraps the output in a `SurfaceHolder`, which this
  direct-Surface-only adapter intentionally rejects. The libmpv adapter returns
  an opaque, owner-bound lease. Replacing a Surface detaches the prior target
  before attaching the new one; a stale or foreign lease cannot detach the
  current target.
- Every ordinary MPV wrapper call is serialized by one native gate in addition
  to the core session actor. Shutdown first quiesces the adapter under that
  gate, then removes the observer and destroys the native instance outside the
  gate. This avoids lock inversion with the wrapper's observer monitor and its
  event-thread join.
- A backend failure classified as `RESET` suppresses commands that would touch
  the unusable engine but retains Media3's set-media-item and Surface commands.
  Surface calls update only the desired output while reset is pending. The
  next item closes the old core/backend pair, creates a replacement under the
  same Media3 player, and then attaches the newest still-valid direct Surface.
- The service declares the media-playback foreground-service type and required
  foreground-service permissions. Media3 owns the system notification/session
  integration; ZivPlayer does not introduce a second playback engine.
- Service destruction starts one shared player-shutdown completion without
  blocking Android's main thread. Potentially blocking native detach and close
  work runs off the application Looper; player, MediaSession, and Android
  lifecycle release remain independent, and asynchronous failures are logged.

## Consequences

Activity recreation and background transitions preserve the service-owned
player and Activity-scoped controller while replacing only the view and render
lease. A process-wide Surface token also guards disposal across distinct Activity
instances: only the current token may issue Media3's null-output clear. This
preserves the new target when an older Activity disposes its own controller's
Surface. Local JVM tests can verify command/state projections and lease
ordering, while an Android device is still required to prove foreground
notification behavior, real Surface rendering, rotation, audio focus, and
native shutdown.

The bootstrap AAR cannot confirm whether a native `wid`, command, or property
write succeeded. A returned Surface lease therefore proves ordering and
ownership, not successful GPU presentation. That limitation remains subject
to ADR 0006 and must be removed by the source-built release wrapper.

Likewise, Java/Kotlin cancellation cannot force a native `destroy()` call that
never returns. Service teardown therefore protects Android's main thread and
logs the asynchronous result, but a permanently hung native teardown can still
retain that engine until the process exits. Device stress coverage and the
source-built wrapper remain required before release.
