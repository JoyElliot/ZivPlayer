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

On the Linux builder, create the root-owned canonical workspace consumed by the
native-build profile with:

```sh
sudo python3 native/tools/materialize_sources.py \
  --workspace /var/tmp/zivplayer-native-source \
  --link-mode preserve
```

The full closure is assembled under a random sibling `.part` directory and
renamed to the selected workspace only after every source and recorded license
path passes. The generic tool default remains `native/out/workspace`; the
native-build profile default is the explicit ext4 path above. Its output parent
must be a trusted real directory, not a symlink or junction. The final workspace
is never overwritten. Remove an old ignored workspace explicitly before
requesting a fresh one; a build-mutated directory is not silently reused as
pristine source. The resulting
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
sudo python3 native/tools/materialize_sources.py \
  --workspace /var/tmp/zivplayer-native-source \
  --verify-workspace
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

## Locked toolchain and APT closure

`toolchain-manifest.toml` is the byte-level lock for the selected Linux/amd64
builder roots. It binds the source manifest digest and Android tuple to:

- Ubuntu `noble-20260810`'s linux/amd64 OCI index, manifest, config, and rootfs
  layer, with the digest-qualified manifest reference as build authority (the
  human-readable tag is descriptive only);
- Android NDK `29.0.14206865`, Platform 36 revision 02, Build Tools 36.0.0,
  command-line tools 12.0, and the Meson 1.11.0 wheel by size and SHA-256;
- the Ubuntu snapshot `20260811T000000Z`, its base dpkg status projection and
  archive keyring, 23 direct package roots, nine resolver-required InRelease/
  Packages indexes, and 102 exact `.deb` files; and
- a no-network build policy that prohibits floating references, `sdkmanager`,
  APT repositories, a pip index, and nonfree output during the actual build.

The Python verifier has no third-party Python dependency and uses the same
stable exit-code meanings as the source tool. It validates the bound manifests
without writes, verifies root bytes independently from APT, or verifies the
complete root-plus-APT closure:

```sh
python3 native/tools/toolchain_tool.py validate
python3 native/tools/toolchain_tool.py verify-roots
python3 native/tools/toolchain_tool.py verify-apt-cache
python3 native/tools/toolchain_tool.py verify-cache
```

`verify-roots` checks all five artifact archives, the four-object OCI graph,
and the config's uncompressed rootfs `diff_id`. APT verification extracts the
locked dpkg status and keyring from that verified layer, rechecks all three
InRelease signatures against fingerprint
`f6ecb3762474eda9d21b7022871920d1991bc93c`, verifies the exact index and
package sets plus the canonical receipt, checks every `.deb` control identity
from the stable verified byte snapshot, and proves 92 base packages plus 102
cached packages satisfy 523 dependency clauses. The solver transcript is
locked by size and SHA-256, and its 102 `Inst` and `Conf` records must match
`toolchain/apt-install-order.tsv` exactly rather than only as an unordered set.
Cache directory metadata and timestamps are not release identities.

NASM `2.16.01-1build1` is an explicit direct root. FFmpeg's selected x86_64
configuration probes a NASM-compatible assembler by default; relying on the
NDK-bundled Yasm instead would require a build-script override and separate
compatibility evidence.

The locked snapshot InRelease files do not carry `Valid-Until`. Reproducibility
therefore rests on the explicit snapshot timestamp, exact signed bytes, locked
signer, and local hashes rather than a claim that a moving mirror is currently
fresh.

Missing root bytes may be downloaded explicitly:

```sh
python3 native/tools/toolchain_tool.py fetch
```

`fetch` does not prepare APT. On the pinned Ubuntu 24.04 preparation
environment, with exact apt 2.8.3, dpkg 1.22.6ubuntu6.6, and gpgv
2.4.4-2ubuntu17.4, prepare the ignored APT cache separately:

```sh
sudo /bin/sh native/toolchain/prepare-apt-cache.sh
```

This is an explicit networked preparation step, not a native build. It starts
under a sanitized environment, snapshots and hashes its locked inputs, verifies
Ubuntu signatures, writes only a new ignored `native/cache/toolchain/apt`
directory, verifies that staged cache before publication, and refuses to
replace an existing cache. The final build consumes these bytes with networking
disabled. APT verification intentionally requires canonical `/usr/bin/gpgv`
and `/usr/bin/dpkg-deb`; Windows should invoke it through the controlled Linux
environment. The current WSL run is useful independent lock evidence but is
not an accepted release builder.

