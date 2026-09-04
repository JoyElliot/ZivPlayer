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
the retention bundle, an accepted release-builder source build and audit, and
corresponding source-manifest container status remain pending. Do not change
those status fields to `complete` without the named evidence.

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

## Historical locked libmpv-stack profile, preflight, and preparation

`native-build-profile.toml` is the historical fail-closed input contract for
the first source-built libmpv-stack slice. It binds the source and toolchain manifest byte
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
The profile, its executor policy, and every preparation/build receipt produced
from it remain immutable stack-only evidence.

The two byte-locked overlays are intentionally narrow:

- `buildscripts/buildall.sh` accepts only
  `--arch arm64 mpv` or `--arch x86_64 mpv`, selects the API-26 NDK compilers,
  and refuses an existing per-ABI prefix;
- `buildscripts/include/path.sh` fixes the composed SDK/NDK/JDK/tool paths,
  helper digest, four-job parallelism, locale/time/reproducibility variables,
  and isolated HOME/TMP/XDG paths.

The historical fixed output allowlist for each ABI is eight source-built
libraries (`libavcodec.so`, `libavdevice.so`, `libavfilter.so`, `libavformat.so`,
`libavutil.so`, `libmpv.so`, `libswresample.so`, and `libswscale.so`) plus the
exact NDK `libc++_shared.so`, or nine libraries in total. Neither
`libplayer.so` nor `libzivplayer_mpv.so` may be added to that old allowlist or
receipt; the ZivPlayer wrapper uses a separate additive profile.

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

The fourth workspace was prepared and independently verified with the commands
below before it was consumed. These exact invocations are retained as
historical evidence and must not be rerun against that path:

```sh
# HISTORICAL - DO NOT RUN AGAINST THIS CONSUMED PATH
sudo python3 native/tools/native_build_tool.py prepare \
  --build-workspace /var/tmp/zivplayer-native-build-298845ca4076-weak-audit
sudo python3 native/tools/native_build_tool.py verify-preparation \
  --build-workspace /var/tmp/zivplayer-native-build-298845ca4076-weak-audit
```

Preparation requires an absent destination. It publishes
the requested workspace only after a fresh root-owned same-filesystem tree
passes full verification. Ordinary files have independent identities,
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
`298845ca407684b0ec073036a78602972cbf971d8b9642a19476bf1c1f6ad4cc`.
These remain WSL inspection results, not accepted release provenance.

The fourth WSL preparation at
`/var/tmp/zivplayer-native-build-298845ca4076-weak-audit` produced a 5,378-byte
canonical receipt with SHA-256
`e51e2e706c3488feabbe1b5482b4d0433db8c823a6a659f2b2483b25b5ff7116`.
The post-overlay source has 30,436 entries, 408,192,579 regular-file bytes, and
tree SHA-256
`72678d1096e844777a6bb7008fee5e3dd1690c0612a6854cdb293b10001d1602`.
An independent verification command passed; the original overlay destinations
still match their upstream hashes. The receipt records `phase=prepared`,
`buildExecuted=false`, `ready=false`, and `releaseInput=false`.

## Additive wrapper-inclusive preparation

`native-wrapper-build-profile.toml` is a separate schema-v2 preparation
contract. It retains the historical nine-library stack and adds the built
`libzivplayer_mpv.so`, for ten expected libraries per ABI and 20 eventual
artifacts. The locked build path is NDK 29 `ndk-build` through the checked-in
`wrapper/Android.mk` and `wrapper/Application.mk`. `wrapper/CMakeLists.txt`
remains a source-contract/reference input and is not executed by this profile.

The preparation copies six exact wrapper inputs into host
`<build-workspace>/wrapper` with root mode `0555` and file mode `0444`. That
permission-locked snapshot is intended for a future read-only bind at
`/build/wrapper`; preparation alone does not establish the execution namespace.
Use only a fresh, absent ext4 workspace:

