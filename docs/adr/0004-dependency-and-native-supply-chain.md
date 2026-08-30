# ADR 0004: Reviewed, locked, and reproducible dependencies

- Status: Accepted
- Date: 2026-08-30

## Decision

- Resolve plugins only from the Gradle Plugin Portal, Google Maven, and Maven
  Central; resolve project dependencies only from Google Maven and Maven
  Central.
- Reject repositories declared by subprojects.
- Centralize approved coordinates in `gradle/libs.versions.toml`.
- Prohibit dynamic versions, snapshots, changing modules, and JitPack.
- Commit Gradle dependency lockfiles and dependency-verification metadata.
- Give each independent included build its own lockfile and verification
  metadata; the root build's files do not substitute for that boundary.
- Treat the initial third-party libmpv AAR as a bootstrap implementation behind
  a ZivPlayer-owned adapter, not as a permanent unexamined binary.
- Before public distribution, build libmpv and its native dependency stack from
  locked source revisions in Linux CI and publish checksums, build flags, SBOM,
  notices, and corresponding source.
- Prohibit FFmpeg `--enable-nonfree` and any dependency whose terms make the
  combined release non-redistributable.

## Consequences

An external dependency is considered prepared only when its version,
repository, checksum, license, and owning module are explicit. Merely adding a
coordinate to an application classpath does not satisfy this requirement.