## Locked OCI base materialization

`rootfs_tool.py` safely turns the one locked Ubuntu OCI layer into a fresh base
directory without Docker or Podman. `preflight` verifies the complete toolchain
cache and plans every layer entry without writing an output:

```sh
python3 native/tools/rootfs_tool.py preflight
```

Canonical materialization and verification require Linux root, canonical
`/usr/bin/findmnt`, and an ext4 destination. The output parent must already
exist, be root-owned, and be either private or sticky. The destination itself
must not exist and must stay outside the toolchain cache:

```sh
sudo python3 native/tools/rootfs_tool.py materialize-base \
  --output /var/tmp/zivplayer-toolchain-base
sudo python3 native/tools/rootfs_tool.py verify-base \
  --rootfs /var/tmp/zivplayer-toolchain-base
```

The materializer verifies all locked cache bytes before writing, rejects
whiteouts, special entries, traversal, unsafe links, sparse/PAX metadata,
case/Unicode collisions, excessive paths, entries, bytes, or TAR padding, and
publishes only by an atomic no-replace rename. Because the base layer contains
setuid/setgid programs, staging and the final root remain `0700 root:root`.
Files, links, owners, modes, normalized timestamps, and bytes are rechecked
against the locked layer; `ziv-toolchain-base.json` binds the stable manifest
snapshot, OCI identity, and canonical tree digest.

This command creates only the verified Ubuntu base. It does not itself install
the locked APT closure, extract Android archives, accept Android licenses, or
make the result a release-grade container.

## Offline installed APT environment

`environment_tool.py` consumes that base and the same verified cache to install
the exact 102-package closure without network access. The destination must be
absent, outside the cache, and below a trusted root-owned ext4 parent. The
command never replaces an existing tree:

```sh
sudo python3 native/tools/environment_tool.py materialize-apt \
  --output /var/tmp/zivplayer-toolchain-apt
sudo python3 native/tools/environment_tool.py verify-apt \
  --rootfs /var/tmp/zivplayer-toolchain-apt
```

The public entry point re-verifies the OCI, artifact, index, package, solver,
and install-order locks before writing. Package installation runs with APT
sources disabled inside private mount, network, PID, UTS, and IPC namespaces.
Only loopback is visible; `/proc/keys` is replaced by an empty read-only mount;
the input and `policy-rc.d` are immutable bind mounts; temporary filesystems
have locked sizes and modes. Before the first package script, all Linux
capability sets are empty, `no_new_privs` is active, and a locked x86_64 seccomp
filter permits only AF_UNIX sockets while denying new namespaces, keyring/BPF/
perf/userfaultfd/io_uring operations, and x32 syscalls. The parent also places
the complete process tree in a dedicated cgroup-v2 domain with locked PID,
memory/swap, CPU, and root-device I/O limits, plus hard `RLIMIT_NOFILE`,
`RLIMIT_FSIZE`, and core-file limits. Any limit event, surviving descendant,
mount leak, malformed security state, or cleanup failure fails closed.

After installation, the tool proves the exact 194-package base-plus-APT dpkg
projection, normalizes the generated Java JKS certificate timestamps while
preserving aliases and DER bytes, removes only ldconfig's nondeterministic
auxiliary cache, canonicalizes the two tzdata wall-clock transcript lines, and
records the helper snapshots, generated-file policy, sandbox policy, stdout,
stderr, and dpkg projection below
`usr/share/zivplayer/toolchain-evidence/apt-stage`. Tree scanning binds file
contents, owners, modes, hard links, safe user xattrs, normalized timestamps,
and symlink targets with explicit entry/path/xattr/byte budgets. The receipt is
written only after independent verification; file data and metadata are synced
before the no-replace rename, and the output parent is synced before success is
reported.