```sh
python3 native/tools/native_build_tool.py \
  --profile native/native-wrapper-build-profile.toml validate
sudo python3 native/tools/native_build_tool.py \
  --profile native/native-wrapper-build-profile.toml preflight \
  --source-workspace /var/tmp/zivplayer-native-source
sudo python3 native/tools/native_build_tool.py \
  --profile native/native-wrapper-build-profile.toml prepare \
  --source-workspace /var/tmp/zivplayer-native-source \
  --build-workspace <fresh-ext4-path>
sudo python3 native/tools/native_build_tool.py \
  --profile native/native-wrapper-build-profile.toml verify-preparation \
  --source-workspace /var/tmp/zivplayer-native-source \
  --build-workspace <prepared-ext4-path>
```

The verified inspection preparation at
`/var/tmp/zivplayer-native-build-wrapper-d6cf2a36-20260905-a1` binds profile
SHA-256
`d6cf2a360b4c8f159e49fc9a9872faf4a21e3dc5e8a225905d3b3ccaf4fe42ce`.
Its canonical 7,690-byte receipt has SHA-256
`e51dae00b7f145de9663ee1b90010bf9904f8775ff2f12624454040c941132b4`
and records `kind=ziv-native-build-preparation-wrapper-v1`,
`buildExecuted=false`, `ready=false`, and `releaseInput=false`. It has not run
`buildall.sh`; the historical executor is not permitted to consume it.

For the historical stack-only path, an accepted namespace-only executor run now
exists in the WSL inspection environment. Four separate build attempts also exist. The first two failed
before output staging; the third completed both ABI commands and staging, then
failed the API-26 symbol audit. The fourth completed the two-command build,
exact staging, structural audit, and canonical non-release receipt
publication. There is still no accepted release-builder build, offline
Gradle/AAR integration, or compliance bundle. Preparation and the namespace
probe alone produced no libmpv library and must not be reported as build
evidence; only the fourth inspection receipt closes that non-release loop.
These checks assume the ADR's
exclusive trusted root-controlled builder boundary and do not claim protection
against a concurrent hostile root process.

## Locked namespace-probe policy

`native-executor-policy.toml` is a separate, exact contract for the
namespace-only executor step. It binds the current build profile's exact
SHA-256, logical mount paths, empty inherited environment plus fixed variables,
timeout and output limits, cgroup/resource envelope, namespace properties, and
four byte-locked launcher/namespace/probe/seccomp helpers. Its current phase is
only `namespace-probe`; `buildCommands`,
`artifactStaging`, `buildReceipt`, `ready`, and `releaseInput` are all `false`.

Validate this contract without entering a namespace or executing a build:

```sh
python3 native/tools/native_executor_tool.py validate
```

The fourth workspace received this namespace-only probe before execution. The
command is historical and must not be rerun against the consumed path:

```sh
# HISTORICAL - DO NOT RUN AGAINST THIS CONSUMED PATH
sudo python3 native/tools/native_executor_tool.py probe \
  --build-workspace /var/tmp/zivplayer-native-build-298845ca4076-weak-audit
```

The locked namespace helper describes an ephemeral overlay root over the APT
stage, a separate read-only SDK mount, and distinct source/output/HOME/TMP
binds. The locked probe checks the exact mount set, detached old root,
loopback-only network, empty/fixed environment, zero capabilities,
`no_new_privs`, seccomp, descriptor cleanup, and root/SDK write protection. The
temporary build bind deliberately remains executable until real upstream build
behavior proves a stricter setting is compatible.

The canonical launcher pins the selected policy/profile/manifests, preparation
and composition receipts, APT/SDK roots, prepared directories, overlays, and
all four helpers before spawning. It re-verifies them before the probe and,
only after an exact successful transcript plus complete cgroup cleanup, again
afterward. The child makes parent-death signaling its first main-path action,
then validates the exact descriptor set and empty inherited environment,
enrolls in the cgroup, and applies hard rlimits; the parent reserves pidfd
capacity before launch and blocks `SIGINT`, `SIGTERM`, and `SIGHUP` until
cleanup completes.

