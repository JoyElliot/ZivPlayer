# ADR 0008: Durable media identity and local history

- Status: Accepted
- Date: 2026-08-31

## Context

The playback bootstrap currently uses a selected URI string as both the media
and queue identifier. Inside the playback service, a `content://` URI is opened
as a process-local file descriptor and libmpv borrows `fd://<n>`.
Neither a queue occurrence nor that transport locator is a durable media
identity. Persisting either would attach history and resume state to the wrong
object after queue changes or process restart.

The initial UI only lets the user explicitly choose audio or video through the
Storage Access Framework. It does not scan MediaStore, folders, or network
sources. The data model should follow that product boundary instead of
pretending that a complete media library already exists.

## Decision

- `core:media-api` defines pure Kotlin records and repository contracts. It is
  free of Android, Room, Media3, Compose, native, and file-descriptor types.
- The first local catalog contains only media explicitly opened through
  `OpenDocument`. MediaStore scans, folder indexing, network sources, playlists,
  favorites, and file-fingerprint reconciliation are deferred.
- The first import of a durable source URI receives an opaque UUID `MediaId`.
  Reopening the same exact source URI reuses that ID. A source URI is stored
  separately and uniquely; `QueueItemId` remains an occurrence ID and is never
  a database key.
- Android persists the original `content://` URI. It never stores
  `fd://`/`fdclose://` locators, `/proc/self/fd` paths, `ParcelFileDescriptor`, `Surface`, HTTP authorization
  headers, or an Android `Uri` object in the database.
- A failed persistable read grant may still permit one temporary playback, but
  that selection is not promised as restart-safe history. Access is rechecked
  with the current `ContentResolver` when a stored item is opened; database
  state is not treated as proof that a provider or grant still exists.
- Room owns media rows and playback checkpoints. DataStore remains reserved for
  small global preferences and does not serialize media records as JSON.
- Room schema version 1 is exported and committed. Production construction does
  not use destructive migration fallbacks. The database stays local to the
  device under the existing backup exclusion and `allowBackup=false` policy.
- Playback progress is written by a single service-owned recorder, sampled
  while active and flushed on pause, explicit stop, completion, item
  transition, engine replacement, and service shutdown. Explicit stop follows
  the core contract and persists position zero. Conflated `StateFlow` snapshots
  supply current positions; ordered `StateChanged` and `ItemTransition` events
  advance structural queue state so skipped intermediate snapshots cannot erase
  a transition source. Engine epochs, state revisions, and event sequences fence
  stale observations after reset.
- Checkpoint timestamps are strictly monotonic within a recorder and begin above
  an existing stored timestamp after process recreation or wall-clock rollback.
- Repository calls are time-bounded. A transient failure, timeout, or repository
  cancellation is reported and retained for an ordered retry; an abnormal
  recorder termination is propagated to service shutdown instead of being
  acknowledged as a successful flush.
- Engine detach and service close perform a bounded final drain of in-flight
  state and transition observations. This prevents ordinary collector
  interleaving from splitting a transition while keeping shutdown bounded; it
  is not a durable event journal or a transactional watermark. Extreme event
  buffer overflow remains a known boundary until the core exposes an atomic
  transition envelope or acknowledgement.
- Forgetting a document removes its database row first, then releases only this
  feature's persisted read grant. A release exception or a failed confirmation
  query is reported as an orphaned grant rather than as successful cleanup.

## Consequences

Room entities can evolve independently from player and feature models, while
Media3 receives a stable media ID and the service still resolves the original
URI to a short-lived descriptor. Revoked permissions or deleted provider rows
remain recoverable product states rather than database corruption.

Host-side tests can prove validation, mapping, ID reuse, progress ordering,
timestamp monotonicity, stop/completion policy, and stale-engine fencing. A
device or configured emulator is still required to prove Room DAO ordering,
schema creation and migrations, persisted and temporary grant lifetime,
provider revocation/deletion, grant quota behavior, process-death reopening,
and service-owned descriptor lifetime.
