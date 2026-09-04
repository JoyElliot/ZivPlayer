#!/usr/bin/env python3
"""Validate the locked closed-loop native inspection-build policy."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

import native_build_tool
import native_executor_tool
import source_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = NATIVE_DIR.parent
DEFAULT_POLICY = NATIVE_DIR / "native-build-executor-policy.toml"
DEFAULT_PROFILE = NATIVE_DIR / "native-build-profile.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_TOOLCHAIN_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"

MAX_POLICY_BYTES = 1024 * 1024
MAX_HELPER_BYTES = 1024 * 1024
MAX_ELF_TOOL_BYTES = 32 * 1024 * 1024
POLICY_KIND = "ziv-native-build-executor-policy-v1"
EXPECTED_POLICY_SHA256 = (
    "a947326abe1d4d7cff9da18eb0b29cca9bbd0545c989ffdc381567a4c90727f8"
)
HELPER_KEYS = (
    ("launcherPath", "launcherSha256"),
    ("namespacePath", "namespaceSha256"),
    ("runnerPath", "runnerSha256"),
    ("seccompPath", "seccompSha256"),
)

EXPECTED_POLICY: dict[str, object] = json.loads(
    r"""
{
    "schemaVersion": 1,
    "kind": "ziv-native-build-executor-policy-v1",
    "binding": {
        "profileName": "ziv-libmpv-stack-api26-v1",
        "profileSha256": "538fe37887840c4e23acdb189d3464878dd006dfc42f6e4c4565490f88252413",
        "namespaceProbePolicyPath": "native/native-executor-policy.toml",
        "namespaceProbePolicySha256": "fe3db9f8397e2cacd96077228b5dabc8fb9fa788492d523beea62da0530b17b9",
        "namespaceProbeTranscriptSha256": "fd251aeb116fd8ced374df5c9887af8dcc3bde03002b4968181421a1682b9f81",
        "preparationReceiptKind": "ziv-native-build-preparation-v1",
        "preparationReceiptSha256": "eff4993d2c1a079564f8da458eb2fc3bb7ee234716d8ce2380ee4c5e59de49f5",
        "toolchainCompositionReceiptSha256": "e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989",
        "hostPlatform": "linux/amd64",
        "toolchainMount": "/opt/zivplayer/toolchain",
        "sourceMount": "/build/source",
        "outputMount": "/build/output",
        "homeMount": "/build/home",
        "temporaryMount": "/build/tmp"
    },
    "gate": {
        "phase": "offline-inspection-build",
        "buildCommands": true,
        "artifactStaging": true,
        "artifactAudit": true,
        "buildReceipt": true,
        "ready": false,
        "releaseInput": false
    },
    "execution": {
        "environmentMode": "empty",
        "commandsSource": "native-build-profile",
        "commandOrder": "sequential-exact",
        "namespaceProfile": "ziv-native-build-namespace-execution-v1",
        "namespaceArgumentCount": 29,
        "preservedDescriptorCount": 10,
        "seccompInvocation": [
            "/usr/bin/perl",
            "/usr/share/zivplayer/toolchain-evidence/apt-stage/helpers/native/toolchain/install-seccomp.pl"
        ],
        "runnerInterpreterInvocation": [
            "/bin/bash",
            "--noprofile",
            "--norc"
        ],
        "runnerInvocation": [
            "/run/ziv-native-build-runner.bash",
            "--execute-locked-build"
        ],
        "procHidepidRequested": "2",
        "procHidepidAccepted": [
            "2",
            "invisible"
        ],
        "workspaceReuse": "single-attempt",
        "buildTimeoutSeconds": 14400,
        "timeoutValidation": "dedicated-build-wall-clock-deadline",
        "stdoutLimitBytes": 33554432,
        "stderrLimitBytes": 33554432,
        "stderrAllowed": true,
        "commandEventSource": "locked-runner-bytes-plus-parent-launch-and-exit",
        "childStdoutMarkers": "diagnostic-only-non-authoritative",
        "transcript": "bounded-stream-sha256-plus-parent-runner-events",
        "parentDeathSignal": "SIGKILL",
        "newSession": true,
        "failureTeardown": "pidfd-sigterm-2s-cgroup-kill-pidfd-sigkill",
        "leaderSigtermGraceSeconds": 2,
        "cgroupDrainTimeoutSeconds": 10,
        "cgroupRemoval": "require-empty-and-identity",
        "failureRetention": "retain-workspace-and-attempt",
        "rlimitNoFile": 4096,
        "rlimitFileSizeBytes": 2147483648,
        "rlimitCoreBytes": 0
    },
    "cgroup": {
        "version": 2,
        "controllers": [
            "cpu",
            "io",
            "memory",
            "pids"
        ],
        "pidsMax": 512,
        "memoryMaxBytes": 2147483648,
        "memorySwapMaxBytes": 0,
        "cpuQuotaMicros": 200000,
        "cpuPeriodMicros": 100000,
        "ioReadBytesPerSecond": 8388608,
        "ioWriteBytesPerSecond": 8388608,
        "ioReadOperationsPerSecond": 2048,
        "ioWriteOperationsPerSecond": 2048,
        "ioDevicePolicy": "require-apt-sdk-canonical-source-and-workspace-same-block-device"
    },
    "filesystem": {
        "minimumFreeBytes": 8589934592,
        "reserveFreeBytes": 4294967296,
        "minimumFreeInodes": 250000,
        "reserveFreeInodes": 100000,
        "maximumEntryDelta": 104096,
        "maximumLibraryBytes": 536870912,
        "maximumArtifactBytes": 4294967296
    },
    "workspace": {
        "precondition": "verified-preparation",
        "attemptMarkerName": "ziv-native-build-attempt.json",
        "attemptMarkerKind": "ziv-native-build-attempt-v1",
        "attemptMarkerSchemaVersion": 1,
        "attemptMarkerCanonicalJson": true,
        "attemptMarkerMode": 384,
        "attemptMarkerOwnerUid": 0,
        "attemptMarkerOwnerGid": 0,
        "attemptMarkerLinkCount": 1,
        "attemptMarkerNormalizedMtimeNs": 946684800000000000,
        "attemptMarkerXattrs": "forbidden",
        "attemptMarkerMaximumBytes": 65536,
        "attemptMarkerPublication": "workspace-root-create-no-replace-fsync",
        "attemptMarkerPublicationParent": "pinned-workspace-descriptor",
        "attemptMarkerReadback": "stable-canonical-bytes-and-sha256",
        "attemptMarkerParentFsync": true,
        "attemptMarkerRecords": [
            "policy-sha256",
            "profile-name-and-sha256",
            "preparation-and-composition-receipt-sha256",
            "accepted-namespace-probe-policy-and-transcript-sha256",
            "executor-helper-sha256",
            "exact-command-order",
            "attempt-state-consumed-before-launch"
        ],
        "attemptMarkerFields": [
            "artifactAudited",
            "artifactStaged",
            "buildExecuted",
            "commands",
            "kind",
            "launcherSha256",
            "namespaceProbePolicySha256",
            "namespaceProbeTranscriptSha256",
            "namespaceSha256",
            "policySha256",
            "preparationReceiptSha256",
            "profileName",
            "profileSha256",
            "ready",
            "releaseInput",
            "runnerSha256",
            "schemaVersion",
            "seccompSha256",
            "state",
            "toolchainCompositionReceiptSha256"
        ],
        "attemptMarkerFieldOrder": "canonical-json-lexicographic",
        "attemptMarkerAdditionalFields": "forbidden",
        "attemptMarkerState": "consumed-before-launch",
        "attemptMarkerBuildExecuted": false,
        "attemptMarkerArtifactStaged": false,
        "attemptMarkerArtifactAudited": false,
        "attemptMarkerReady": false,
        "attemptMarkerReleaseInput": false,
        "preparationReceiptDisposition": "retain-read-only",
        "mutableAfterAttempt": [
            "source",
            "output",
            "home",
            "tmp"
        ],
        "canonicalSourcePostcondition": "reverify-external-canonical-source-input-unchanged",
        "preparedSourcePostcondition": "allow-build-mutations-within-pinned-directory-and-bounded-filesystem-delta",
        "immutableInputsPostcondition": "reverify-policy-profile-receipts-toolchain-helpers-bytes-and-identity",
        "failureDisposition": "retain-workspace-and-attempt-without-build-receipt"
    },
    "artifacts": {
        "stagingMode": "copy-exact-allowlist",
        "publication": "per-abi-tree-no-replace-fsync",
        "builtLibraryRootTemplate": "/build/source/buildscripts/prefix/{upstreamArch}/lib",
        "runtimeLibraryRootTemplate": "/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/{ndkRuntimeDirectory}",
        "outputLibraryTemplate": "/build/output/{abi}/{library}",
        "expectedLibraries": [
            "libavcodec.so",
            "libavdevice.so",
            "libavfilter.so",
            "libavformat.so",
            "libavutil.so",
            "libc++_shared.so",
            "libmpv.so",
            "libswresample.so",
            "libswscale.so"
        ],
        "builtLibraries": [
            "libavcodec.so",
            "libavdevice.so",
            "libavfilter.so",
            "libavformat.so",
            "libavutil.so",
            "libmpv.so",
            "libswresample.so",
            "libswscale.so"
        ],
        "runtimeLibraries": [
            "libc++_shared.so"
        ],
        "abiDirectoryMode": 493,
        "libraryMode": 420,
        "normalizedMtimeNs": 946684800000000000,
        "sourceSymlinkPolicy": "resolve-within-locked-root-copy-regular-bytes",
        "outputSymlinks": "forbidden",
        "outputHardlinks": "forbidden",
        "outputXattrs": "forbidden"
    },
    "audit": {
        "elfToolPath": "/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf",
        "elfToolTarget": "llvm-readobj",
        "elfToolTargetSha256": "5104576a3518575cf1887c2afa9249bbd0dc175cb9dc0f2af0d430fe0cb20bbe",
        "elfToolDialect": "llvm-readelf",
        "elfToolArguments": [
            "--file-header",
            "--program-headers",
            "--dynamic-table",
            "--dyn-symbols",
            "--notes",
            "--wide"
        ],
        "elfType": "ET_DYN",
        "pageSizeBytes": 16384,
        "loadSegmentPolicy": "require-one-or-more-load-segments-each-align-16384-and-offset-vaddr-congruent",
        "sonamePolicy": "require-one-basename-per-artifact-and-unique-within-abi",
        "neededPolicy": "require-basename-and-resolve-to-staged-soname-or-api26-platform-stub",
        "dependencyClosurePolicy": "per-abi-complete-no-unknown-needed",
        "jniExportPolicy": "record-java-prefix-and-require-none",
        "nativeApiEvidence": "locked-api26-driver-plus-api26-platform-stub-symbol-closure",
        "platformStubRootTemplate": "/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/{ndkRuntimeDirectory}/26",
        "platformSonameAllowlist": [
            "libOpenSLES.so",
            "libaaudio.so",
            "libandroid.so",
            "libc.so",
            "libdl.so",
            "libEGL.so",
            "libGLESv2.so",
            "libGLESv3.so",
            "libjnigraphics.so",
            "liblog.so",
            "libm.so",
            "libmediandk.so",
            "libvulkan.so",
            "libz.so"
        ],
        "platformSymbolPolicy": "resolve-each-undefined-platform-symbol-at-api26",
        "androidIdentNotePolicy": "record-and-require-ndk-major-29-if-present",
        "abi": [
            {
                "name": "arm64-v8a",
                "clangTriple": "aarch64-linux-android26",
                "elfClass": 64,
                "elfMachine": "AArch64"
            },
            {
                "name": "x86_64",
                "clangTriple": "x86_64-linux-android26",
                "elfClass": 64,
                "elfMachine": "Advanced Micro Devices X86-64"
            }
        ]
    },
    "receipt": {
        "name": "ziv-native-build-receipt.json",
        "kind": "ziv-native-build-receipt-v1",
        "schemaVersion": 1,
        "canonicalJson": true,
        "mode": 420,
        "ownerUid": 0,
        "ownerGid": 0,
        "linkCount": 1,
        "normalizedMtimeNs": 946684800000000000,
        "xattrs": "forbidden",
        "maximumBytes": 4194304,
        "publication": "workspace-root-no-replace-fsync",
        "publicationParent": "pinned-workspace-descriptor",
        "temporarySuffix": ".part",
        "readback": "stable-canonical-bytes-and-sha256",
        "parentFsync": true,
        "records": [
            "policy-and-input-locks",
            "attempt-state",
            "exact-command-order-from-policy-profile-locked-runner-and-parent-events",
            "bounded-log-size-and-sha256",
            "cgroup-and-filesystem-outcome",
            "per-artifact-size-and-sha256",
            "elf-class-machine-type-and-load-segments",
            "soname-and-needed-resolution-closure",
            "api26-platform-stub-symbol-closure",
            "jni-export-observation",
            "build-options-and-overlay-hashes",
            "pending-release-blockers"
        ],
        "buildExecuted": true,
        "artifactStaged": true,
        "artifactAudited": true,
        "ready": false,
        "releaseInput": false
    },
    "helpers": {
        "launcherPath": "native/toolchain/native-executor-child.py",
        "launcherSha256": "02bdfc1357fbf533364f12829caa7f763f422c0053b8841499ae63166c272e3a",
        "namespacePath": "native/toolchain/native-build-execution-namespace.bash",
        "namespaceSha256": "6c7f7813b1e35a564a5b5e8af2ecaca89a37ec5b1bbe09084cde1df32c03cf3c",
        "runnerPath": "native/toolchain/native-build-runner.bash",
        "runnerSha256": "afbca67a7839c69efdd6a6856e1a4bd9bd6a1f4923213d95dbd2855fe3817c29",
        "seccompPath": "native/toolchain/install-seccomp.pl",
        "seccompSha256": "ab2e6d21a2768a585b2bc53a2495c78f46ab9a4dbac32d09bf58e311091c597d"
    }
}
"""
)


@dataclass(frozen=True)
class LoadedBuildExecutorPolicy:
    data: dict[str, object]
    raw: bytes
    sha256: str
    profile: native_build_tool.LoadedProfile
    probe_policy: native_executor_tool.LoadedExecutorPolicy
    helper_raws: dict[str, bytes]


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _assert_exact(actual: object, expected: object, location: str) -> None:
    if type(actual) is not type(expected):
        _schema(
            f"{location} type is not exact: "
            f"expected {type(expected).__name__}, got {type(actual).__name__}"
        )
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        missing = [key for key in expected if key not in actual]
        extra = [key for key in actual if key not in expected]
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if extra:
                details.append("unknown " + ", ".join(extra))
            _schema(f"{location} keys are not exact: {'; '.join(details)}")
        for key, expected_value in expected.items():
            _assert_exact(actual[key], expected_value, f"{location}.{key}")
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        if len(actual) != len(expected):
            _schema(
                f"{location} length is not exact: "
                f"expected {len(expected)}, got {len(actual)}"
            )
        for index, (actual_value, expected_value) in enumerate(
            zip(actual, expected, strict=True)
        ):
            _assert_exact(actual_value, expected_value, f"{location}[{index}]")
        return
    if actual != expected:
        _schema(f"{location} must be {expected!r}")


def validate_policy_data(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        _schema("native build executor policy must be a table")
    _assert_exact(data, EXPECTED_POLICY, "native build executor policy")
    return copy.deepcopy(data)


def _stable_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    return native_executor_tool._stable_bytes(  # noqa: SLF001
        path,
        maximum=maximum,
        label=label,
        missing_exit=source_tool.EXIT_SCHEMA,
    )


def _resolved_repository_file(relative: str, label: str) -> Path:
    return native_build_tool._resolve_repository_file(  # noqa: SLF001
        REPOSITORY_ROOT,
        relative,
        label,
    )


def _assert_profile_binding(
    data: dict[str, object],
    profile: native_build_tool.LoadedProfile,
) -> None:
    binding = data["binding"]
    artifacts = data["artifacts"]
    audit = data["audit"]
    assert isinstance(binding, dict)
    assert isinstance(artifacts, dict)
    assert isinstance(audit, dict)

    profile_pairs = (
        (binding["profileName"], profile.project["profile"], "profile name"),
        (binding["profileSha256"], profile.sha256, "profile SHA-256"),
        (binding["hostPlatform"], profile.toolchain["hostPlatform"], "host platform"),
        (binding["toolchainMount"], profile.toolchain["mount"], "toolchain mount"),
        (binding["sourceMount"], profile.policy["sourceCopyMount"], "source mount"),
        (binding["outputMount"], profile.policy["outputMount"], "output mount"),
        (binding["homeMount"], profile.policy["buildHomeMount"], "HOME mount"),
        (binding["temporaryMount"], profile.policy["temporaryMount"], "temporary mount"),
        (
            binding["preparationReceiptKind"],
            native_build_tool.PREPARATION_RECEIPT_KIND,
            "preparation receipt kind",
        ),
        (
            artifacts["builtLibraryRootTemplate"],
            profile.build["builtLibraryRootTemplate"],
            "built-library root template",
        ),
        (
            artifacts["runtimeLibraryRootTemplate"],
            profile.build["runtimeLibraryRootTemplate"],
            "runtime-library root template",
        ),
        (
            artifacts["outputLibraryTemplate"],
            profile.build["outputLibraryTemplate"],
            "output-library template",
        ),
        (
            artifacts["expectedLibraries"],
            profile.build["expectedLibraries"],
            "expected library allowlist",
        ),
        (
            artifacts["builtLibraries"],
            profile.build["builtLibraries"],
            "built library allowlist",
        ),
        (
            artifacts["runtimeLibraries"],
            profile.build["runtimeLibraries"],
            "runtime library allowlist",
        ),
        (
            audit["pageSizeBytes"],
            profile.project["pageSizeBytes"],
            "ELF page-size policy",
        ),
    )
    for actual, expected, label in profile_pairs:
        if actual != expected:
            _integrity(f"native build executor {label} is not bound to the profile")

    expected_audit_abis = [
        {
            "name": abi["name"],
            "clangTriple": abi["clangTriple"],
            "elfClass": abi["elfClass"],
            "elfMachine": abi["elfMachine"],
        }
        for abi in profile.abis
    ]
    if audit["abi"] != expected_audit_abis:
        _integrity("native build executor ABI audit is not bound to the profile")

    commands = profile.build["commands"]
    if not isinstance(commands, list) or commands != [
        ["/build/source/buildscripts/buildall.sh", "--arch", "arm64", "mpv"],
        ["/build/source/buildscripts/buildall.sh", "--arch", "x86_64", "mpv"],
    ]:
        _integrity("native build executor commands are not the exact profile sequence")


def _load_probe_policy(
    data: dict[str, object],
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
) -> native_executor_tool.LoadedExecutorPolicy:
    binding = data["binding"]
    assert isinstance(binding, dict)
    probe_path = _resolved_repository_file(
        str(binding["namespaceProbePolicyPath"]),
        "namespace-probe policy",
    )
    probe_policy = native_executor_tool.load_execution_policy(
        probe_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    native_executor_tool._assert_runtime_policy(probe_policy)  # noqa: SLF001
    if probe_policy.sha256 != binding["namespaceProbePolicySha256"]:
        _integrity("accepted namespace-probe policy digest differs")
    transcript_sha256 = hashlib.sha256(
        native_executor_tool.EXPECTED_PROBE_TRANSCRIPT
    ).hexdigest()
    if transcript_sha256 != binding["namespaceProbeTranscriptSha256"]:
        _integrity("accepted namespace-probe transcript binding differs")
    return probe_policy


def _assert_runtime_binding(
    data: dict[str, object],
    probe_policy: native_executor_tool.LoadedExecutorPolicy,
) -> None:
    execution = data["execution"]
    cgroup = data["cgroup"]
    filesystem = data["filesystem"]
    probe_process = probe_policy.data["process"]
    probe_cgroup = probe_policy.data["cgroup"]
    probe_filesystem = probe_policy.data["filesystem"]
    assert isinstance(execution, dict)
    assert isinstance(cgroup, dict)
    assert isinstance(filesystem, dict)
    assert isinstance(probe_process, dict)
    assert isinstance(probe_cgroup, dict)
    assert isinstance(probe_filesystem, dict)

    process_keys = (
        "environmentMode",
        "parentDeathSignal",
        "newSession",
        "rlimitNoFile",
        "rlimitFileSizeBytes",
        "rlimitCoreBytes",
    )
    if any(execution[key] != probe_process[key] for key in process_keys):
        _integrity("native build runtime process limits differ from the accepted probe")

    build_cgroup_base = {
        key: value for key, value in cgroup.items() if key != "ioDevicePolicy"
    }
    if build_cgroup_base != probe_cgroup:
        _integrity("native build cgroup limits differ from the accepted probe")

    build_filesystem_base = {
        key: filesystem[key]
        for key in (
            "minimumFreeBytes",
            "reserveFreeBytes",
            "minimumFreeInodes",
            "reserveFreeInodes",
            "maximumEntryDelta",
        )
    }
    if build_filesystem_base != probe_filesystem:
        _integrity("native build filesystem limits differ from the accepted probe")

    if (
        execution["cgroupDrainTimeoutSeconds"]
        != native_executor_tool.environment_tool.CGROUP_DRAIN_TIMEOUT_SECONDS
    ):
        _integrity("native build cgroup drain timeout differs from the runtime constant")


def _load_helpers(data: dict[str, object]) -> dict[str, bytes]:
    helpers = data["helpers"]
    assert isinstance(helpers, dict)
    raw_by_path: dict[str, bytes] = {}
    for path_key, digest_key in HELPER_KEYS:
        relative = str(helpers[path_key])
        path = _resolved_repository_file(relative, f"build executor helper {relative}")
        raw = _stable_bytes(
            path,
            maximum=MAX_HELPER_BYTES,
            label=f"build executor helper {relative}",
        )
        if hashlib.sha256(raw).hexdigest() != helpers[digest_key]:
            _integrity(f"build executor helper digest differs: {relative}")
        raw_by_path[relative] = raw
    if len(raw_by_path) != len(HELPER_KEYS):
        _schema("build executor helper paths must be distinct")
    return raw_by_path


def load_build_execution_policy(
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
) -> LoadedBuildExecutorPolicy:
    raw = _stable_bytes(
        policy_path,
        maximum=MAX_POLICY_BYTES,
        label="native build executor policy",
    )
    sha256 = hashlib.sha256(raw).hexdigest()
    if sha256 != EXPECTED_POLICY_SHA256:
        _integrity("native build executor policy digest differs")
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        _schema(f"cannot parse native build executor policy: {error}")
    data = validate_policy_data(parsed)
    if data["kind"] != POLICY_KIND:
        _schema("native build executor policy kind is not exact")

    profile = native_build_tool.load_profile(
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    _assert_profile_binding(data, profile)
    probe_policy = _load_probe_policy(
        data,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    _assert_runtime_binding(data, probe_policy)
    helper_raws = _load_helpers(data)
    return LoadedBuildExecutorPolicy(
        data=data,
        raw=raw,
        sha256=sha256,
        profile=profile,
        probe_policy=probe_policy,
        helper_raws=helper_raws,
    )


def _verify_io_device_scope(
    policy: LoadedBuildExecutorPolicy,
    apt_root: Path,
    sdk_root: Path,
    source_workspace: Path,
    build_workspace: Path,
) -> None:
    cgroup = policy.data["cgroup"]
    assert isinstance(cgroup, dict)
    if (
        cgroup["ioDevicePolicy"]
        != "require-apt-sdk-canonical-source-and-workspace-same-block-device"
    ):
        _integrity("native build cgroup I/O device policy differs")
    roots = (apt_root, sdk_root, source_workspace, build_workspace)
    try:
        devices = {os.stat(path).st_dev for path in roots}
    except OSError as error:
        _integrity(f"cannot inspect native build cgroup I/O device scope: {error}")
    if len(devices) != 1:
        _integrity("native build inputs do not share the cgroup I/O block device")
    device = next(iter(devices))
    block_device = Path("/sys/dev/block") / f"{os.major(device)}:{os.minor(device)}"
    if not block_device.exists():
        _integrity("native build cgroup I/O block device cannot be mapped")


def _verify_audit_tool(
    policy: LoadedBuildExecutorPolicy,
    sdk_root: Path,
) -> None:
    binding = policy.data["binding"]
    audit = policy.data["audit"]
    assert isinstance(binding, dict)
    assert isinstance(audit, dict)
    logical_mount = PurePosixPath(str(binding["toolchainMount"]))
    logical_tool = PurePosixPath(str(audit["elfToolPath"]))
    try:
        relative = logical_tool.relative_to(logical_mount)
    except ValueError:
        _schema("ELF audit tool is outside the locked toolchain mount")
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        _schema("ELF audit tool path is not canonical")

    link = sdk_root.joinpath(*relative.parts)
    try:
        link_info = os.lstat(link)
    except OSError as error:
        _integrity(f"cannot inspect locked ELF audit tool link: {error}")
    if not stat.S_ISLNK(link_info.st_mode):
        _integrity("locked ELF audit tool path is not the expected symlink")
    expected_target = str(audit["elfToolTarget"])
    try:
        actual_target = os.readlink(link)
    except OSError as error:
        _integrity(f"cannot read locked ELF audit tool link: {error}")
    if actual_target != expected_target or "/" in actual_target or "\\" in actual_target:
        _integrity("locked ELF audit tool symlink target differs")

    target = link.parent / actual_target
    sdk_resolved = sdk_root.resolve(strict=True)
    try:
        target_resolved = target.resolve(strict=True)
        target_resolved.relative_to(sdk_resolved)
    except (OSError, ValueError) as error:
        _integrity(f"locked ELF audit tool target escaped the SDK projection: {error}")
    try:
        target_info = os.lstat(target_resolved)
    except OSError as error:
        _integrity(f"cannot inspect locked ELF audit tool target: {error}")
    if (
        not stat.S_ISREG(target_info.st_mode)
        or target_info.st_uid != 0
        or target_info.st_gid != 0
        or stat.S_IMODE(target_info.st_mode) != 0o755
        or target_info.st_nlink != 1
    ):
        _integrity("locked ELF audit tool target metadata differs")
    raw = _stable_bytes(
        target_resolved,
        maximum=MAX_ELF_TOOL_BYTES,
        label="locked ELF audit tool target",
    )
    if hashlib.sha256(raw).hexdigest() != audit["elfToolTargetSha256"]:
        _integrity("locked ELF audit tool target digest differs")


def verify_inputs(
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
    source_cache: Path,
    toolchain_cache: Path,
    source_workspace: Path,
    apt_root: Path,
    sdk_root: Path,
    composition_receipt_path: Path,
    build_workspace: Path,
) -> LoadedBuildExecutorPolicy:
    native_build_tool._require_linux_root("build executor input verification")  # noqa: SLF001
    policy = load_build_execution_policy(
        policy_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    _parent, build_workspace = native_build_tool._resolved_build_workspace(  # noqa: SLF001
        build_workspace,
        source_cache=source_cache,
        toolchain_cache=toolchain_cache,
        source_workspace=source_workspace,
        apt_root=apt_root,
        sdk_root=sdk_root,
        composition_receipt=composition_receipt_path,
        must_exist=True,
    )
    profile = native_build_tool.preflight(
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
        source_cache,
        toolchain_cache,
        source_workspace,
        apt_root,
        sdk_root,
        composition_receipt_path,
        announce=False,
    )
    if profile.raw != policy.profile.raw or profile.sha256 != policy.profile.sha256:
        _integrity("native build profile changed during input verification")
    preparation = native_build_tool._snapshot_preparation_inputs(  # noqa: SLF001
        profile,
        source_workspace,
        composition_receipt_path,
    )
    _receipt, preparation_raw = native_build_tool._verify_prepared_workspace(  # noqa: SLF001
        build_workspace,
        preparation,
    )
    binding = policy.data["binding"]
    assert isinstance(binding, dict)
    if hashlib.sha256(preparation_raw).hexdigest() != binding["preparationReceiptSha256"]:
        _integrity("native build preparation receipt digest differs from the build policy")
    if (
        hashlib.sha256(preparation.composition_receipt_raw).hexdigest()
        != binding["toolchainCompositionReceiptSha256"]
    ):
        _integrity("toolchain composition receipt digest differs from the build policy")
    _verify_io_device_scope(
        policy,
        apt_root,
        sdk_root,
        source_workspace,
        build_workspace,
    )
    _verify_audit_tool(policy, sdk_root)
    print(
        "native build executor inputs verified without execution: "
        f"policy={policy.sha256}; "
        f"preparation={binding['preparationReceiptSha256']}; "
        f"composition={binding['toolchainCompositionReceiptSha256']}; "
        f"elfTool={policy.data['audit']['elfToolTargetSha256']}"  # type: ignore[index]
    )
    return policy


def validate(
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
) -> LoadedBuildExecutorPolicy:
    policy = load_build_execution_policy(
        policy_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    gate = policy.data["gate"]
    assert isinstance(gate, dict)
    print(
        "native build executor policy valid: "
        f"phase={gate['phase']}; "
        f"buildCommands={json.dumps(gate['buildCommands'])}; "
        f"artifactAudit={json.dumps(gate['artifactAudit'])}; "
        f"ready={json.dumps(gate['ready'])}; "
        f"releaseInput={json.dumps(gate['releaseInput'])}; "
        f"sha256={policy.sha256}"
    )
    return policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--toolchain-manifest", type=Path, default=DEFAULT_TOOLCHAIN_MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "validate",
        help="validate the closed-loop inspection-build policy without executing it",
    )
    verify_command = commands.add_parser(
        "verify-inputs",
        help="verify current prepared inputs without entering a namespace or building",
    )
    verify_command.add_argument(
        "--source-cache",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_CACHE,
    )
    verify_command.add_argument(
        "--toolchain-cache",
        type=Path,
        default=native_build_tool.DEFAULT_TOOLCHAIN_CACHE,
    )
    verify_command.add_argument(
        "--source-workspace",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_WORKSPACE,
    )
    verify_command.add_argument(
        "--apt-root",
        type=Path,
        default=native_build_tool.DEFAULT_APT_ROOT,
    )
    verify_command.add_argument(
        "--sdk-root",
        type=Path,
        default=native_build_tool.DEFAULT_SDK_ROOT,
    )
    verify_command.add_argument(
        "--composition-receipt",
        type=Path,
        default=native_build_tool.DEFAULT_COMPOSITION_RECEIPT,
    )
    verify_command.add_argument(
        "--build-workspace",
        type=Path,
        default=native_build_tool.DEFAULT_BUILD_WORKSPACE,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "validate":
            validate(
                arguments.policy.resolve(),
                arguments.profile.resolve(),
                arguments.source_manifest.resolve(),
                arguments.toolchain_manifest.resolve(),
            )
        elif arguments.command == "verify-inputs":
            verify_inputs(
                arguments.policy.resolve(),
                arguments.profile.resolve(),
                arguments.source_manifest.resolve(),
                arguments.toolchain_manifest.resolve(),
                arguments.source_cache.resolve(),
                arguments.toolchain_cache.resolve(),
                Path(os.path.abspath(arguments.source_workspace)),
                Path(os.path.abspath(arguments.apt_root)),
                Path(os.path.abspath(arguments.sdk_root)),
                Path(os.path.abspath(arguments.composition_receipt)),
                Path(os.path.abspath(arguments.build_workspace)),
            )
        else:  # pragma: no cover - argparse owns this.
            _schema(f"unknown command: {arguments.command}")
        return source_tool.EXIT_OK
    except source_tool.SourceToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return source_tool.EXIT_INTERNAL
    except Exception as error:  # pragma: no cover - stable CLI boundary.
        print(f"error: internal native build executor tool failure: {error}", file=sys.stderr)
        return source_tool.EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