The accepted WSL inspection run used policy SHA-256
`24a42f725bc174d41a7434d57c7078dd1f02a2cc27963202a8638909cb7276ce`
and produced the exact six-record transcript with SHA-256
`fd251aeb116fd8ced374df5c9887af8dcc3bde03002b4968181421a1682b9f81`.
It confirmed the private mount/network/PID/UTS/IPC namespace, declared mounts,
fixed environment, loopback-only network, zero capabilities, `no_new_privs`,
seccomp, descriptor isolation, unchanged workspace, and the namespace-probe
policy's closed build gate. The run left no executor cgroup or process; the
output, HOME, and temporary trees remained empty. It did not execute a compiler
or `buildall.sh`.

This remains inspection evidence, not accepted release-builder provenance.
The host Python interpreter, system tools, kernel, and hard host termination are
inside the trusted-host boundary rather than byte-locked executor inputs. A
hard host failure or a cgroup-removal error can retain one precisely named empty
`.zivplayer-apt-*` cgroup; an operator must prove `populated 0` and zero
descendants before removing that exact directory. The verified preparation and
namespace-probe gates remained unchanged by that probe. The later fourth
one-shot inspection build is recorded separately below.

## Locked offline inspection-build contract

`native-build-executor-policy.toml` is a new policy rather than a mutation of
the accepted namespace-probe policy. It binds the exact profile and prepared
workspace receipt, the current composition receipt, and the accepted probe
policy/transcript. Its SHA-256 is
`f9d7c4afe5cdf3ff701a9281c4ba92134a3c21512fb8f18e8ecc1addef9feafc`.

Validate this contract without creating an attempt marker, entering a
namespace, or executing a build:

```sh
python3 native/tools/native_build_executor_tool.py validate
```

Before consumption, the fourth workspace also passed this exact read-only
input check:

```sh
# HISTORICAL - DO NOT RUN AGAINST THIS CONSUMED PATH
sudo python3 native/tools/native_build_executor_tool.py verify-inputs \
  --build-workspace /var/tmp/zivplayer-native-build-298845ca4076-weak-audit
```

The policy and executor treat build execution, exact allowlist staging,
structural artifact audit, and canonical build-receipt publication as one
closed loop. All four capabilities must succeed together; `ready` and
`releaseInput` remain `false`. The CLI exposes static `validate`, read-only
`verify-inputs`, and the explicit one-shot `execute`. On Linux as root,
`verify-inputs` rechecks the complete prepared inputs, the exact
preparation/composition receipt bytes, and the resolved locked `llvm-readelf`
target without entering a namespace. `execute` is the only command that may
consume a prepared workspace. The exact fourth invocation was:

```sh
# HISTORICAL - DO NOT RUN AGAINST THIS CONSUMED PATH
sudo python3 native/tools/native_build_executor_tool.py execute \
  --build-workspace /var/tmp/zivplayer-native-build-298845ca4076-weak-audit
```

That path is now permanently consumed; neither `verify-inputs` nor `execute`
may be rerun against it.

The CLI default deliberately remains the original consumed workspace path, so
current prepared workspaces must be selected explicitly and cannot be consumed
by an omitted argument.

The first invocation consumed the older `/var/tmp/zivplayer-native-build`
workspace under policy SHA-256
`d0cf43cedfa73a41f4a4cd0aebe144321bbd01c2d7431c7035551d87f472e329`.
Its retained marker SHA-256 is
`f6e7fb1e87737c0361565f0e24a5cf43086201a2fc72a80b7f227986cb3931c8`.
The arm64 command built and installed mbedTLS and dav1d, then Meson 1.11 failed
at `libxml2/meson.build:19` because its optional `git describe` program was not
present in the closed PATH. The x86_64 command was not started. The output tree
remained empty, no build receipt was published, and no executor process or
cgroup remained. That consumed workspace is retained unchanged for diagnosis.