The retained hardened stage used by the current composition contains 12,449
entries and 709,614,523 file bytes. Its tree SHA-256 is
`5ed7511bcb6f9d9cc5a2a966f54495b243135a2f9de469e1e7a4c5850406f7e0`, and its
receipt SHA-256 is
`6a4929a97403cf8058a3b02c36dc5ba5d30e262f44472520b3b8061ede9b5cae`.
Earlier pre-hardening WSL reproducibility measurements are superseded by these
current bound values. This remains inspection evidence, not accepted release
provenance. The trusted boundary remains the exclusive host process, repository,
cache, output parent, and cgroup hierarchy; a host hard kill may leave a
precisely named empty cgroup that requires operator cleanup after its population
is checked.

The final fail-closed gate is:

```sh
python3 native/tools/toolchain_tool.py check-lock
```

It currently verifies all locked bytes and then exits 3. Android/Python tools
have a canonical standalone projection, and a separate composition receipt now
proves its fixed ephemeral binding to the installed APT environment. The SDK
receipt itself intentionally remains standalone with
`aptEnvironmentBound=false`. Accepted Android license files, system notices,
the retention bundle, canonical source build, and corresponding source-manifest
container status remain pending. Do not change those status fields to
`complete` without the named evidence.

The standalone SDK projection is prepared and verified with:

```sh
python3 native/tools/sdk_tool.py preflight
sudo python3 native/tools/sdk_tool.py materialize \
  --output /var/tmp/zivplayer-sdk-projection
sudo python3 native/tools/sdk_tool.py verify \
  --root /var/tmp/zivplayer-sdk-projection
```

`sdk_tool.py` re-verifies immutable archive snapshots, validates every ZIP
member and Android `source.properties`, preserves the NDK's 37 audited relative
symlinks and eight explicit case-only header pairs, validates every Meson wheel
RECORD row, and projects all package resources plus a fixed Python launcher.
It calls neither `sdkmanager` nor `pip`, normalizes ownership/modes/timestamps,
rejects replacement, and publishes only after an independent tree/receipt
verification and filesystem synchronization. The receipt explicitly says
`aptEnvironmentBound=false`, `releaseInput=false`, and `packageXml=not-generated`.
Its toolchain legal inventory covers projected builder tools only; it is not
the generated product notices bundle.

The fixed read-only composition is prepared and verified with:

```sh
sudo python3 native/tools/composition_tool.py preflight
sudo python3 native/tools/composition_tool.py compose-and-smoke \
  --receipt /var/tmp/zivplayer-toolchain-composition.json
sudo python3 native/tools/composition_tool.py verify \
  --receipt /var/tmp/zivplayer-toolchain-composition.json
```

`composition_tool.py` independently re-verifies and pins both root directories
and their receipts, pins every executed helper by stable file descriptor, and
publishes a root-owned canonical JSON receipt with `renameat2(NOREPLACE)`
relative to a pinned parent directory. Its fixed namespace pivots onto the APT
root, mounts that root and the SDK projection read-only, permits writable tmpfs
only at the declared transient paths, exposes loopback with no routes, and
requires the combined zero-capability, `no_new_privs`, locked-seccomp, resource-
limit, and cgroup boundary. The smoke then verifies Python 3.12.3, Meson 1.11.0,
Ninja 1.11.1, pkg-config 1.8.1, JDK 17, Build Tools 36.0.0, Platform 36, NDK 29,
and both API-26 NDK compiler targets. It builds one temporary shared object per
ABI and rejects an ELF LOAD alignment other than `0x4000`.

Two fresh complete-cache inspection materializations produced identical
25,286-entry trees (2,913,084,578 file bytes) and byte-identical receipts. The
tree SHA-256 is `dac18763...1c5a4`, the projection SHA-256 is
`d5becfde...6ef3c`, and the receipt SHA-256 is `7f9bf9d6...6e544`. These are WSL
inspection results, not accepted release provenance.

