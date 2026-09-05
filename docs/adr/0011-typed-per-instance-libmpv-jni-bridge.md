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
contains no JNI wrapper and rejects every exported `Java_` symbol. That profile,
policy, and receipt remain immutable historical evidence.

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
  lifecycle. Ordinary operations reject stale, foreign, closing, or destroyed
  tokens; only controlled wakeup and teardown paths may reach a closing handle.
  Destruction is idempotent and terminates each handle once.
- Register native methods from `JNI_OnLoad` with `RegisterNatives`. The wrapper
  exports no name-derived `Java_` entry points; the wrapper-inclusive audit
  will instead require the exact 16-entry registration table, exported
  `JNI_OnLoad`/`JNI_OnUnload`, SONAME contract, and all 20 outputs across the two
  ABIs. Every registered pointer targets a non-throwing
  JNI guard so a C++ exception is converted to a Java failure instead of
  crossing the native boundary.
- Keep the JNI event wire independent of Kotlin object construction: one call
  fills fixed-size integer, long, double, and nullable-string buffers before
  returning. Presence bits distinguish absent fields from valid zero values,
  and `uint64_t reply_userdata` is retained as the exact signed `Long` bit
  pattern. The registration table and buffer indices are an audited ABI.
- A single Kotlin-owned event thread is the only caller of `mpv_wait_event` for
  one handle. JNI deep-copies every supported primitive event field and payload
  before returning because libmpv owns that memory only until the next wait.
  Node and byte-array payloads are deliberately omitted: observation requests
  for them are rejected, while an unexpected received format is surfaced as
  typed `Unsupported` metadata. Shutdown marks the client closed, calls
  `mpv_wakeup`, joins the event thread off Android's main Looper, clears the
  Surface, and only then calls `mpv_terminate_destroy`.
- An unexpected event-pump exit moves the source client to `BROKEN` once and
  notifies its observer. The owning backend then becomes unusable, fails the
  active generation with a sanitized RESET error, and rejects later observer
  registration or native operations. Malformed START_FILE/END_FILE payloads
  take this path instead of being guessed as a natural completion.
- Carry the raw event identity only inside this module, with typed property
  values, operation error codes, `reply_userdata`, START_FILE/END_FILE
  playlist-entry IDs, END_FILE reason/error, and insertion details. Unknown
  future event, property-format, and reason numbers are retained alongside an
  unknown classification instead of guessed.
- Treat an explicit END_FILE error as a source failure rather than natural
  completion. Quit, redirect, and unknown reasons fail closed and require a
  backend reset until entry-ID-aware redirect handling is implemented.
- Every command, option, property, observation, and Surface mutation propagates
  its native result: negative statuses become typed operation failures,
  property-unavailable maps to `null`, and unobserve retains its native count.
  The first backend surface deliberately uses synchronous command/property
  operations, so submission results are available before the call returns.
  Property observations retain non-zero correlation IDs. If an
  asynchronous command/property API is added later, both submission failure
  and its reply ID become part of this contract. The first source client accepts
  only the primitive property formats it requests; node and byte-array formats
  must be rejected explicitly rather than dropped until an immutable node model
  and exact copy/free tests are added.
- Version 1 does not expose asynchronous property getters and therefore does
  not decode `GET_PROPERTY_REPLY` payloads. Adding them requires a correlated
  typed reply contract, including error-gated values, before the wire expands.
- `MPV_FORMAT_OSD_STRING` is read-only and libmpv rejects it for property
  observation. The source client therefore rejects it alongside node and byte
  formats even though the temporary AAR can request it and then mislabels its
  callback as a plain string.
- Preserve the existing direct Android `wid` path for the first source bridge.
  A successful `mpv_set_option` only queues the selected mpv build's VO update,
  so replacement writes the new target directly and retains every global
  `Surface` reference which reached `wid` until `mpv_terminate_destroy`
  completes. Surface calls use a lock separate from lifecycle state, and no
  lifecycle lock is held across libmpv. Every requested mutation still returns
  its `mpv_set_option` status to the Surface lease controller. This behavior is
  pinned to the selected source and still requires device tests.
- FFmpeg retains its Android application-context pointer without a clear API.
  The wrapper therefore owns one process-lifetime global reference and does not
  delete it from `JNI_OnUnload`.
- Keep Storage Access Framework descriptors in the Kotlin/Media3 owner. JNI
  receives only the existing `/proc/self/fd/<n>` locator and never closes or
  duplicates that descriptor.
- Build the wrapper separately with locked NDK 29 `ndk-build`, native API 26,
  the two selected ABIs, shared libc++, a Release configuration, and 16-KiB load
  alignment. The additive profile snapshots `Android.mk`, `Application.mk`, the
  wrapper source, and contract inputs into a permission-locked tree. A separate
  namespace-only policy now proves that tree can be mounted read-only at
  `/build/wrapper` without opening any build or release gate. `CMakeLists.txt`
  remains a shallow contract/reference input and is not the selected execution
  path. Neither its restrictions nor the build-file declarations are
  provenance: the checked-in wrapper-inclusive build executor binds the
  accepted probe, exact preparation, six source inputs, and helper hashes. On
  execution it must audit exactly nine stack libraries plus the wrapper per
  ABI, or 20 outputs in total, before publishing its separate non-release
  receipt. The implementation and its tests are not compiled-artifact evidence;
  that evidence starts only with a successful fresh-workspace execution.
  Historical stack-only policies and receipts remain immutable.
- Gradle may consume native libraries only from a generated staging tree whose
  complete manifest, hashes, ABI inventory, metadata, ELF dependency closure,
  and wrapper receipt have passed. It must never package directly from a WSL
  workspace or silently fall back to the bootstrap AAR.
- Wrapper preparation alone records `buildExecuted=false`, `ready=false`, and
  `releaseInput=false`; those gates remain closed through the non-release build
  until generated staging, Gradle, compliance, and device evidence pass.
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

The checked-in `SourceMpvClient`, `MpvNativeBindings`, and
`native/wrapper/zivplayer_mpv.cpp` establish the source-side contract, but the
public backend factory remains `BootstrapMpvClient` until a wrapper-inclusive
receipt and generated staging tree pass. Their presence in source is not
release provenance and does not permit Gradle to fall back to an unaudited
local library.

The checked-in contract tool verifies declared source structure, including the
top-level Kotlin object and direct native members, guarded C++ definitions and
signatures, the scoped `JNI_OnLoad` registration path, exact fixed-buffer
constant sets, the fixed 16-method cardinality, exact CMake target/source
literals, and Release final-name properties. It rejects source `#define`,
`#undef`, and conditional-compilation directives in the JNI ABI translation
unit and permits only its fixed system/audited-input include list. It
deliberately does not run CMake or claim its NDK/API/ABI/STL/build-type/prefix
gates, included-header or toolchain macro effects, compiled class/R8 identity,
wire use-site semantics, ELF exports/SONAME/NEEDED, or runtime lifecycle
behavior; those remain separate `ndk-build`, compiled-audit, and device
evidence.

An initial source-destruction attempt is rejected before state mutation on
Android's main thread or the client's own event thread. Any reported
pre-termination failure—wakeup, join, requested Surface cleanup, or the native
bridge's final defensive Surface clear—leaves a retryable closing client. A
negative final-clear status is returned before `mpv_terminate_destroy`;
success means the token was already absent or destroyed, or termination
completed. If the JNI call throws, Kotlin treats its commit point as unknowable
and retains a terminal failure rather than invoking native termination a second
time.

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