The refreshed `buildall.sh` overlay installs and re-verifies a 19-byte,
root-owned, mode-0500, single-link `git` stub with exact SHA-256
`c07d6c0d3d6f1bcd8396ab432e050a0578f73e7e644c5c3fd230386c1294cb75`.
It always exits 127: optional snapshot-version probes fall back deterministically
without importing ambient Git or network access, while required Git behavior
still fails closed. That repair produced profile SHA-256
`ac17ad61199c3761d5cc484f5ec258a55912981f2d1af83ce7587ee91e013915`.

The second invocation consumed
`/var/tmp/zivplayer-native-build-ac17ad61199c` under policy SHA-256
`b1b0f9499bf7452ca5228f16e2371209cfc5fb7c1f58f7299d427164fee8302d`.
Its retained root-owned mode-0600 marker SHA-256 is
`87c30c328a24e0a4a517f4302d8267b2c82fa371b4ebcb79ae1ff9b305557af3`.
The Git stub worked: arm64 passed libxml2, FFmpeg, FreeType, fontconfig,
FriBidi, and HarfBuzz before libunibreak installation failed. The relative
`INSTALL=install` value was rewritten by Autoconf's nested `config.status`
handling to a nonexistent `../install`. No x86_64 build started, the output
tree remained empty, no build receipt was published, and process/cgroup cleanup
completed. This consumed workspace is also retained and was not rerun.

The current path overlay pins `INSTALL=/usr/bin/install`, the absolute GNU
coreutils binary in the locked APT tree. A disposable Autoconf probe against
the exact libunibreak source confirmed that both top-level and nested Makefiles
retain the absolute path. The current profile SHA-256 is
`298845ca407684b0ec073036a78602972cbf971d8b9642a19476bf1c1f6ad4cc`.

The third invocation consumed
`/var/tmp/zivplayer-native-build-298845ca4076` under build policy SHA-256
`eb671c17e47b4fc34b2d1974d0b5cbcb7fe8d24975b46100f606f61f559b91a3`.
Its retained root-owned mode-0600 marker SHA-256 is
`681a0de50104d6a02378d7df13e5fd56e026d4e3cc924de1f8993b8a622aba91`.
Both ABI commands completed, and staging published the exact 18-library output
set with 243,407,008 regular-file bytes. The API-26 audit then rejected a weak
undefined `memfd_create` in the first affected AArch64 artifact. No build
receipt or temporary receipt was published, and process/cgroup cleanup
completed. The consumed workspace and its non-release staged artifacts remain
retained and were not rerun.

The retained ELF evidence traces that symbol to the locked NDK 29
compiler-runtime `aarch64.c.o` availability probe, not an unguarded libmpv or
FFmpeg call. It is `NOTYPE WEAK DEFAULT UND` with only
`R_AARCH64_GLOB_DAT` in six
AArch64 DSOs. The local resolver tests the GOT entry and returns when it is
zero; the Android 8.1 linker zero-fills this unresolved weak relocation. The
revised audit still requires every strong undefined symbol to resolve through
the version-aware staged/API-26 closure. It permits
`NOTYPE WEAK DEFAULT UND memfd_create` only in AArch64 `libavcodec.so`,
`libavfilter.so`, `libavformat.so`, `libavutil.so`, `libmpv.so`, and
`libswscale.so`, and only with the exact `R_AARCH64_GLOB_DAT` relocation set.
Each accepted case is recorded per artifact; any other library, symbol type,
weak symbol, or relocation remains an error.

Before consumption, the complete WSL read-only check passed for preparation
receipt
`e51e2e706c3488feabbe1b5482b4d0433db8c823a6a659f2b2483b25b5ff7116`,
composition receipt
`e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989`,
and resolved ELF-tool digest
`5104576a3518575cf1887c2afa9249bbd0dc175cb9dc0f2af0d430fe0cb20bbe`.
That check opened no namespace and ran no build command.

