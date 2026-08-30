# Native source supply chain

The native tree owns the source-built libmpv supply-chain inputs used by
ZivPlayer. It does not turn the current bootstrap AAR into a release artifact.

`source-manifest.toml` is the canonical byte-level lock for the selected
mpv-android 2026-08-11 source closure. It records 23 inputs (22 source archives
and one build-helper script): the wrapper,
mpv, FFmpeg, their direct native dependencies, required build helpers, and the
upstream submodules that must be accounted for even when a selected feature is
disabled. Every entry has an immutable revision or release label, HTTPS URL,
expected byte count, SHA-256, license expression, and linkage/build role.
For inputs whose complete source archive and selected output have different
license profiles, `selectedBuildLicense` records the intended output profile
separately instead of pretending one expression describes both scopes.

## Source cache commands

Python 3.11 or newer is required. The tool has no third-party Python
dependencies.

Validate only the manifest schema. This command does not use the network or
write files:

```powershell
python native/tools/source_tool.py validate
```

Explicitly download missing locked inputs into the ignored local cache. Each
download is first written to a `.part` file, then size- and SHA-256-checked
before an atomic rename:

```powershell
python native/tools/source_tool.py fetch
```

Verify a complete cache without network access or writes:

```powershell
python native/tools/source_tool.py verify-cache
```

Run the source-tool tests:

```powershell
python -m unittest discover -s native/tests -v
```

Custom paths are supported, but the global manifest argument must precede the
subcommand:

```powershell
python native/tools/source_tool.py --manifest native/source-manifest.toml `
  verify-cache --cache native/cache/sources
```

The stable exit codes are:

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Unexpected internal failure or interruption |
| 2 | CLI or manifest schema error |
| 3 | Required archive missing from an offline cache |
| 4 | Size or SHA-256 integrity failure |
| 5 | Network or cache-write failure during explicit fetch |

An existing cache file that fails integrity verification is never silently
overwritten. Investigate or remove that one ignored cache file explicitly,
then run `fetch` again.

Handled errors and keyboard interruption remove the temporary file. An
external hard kill or host crash can still leave a hidden `*.part` file; it is
not accepted as a source input and may be removed after confirming that no
other fetch process is running.

## Offline source materialization

The materializer verifies the complete cache again before it creates any
output. It never downloads or initializes Git submodules. It pre-scans every
archive, rejects traversal, absolute/ambiguous paths, case or Unicode
collisions, unsafe links, sparse and special files, then strips the one locked
top-level source prefix. Parent/child source placements come only from the
manifest. In particular, it maps `freetype` to the upstream `freetype2`
directory, `libunibreak` to `unibreak`, and places the pinned
`gas-preprocessor.pl` in the upstream SDK helper path.

On a Linux builder that can preserve symbolic links, create the canonical
ignored workspace with:

```sh
python3 native/tools/materialize_sources.py
```

The full closure is assembled under a random sibling `.part` directory and
renamed to `native/out/workspace` only after every source and recorded license
path passes. Its output parent must be a trusted real directory, not a symlink
or junction. The final workspace is never overwritten. Remove an old ignored
workspace explicitly before requesting a fresh one; a build-mutated directory
is not silently reused as pristine source. The resulting
`ziv-native-materialization.json` records the manifest digest, every source
revision/digest/destination, selected Android tuple, link mode, and a canonical
tree digest over entry types, paths, modes, file bytes, and symbolic-link
targets. Archive bytes are copied into a verified temporary snapshot before
extraction, and generated timestamps are normalized to nanosecond precision.
The preflight also bounds individual TAR metadata reads and the whole
decompressed TAR stream before Python can allocate GNU longname or PAX
payloads. It enforces per-archive and whole-closure entry/byte limits, counts
link-copy expansion, rejects cross-archive path/type/case/Unicode collisions,
and derives the link topology later checked by the verifier.

Immediately verify a canonical workspace and the still-locked cache with:

```sh
python3 native/tools/materialize_sources.py --verify-workspace
```

The locked archives contain two safe relative symbolic links. Windows often
lacks permission to create them. An explicit inspection-only mode copies the
Harfbuzz file link and represents mpv-android's generated `jniLibs -> libs/`
directory link as an empty directory, while recording the non-canonical mode
in the receipt:

```powershell
python native/tools/materialize_sources.py `
  --workspace native/out/workspace-windows `
  --link-mode portable-copy