Two final WSL composition runs used fresh namespaces/cgroups and produced
byte-identical 9,562-byte receipts. The receipt SHA-256 is
`e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989`,
the composition digest is
`11173e513a3fc0348d5780b60def6a340d140980d4b40bd0bf74fdfd9b5cd51a`,
and the canonical smoke transcript SHA-256 is
`c491134a712d88a1d0d76e96eb39494a623cb61fb818ad962e286a112d19e60c`.
The receipt binds the current APT receipt/tree SHA-256 values
`6a4929a97403cf8058a3b02c36dc5ba5d30e262f44472520b3b8061ede9b5cae` /
`5ed7511bcb6f9d9cc5a2a966f54495b243135a2f9de469e1e7a4c5850406f7e0`
and the SDK receipt/tree/projection values
`7f9bf9d66185c63d60a41e451146717be2794f95dc73a675618da7c09fd6e544` /
`dac187631c910c5e7bb10c20573d8c7b8accc1051077d22a6d11a5707c11c5a4` /
`d5becfde012c856be4ed5eb04215e233a8a6e8ff3d860198c45c9f197fd6ef3c`.
It explicitly retains `ready=false` and `releaseInput=false`. These measurements
assume an exclusive trusted root-controlled host; they do not claim protection
from a concurrent hostile root process and remain WSL inspection evidence only.

## Locked libmpv-stack profile, preflight, and preparation

`native-build-profile.toml` is the fail-closed input contract for the first
source-built libmpv-stack slice. It binds the source and toolchain manifest byte
digests, upstream revision, API 26, the two selected ABIs, 16 KiB page-size
policy, fixed tool versions, logical mounts, exact command argv, output
allowlist, NDK runtime-library bytes, and both original/replacement overlay
hashes. The desired applied overlay mode is explicitly `0755`; it is not
inferred from Git file-mode behavior on Windows.

The profile keeps the verified preserve-mode source tree read-only. `prepare`
creates a fresh writable independent source copy plus disjoint fresh output,
HOME, and temporary directories. This distinction is required because the
reviewed upstream dependency scripts generate Autotools files and build Lua and
mbedTLS in their source directories. The eventual executor must start from an
empty environment and apply only the declared variables; the profile forbids
network, Gradle, `sdkmanager`, APT repositories, a pip index, floating
references, and nonfree output during this stack-only phase.

The two byte-locked overlays are intentionally narrow:

- `buildscripts/buildall.sh` accepts only
  `--arch arm64 mpv` or `--arch x86_64 mpv`, selects the API-26 NDK compilers,
  and refuses an existing per-ABI prefix;
- `buildscripts/include/path.sh` fixes the composed SDK/NDK/JDK/tool paths,
  helper digest, four-job parallelism, locale/time/reproducibility variables,
  and isolated HOME/TMP/XDG paths.

The fixed output allowlist for each ABI is eight source-built libraries
(`libavcodec.so`, `libavdevice.so`, `libavfilter.so`, `libavformat.so`,
`libavutil.so`, `libmpv.so`, `libswresample.so`, and `libswscale.so`) plus the
exact NDK `libc++_shared.so`. `libplayer.so` is deliberately absent: the
release-grade ZivPlayer JNI wrapper remains a separate pending source and
contract gate.

Validate the profile without building:

```sh
python3 native/tools/native_build_tool.py validate
```

On Linux as root, the current preflight additionally verifies the complete
preserve-mode source workspace on ext4, the untouched upstream bytes/modes at
both overlay destinations, both locked NDK runtime libraries, and the existing
toolchain-composition receipt:

```sh
sudo python3 native/tools/native_build_tool.py preflight \
  --source-workspace /var/tmp/zivplayer-native-source
```

Prepare and then independently verify the default build workspace without
executing either locked build command:

```sh
sudo python3 native/tools/native_build_tool.py prepare
sudo python3 native/tools/native_build_tool.py verify-preparation
```

Preparation requires an absent destination. It publishes
`/var/tmp/zivplayer-native-build` only after a fresh root-owned same-filesystem
tree passes full verification. Ordinary files have independent identities,
hardlinks and nested mounts are rejected, the two symlink texts are preserved,
the byte-locked overlays are applied atomically only to the copy, and
`output`/`home`/`tmp` remain empty. The canonical source is re-verified before
publication and is never overlaid or mutated.

Two fresh WSL preserve-mode materializations produced byte-identical receipts
with SHA-256
`e2ea14b6eea0c2f692a321853473f04dad2f1f3199d017bfffc733a858523c04`
and the same 30,436-entry tree. That tree contains 29,238 regular files, 1,196
directories, and two symlinks; its SHA-256 is
`05a19a0fc8d11c88903c663783ebca00c0201f656fa54b580ab47a75fa746ee6`.
The retained tree then passed the locked preflight with profile SHA-256
`538fe37887840c4e23acdb189d3464878dd006dfc42f6e4c4565490f88252413`.
These remain WSL inspection results, not accepted release provenance.