The fourth invocation then consumed
`/var/tmp/zivplayer-native-build-298845ca4076-weak-audit` under the current
policy. Its canonical root-owned mode-0600, 1,434-byte attempt marker has
SHA-256
`44d94fe0c293b3480d316ca4603fc8e4842cb995a9967bcc0ad84bcf66854015`.
Both fixed ABI commands completed in order with runner exit code 0, and staging
published exactly 18 root-owned single-link regular libraries totalling
243,407,008 bytes. All staged sizes and SHA-256 values match the receipt, and
all 18 files are byte-identical to the third attempt's retained output.

The complete ELF/API-26/16-KiB audit passed. It resolved every strong undefined
symbol through the version-aware staged/platform closure and recorded exactly
the six permitted AArch64 `memfd_create` references, each as `NOTYPE`,
`DEFAULT`, and `R_AARCH64_GLOB_DAT`; no x86_64 exception was needed. The
canonical root-owned mode-0644, 72,674-byte build receipt has SHA-256
`7d5dc92f4d4ccd55f2340814d6bce071133560178f294c907a86f251dabd4d3e`.
It records `buildExecuted=true`, `artifactStaged=true`, and
`artifactAudited=true`, while `ready=false` and `releaseInput=false` remain
closed. Its pending blockers are the source wrapper, offline closure, accepted
actual build graph, and release gate. Post-run verification found exactly the
seven expected workspace-root entries, no temporary receipt, empty HOME/TMP,
no residual executor process, and no `.zivplayer-apt-*` cgroup.

Historical stack-only execution is single-attempt. Before launching, it
publishes and syncs a canonical, bounded, root-owned, metadata-normalized, no-replace attempt
marker through the pinned workspace descriptor. Its immutable fields bind the
policy, profile, preparation/composition receipts, accepted probe evidence,
helpers, exact commands, and pre-launch consumed state. Every failure retains
the consumed workspace without a build receipt. The two profile commands must
run in their fixed order with an empty inherited environment, the existing
cgroup envelope, a dedicated four-hour build deadline, and separate 32-MiB
stdout/stderr bounds. The build deadline requires its own validator rather than
the shorter installer-timeout validator. Child stdout markers are diagnostic,
not command-order evidence; the receipt must bind the policy/profile, locked
runner bytes, and parent-observed runner lifecycle. Failure teardown is locked
to pidfd/cgroup termination, a bounded drain, and cgroup identity/emptiness
proof before removal. The new namespace and runner helpers are byte-locked; the
historical runner contains only the fixed API-26 `arm64` and `x86_64` `mpv`
commands. They
are reachable only through the explicit `execute` command after every input
gate passes.

The invocation contract separately fixes the execution namespace marker,
descriptor count, seccomp interpreter/helper, clean Bash interpreter flags,
and runner argv. The proc mount requests `hidepid=2`; the policy accepts only
the equivalent reported values `2` and `invisible`. The cgroup I/O device policy
requires the retained APT root, SDK projection, external canonical source, and
build workspace to share the same mapped block device; the accepted current
inputs satisfy that precondition.

Post-build verification distinguishes the immutable external canonical source
input from the prepared `workspace/source` tree. The former must remain
byte-identical; the latter is deliberately writable and may gain build output,
but only within its pinned directory and the policy's bounded filesystem delta.

Historical stack-only staging resolves each source symlink inside its locked
prefix and copies only
the nine expected regular-library bytes per ABI into no-replace output trees.
The audit requires exactly one complete `.dynsym` table whose declared rows
have contiguous zero-based indices. It also requires ELF64 `ET_DYN` and the
profile machine for each ABI; at least one `PT_LOAD`, with every `PT_LOAD`
aligned to exactly `0x4000` and `p_offset`/`p_vaddr` congruent; a unique basename
SONAME per artifact; strict whole-line SONAME/NEEDED names without whitespace,
brackets, or trailing data; and a complete per-ABI `DT_NEEDED` closure over
staged SONAMEs or the locked API-26 platform-stub allowlist. Every strong
undefined symbol, including its requested ELF version, must resolve through
that closure. An unresolved weak symbol is
accepted only when its ABI, library, symbol name, complete symbol-type set,
complete visibility set, and complete relocation-type set exactly match the
locked compiler-runtime exception described above; the receipt records each
accepted weak reference.
This stack-only phase also records `Java_` exports and rejects any such export
because the ZivPlayer JNI wrapper is still absent. Android
ident notes are recorded for every artifact; an ident on a newly built library
must report NDK major r29. The byte-locked NDK 29 package's prebuilt
`libc++_shared.so` reports r28 and is accepted as a locked runtime input rather
than misrepresented as a newly built artifact.

