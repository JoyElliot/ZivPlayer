# ADR 0011: Typed per-instance libmpv JNI bridge

- Status: Accepted
- Date: 2026-09-05

## Context

The development-only `dev.jdtech.mpv:libmpv:1.0.0` AAR uses a handle-based
Kotlin API, but its native wrapper drops command and property-write errors,
END_FILE details, reply identifiers, and playlist-entry identity. Its callbacks
run directly on a native event thread and Kotlin destruction is not idempotent.

The locked mpv-android source contains a different `is.xyz.mpv` wrapper. That
implementation owns one process-global `mpv_handle`, uses static callbacks,
logs or terminates on several failures, and cannot be renamed into ZivPlayer's
release bridge. The successful stack-only inspection receipt intentionally
contains no JNI wrapper and rejects every exported `Java_` symbol.

## Decision

- Introduce a module-internal typed `MpvClient` boundary before replacing the
  bootstrap binary. `LibmpvBackend` remains the only adapter to core player
  events, generation/seek fences, Surface leases, and sanitized failures.
- Keep a temporary `BootstrapMpvClient` implementation only while the new
  source bridge is built and audited. It explicitly contains the AAR's missing
  result and payload fidelity; it does not turn that AAR into a release input.
  Its adapter serializes concurrent destroy callers but, as required by ADR
  0006, invokes the AAR's non-idempotent native destroy at most once. If that
  invocation throws, its commit point is unknowable and the adapter records a
  terminal failure instead of risking a second native destroy.
- Build a new ZivPlayer-owned `libzivplayer_mpv.so`. Do not reuse the upstream
  global `libplayer.so` ABI or its package name.
- The source bridge uses an opaque positive token backed by a native registry,
  not a raw `mpv_handle *` exposed as a `jlong`. Tokens have a fail-closed
  lifecycle and stale, foreign, closing, or destroyed tokens cannot reach a
  native handle. Destruction is idempotent and terminates each handle once.
- Register native methods from `JNI_OnLoad` with `RegisterNatives`. The wrapper
  exports no name-derived `Java_` entry points; the wrapper-inclusive audit
  will instead require the exact registration table and exported
  `JNI_OnLoad`/SONAME contract.
- A single Kotlin-owned event thread is the only caller of `mpv_wait_event` for
  one handle. JNI deep-copies every event and payload before returning because
  libmpv owns that memory only until the next wait. Shutdown marks the client
  closed, calls `mpv_wakeup`, joins the event thread off Android's main Looper,
  clears the Surface, and only then calls `mpv_terminate_destroy`.
- Carry the raw event identity only inside this module, with typed property
  values, operation error codes, `reply_userdata`, START_FILE/END_FILE
  playlist-entry IDs, END_FILE reason/error, and insertion details. Unknown
  future event, property-format, and reason numbers are retained alongside an
  unknown classification instead of guessed.
- Treat an explicit END_FILE error as a source failure rather than natural
  completion. Quit, redirect, and unknown reasons fail closed and require a
  backend reset until entry-ID-aware redirect handling is implemented.
- Every command, option, property, observation, and Surface mutation returns
  its native status. The first backend surface deliberately uses synchronous
  command/property operations, so submission results are available before the
  call returns. Property observations retain non-zero correlation IDs. If an
  asynchronous command/property API is added later, both submission failure
  and its reply ID become part of this contract. The first source client accepts
  only the primitive property formats it requests; node and byte-array formats
  must be rejected explicitly rather than dropped until an immutable node model
  and exact copy/free tests are added.
- Preserve the existing direct Android `wid` path for the first source bridge:
  hold a global `Surface` reference until a successful clear or final native
  destruction, detach the old target before replacement, and propagate every
  `mpv_set_option` failure back to the Surface lease controller. This behavior
  is pinned to the selected mpv Android source and still requires device tests.
- Keep Storage Access Framework descriptors in the Kotlin/Media3 owner. JNI
  receives only the existing `/proc/self/fd/<n>` locator and never closes or
  duplicates that descriptor.
- Build the wrapper separately with locked NDK 29, native API 26, the two
  selected ABIs, and 16-KiB load alignment. A new wrapper-inclusive profile and
  receipt must audit exactly nine stack libraries plus the wrapper per ABI.
  Historical stack-only policies and receipts remain immutable.
- Gradle may consume native libraries only from a generated staging tree whose
  complete manifest, hashes, ABI inventory, metadata, ELF dependency closure,
  and wrapper receipt have passed. It must never package directly from a WSL
  workspace or silently fall back to the bootstrap AAR.
- Bootstrap retirement requires regenerated dependency locks and verification
  metadata, an offline Gradle resolution proof, clean APK native inventory,
  wrapper-inclusive SBOM/notices/corresponding source, and device lifecycle and
  playback validation. Missing evidence keeps `ready=false` and public release
  at No-Go.

## Consequences

The first migration commit can replace direct vendor API use with the typed
client seam without changing the running engine. Native source, wrapper build,
artifact staging, Gradle cutover, and bootstrap retirement can then be reviewed
and validated as separate commits.

The bootstrap AAR can still accept a seek command which native mpv rejects
because its void wrapper discards submission status. Such a seek may remain
pending without a completion event. This known development-path limitation is
not fixed by the typed seam and is another reason the source client is required
before release.

The source client must preserve the existing native gate, lossless event order,
generation/seek correlation, two-phase shutdown, and owner-bound Surface lease
behavior. Richer entry and end payloads may strengthen attribution later, but
cannot weaken the current ordering safeguards. A successful host build or APK
assembly is still not device playback evidence or accepted release provenance.
