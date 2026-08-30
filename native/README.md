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

The next native milestones must:

1. materialize the locked archives with traversal-safe extraction and verified
   submodule placement;
2. adapt and harden the Kotlin/JNI wrapper inside `platform:libmpv-android`;
3. build both selected ABIs at native API 26 in the locked Linux environment;
4. record every build option and patch hash;
5. audit ELF class, machine, SONAME/NEEDED, 16 KiB LOAD alignment, and exported
   JNI symbols;
6. produce per-artifact hashes, SBOM, notices, and complete corresponding
   source; and
7. prove the release APK contains no `dev.jdtech.mpv:libmpv:1.0.0` bootstrap
   artifact.

Until those gates pass, the Maven bootstrap AAR remains development-only and
public release is blocked.