python native/tools/materialize_sources.py `
  --workspace native/out/workspace-windows `
  --verify-workspace `
  --allow-portable-copy
```

`portable-copy` proves cache, archive, placement, and extraction behavior; it
is not a release build input and cannot target the canonical
`native/out/workspace` path. Native build orchestration must run the verifier,
which rejects any receipt whose `linkMode` is not `preserve` by default. The
verifier independently compares every ordinary file with its locked archive
bytes and rejects missing, extra, type-changed, or topology-changed entries;
Windows inspection does not claim POSIX archive-mode parity. A handled error or keyboard interruption
removes staging. As with downloads, an external hard kill can leave a hidden
`.part` directory, which is never accepted as the final workspace.

If the final no-replace rename succeeds but syncing the output parent fails,
the command reports that the workspace was published with durability
unconfirmed and deliberately leaves it in place. Do not retry blindly: run
`--verify-workspace` against that path, then either accept it for the current
non-release inspection or remove it explicitly before creating a fresh one.

The materializer protects against hostile archive structure and ordinary
publication races. Its cache, repository, output parent, and process identity
remain a trusted boundary: a concurrent process running as the same user can
rewrite the tool, inputs, or receipt. A release builder must therefore use an
exclusive fresh workspace, materialize and verify it in one controlled job,
and treat the receipt as an integrity record rather than an authentication
signature. The verifier re-derives locked link topology and rehashes every
materialized tree entry against the locked ordinary-file proofs. It still is
not an authentication signature over a hostile same-user rewrite of the tool,
manifest, cache, tree, and receipt together.

## Locked build baseline

- Linux host only for native artifact provenance.
- Android NDK `29.0.14206865`.
- Android SDK Platform 36 and Build Tools 36.0.0 for the imported upstream
  wrapper build boundary; ZivPlayer itself continues to compile and target API
  37.
- Android native API and application minimum API 26. Lower Android releases
  are intentionally outside the current compatibility scope.
- `arm64-v8a` and `x86_64` only.
- 16 KiB maximum page-size alignment, with ELF validation still required on
  produced libraries.
- Meson/Ninja, Autoconf/Automake/libtool/Make, and `ndk-build`. The selected
  upstream pipeline does not use CMake.
- Full GPL-compatible feature profile. FFmpeg uses GPL and version-3 features;
  `--enable-nonfree` is prohibited.

The source byte lock is complete, but the build environment is not yet a
release-grade reproducible container. Exact Linux base image digest, apt
package revisions, Python/Meson/Ninja versions, JDK patch, and Android command
line tools still have to be locked before native artifacts can be accepted.

## Remaining native gates

Traversal-safe offline materialization is now implemented and has been run
against the complete locked cache in inspection mode. The next native
milestones must:

1. run the canonical `preserve` materialization inside the locked Linux build
   environment;
2. adapt and harden the Kotlin/JNI wrapper inside `platform:libmpv-android`;
3. build both selected ABIs at native API 26 in that environment;
4. record every build option and patch hash;
5. audit ELF class, machine, SONAME/NEEDED, 16 KiB LOAD alignment, and exported
   JNI symbols;
6. produce per-artifact hashes, SBOM, notices, and complete corresponding
   source; and
7. prove the release APK contains no `dev.jdtech.mpv:libmpv:1.0.0` bootstrap
   artifact.

Until those gates pass, the Maven bootstrap AAR remains development-only and
public release is blocked.