One complete WSL preparation produced a 5,378-byte canonical receipt with
SHA-256
`eff4993d2c1a079564f8da458eb2fc3bb7ee234716d8ce2380ee4c5e59de49f5`.
The post-overlay source has 30,436 entries, 408,191,312 regular-file bytes, and
tree SHA-256
`bdabef1dc6032963422aad7576ab8ea45a5f5393d5a304b5273f94fbaabb2d2d`.
An independent verification command passed; the original overlay destinations
still match their upstream hashes. The receipt records `phase=prepared`,
`buildExecuted=false`, `ready=false`, and `releaseInput=false`.

There is still no accepted native executor run, build command execution,
output staging, ELF/JNI audit, canonical build receipt, offline Gradle/AAR
integration, or compliance bundle. No libmpv library was produced by
preparation, and its success must not be reported as an M7E build. These checks
assume the ADR's exclusive trusted root-controlled builder boundary and do not
claim protection against a concurrent hostile root process.

## Locked namespace-probe policy

`native-executor-policy.toml` is a separate, exact contract for the next
executor step. Keeping it separate from `native-build-profile.toml` preserves
the already-published preparation receipt while binding that profile's exact
SHA-256, logical mount paths, empty inherited environment, timeout and output
limits, cgroup/resource envelope, namespace properties, and three helper byte
digests. Its current phase is only `namespace-probe`; `buildCommands`,
`artifactStaging`, `buildReceipt`, `ready`, and `releaseInput` are all `false`.

Validate this contract without entering a namespace or executing a build:

```sh
python3 native/tools/native_executor_tool.py validate
```

The locked namespace helper describes an ephemeral overlay root over the APT
stage, a separate read-only SDK mount, and distinct source/output/HOME/TMP
binds. The locked probe checks the exact mount set, detached old root,
loopback-only network, empty/fixed environment, zero capabilities,
`no_new_privs`, seccomp, descriptor cleanup, and root/SDK write protection. The
temporary build bind deliberately remains executable until real upstream build
behavior proves a stricter setting is compatible.

This step only establishes and statically validates that contract. The Python
launcher does not yet expose a probe or build subcommand, and no accepted
executor-probe result has been recorded. Implementation-only WSL helper checks
do not change that boundary and did not execute a compiler or `buildall.sh`.
Consequently the verified preparation and all release gates remain unchanged.

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

The source bytes, Linux base-image object graph, Android/Python tool archives,
base dpkg projection, and APT package/index closure are locked. The base-plus-
APT stage and standalone Android/Python projection now have reproducible
offline materializers, and their exact read-only composition has passed the
fixed isolated smoke profile in inspection mode. This is not yet a release-
grade installed container: accepted Android license files, redistribution
evidence, an accepted release-builder materialization/run, the actual native
build, and its artifact audits are still required before native artifacts can
be accepted.

## Remaining native gates

Traversal-safe offline materialization and fixed toolchain composition are now
implemented and have been run against the complete locked cache in inspection
mode. A separate namespace-probe policy is also byte-locked but has not yet
been launched by the canonical executor. The next native milestones must:

1. finish the Linux namespace executor: pin and re-verify every immutable input,
   run the harmless namespace probe, then separately authorize and run the two
   exact offline build commands before publishing a no-replace canonical build
   receipt;
2. close the portable offline Gradle artifact set before any Android wrapper
   build;
3. adapt and harden the Kotlin/JNI wrapper inside `platform:libmpv-android`;
4. build both selected ABIs at native API 26 in that environment;
5. record every build option and patch hash;
6. audit ELF class, machine, SONAME/NEEDED, 16 KiB LOAD alignment, and exported
   JNI symbols;
7. produce per-artifact hashes, SBOM, notices, and complete corresponding
   source; and
8. prove the release APK contains no `dev.jdtech.mpv:libmpv:1.0.0` bootstrap
   artifact.

Until those gates pass, the Maven bootstrap AAR remains development-only and
public release is blocked.