The additive wrapper path requires a new policy, namespace probe, runner, and
receipt kind bound to its exact preparation. The namespace must mount the six
inputs at `/build/wrapper` read-only, and the compiled audit must cover all 20
artifacts, including exact `JNI_OnLoad`/`JNI_OnUnload` visibility, no `Java_`
exports, the 16-entry `RegisterNatives` contract, SONAME/NEEDED closure, API 26,
and 16-KiB LOAD alignment. None of that evidence exists merely because the
preparation receipt passed.

The receipt records exact command order, bounded log sizes and
digests, parent-observed command events, cgroup/filesystem outcomes, artifact
hashes, ELF/SONAME/NEEDED/API-26
observations, overlay/build options, and explicit pending release blockers. It
remains a non-release inspection receipt after successful execution. All three
failed attempts remain retained as described above; the consumed fourth
workspace and its successful non-release receipt are retained without rerun.

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
- Meson/Ninja, Autoconf/Automake/libtool/Make, and `ndk-build`. The upstream
  stack pipeline does not use CMake; the additive wrapper profile also executes
  only locked NDK `ndk-build`. Its `CMakeLists.txt` is contract/reference input,
  not the selected build system.
- Full GPL-compatible feature profile. FFmpeg uses GPL and version-3 features;
  `--enable-nonfree` is prohibited.

The source bytes, Linux base-image object graph, Android/Python tool archives,
base dpkg projection, and APT package/index closure are locked. The base-plus-
APT stage and standalone Android/Python projection now have reproducible
offline materializers, and their exact read-only composition has passed the
fixed isolated smoke profile in inspection mode. This is not yet a release-
grade installed container: accepted Android license files, redistribution
evidence, and an accepted release-builder materialization/run with native
artifact audits are still required before native artifacts can be accepted.
The completed fourth WSL build and audit are non-release inspection evidence.

## Remaining native gates

Traversal-safe offline materialization and fixed toolchain composition are now
implemented and have been run against the complete locked cache in inspection
mode. The separate byte-locked namespace probe has also passed through the
canonical executor in inspection mode, and the fourth WSL one-shot build has
published its successful non-release receipt. The next native milestones must:

1. add a wrapper-specific policy and namespace probe bound to the additive
   profile and preparation, including a verified read-only `/build/wrapper`
   mount;
2. run the two locked `mpv+zivplayer_mpv` commands once in a fresh workspace and
   audit exactly 20 outputs, including ELF class/machine, API 26, SONAME/NEEDED,
   strong/weak symbol closure, 16-KiB LOAD alignment, and the exact JNI export
   and registration contract;
3. publish a separate non-release wrapper receipt without changing any
   historical stack receipt;
4. close the portable offline Gradle artifact set and generate a complete,
   receipt-bound native staging manifest before Gradle consumes any wrapper;
5. reproduce the source build and audit on the accepted release builder while
   retaining every option and patch hash;
6. package both selected API-26 ABIs and prove their Gradle/APK native closure;
7. produce per-artifact hashes, SBOM, notices, complete corresponding source,
   and device lifecycle/playback evidence; and
8. prove the release APK contains no `dev.jdtech.mpv:libmpv:1.0.0` bootstrap
   artifact.

Until those gates pass, the Maven bootstrap AAR remains development-only and
public release is blocked.
