#!/usr/bin/env python3
"""Validate the locked closed-loop native inspection-build policy."""

from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn

import composition_tool
import environment_tool
import materialize_sources
import native_build_tool
import native_executor_tool
import sdk_tool
import source_tool
import toolchain_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = NATIVE_DIR.parent
DEFAULT_POLICY = NATIVE_DIR / "native-build-executor-policy.toml"
DEFAULT_PROFILE = NATIVE_DIR / "native-build-profile.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_TOOLCHAIN_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"

MAX_POLICY_BYTES = 1024 * 1024
MAX_HELPER_BYTES = 1024 * 1024
MAX_ELF_TOOL_BYTES = 32 * 1024 * 1024
MAX_AUDIT_OUTPUT_BYTES = 32 * 1024 * 1024
AUDIT_TIMEOUT_SECONDS = 120
POLICY_KIND = "ziv-native-build-executor-policy-v1"
CHILD_PROFILE = native_executor_tool.CHILD_PROFILE
BUILD_ENVIRONMENT = dict(native_executor_tool.PROBE_ENVIRONMENT)
CONTROL_SIGNALS = native_executor_tool.CONTROL_SIGNALS
BUILD_RECEIPT_FIELDS = (
    "artifactAudited",
    "artifactStaged",
    "artifacts",
    "attempt",
    "buildExecuted",
    "buildOptionsAndOverlays",
    "commandEvidence",
    "inputLocks",
    "kind",
    "pendingReleaseBlockers",
    "platformApi26",
    "policySha256",
    "profileName",
    "profileSha256",
    "ready",
    "releaseInput",
    "resourceOutcome",
    "schemaVersion",
)
EXPECTED_POLICY_SHA256 = (
    "b1b0f9499bf7452ca5228f16e2371209cfc5fb7c1f58f7299d427164fee8302d"
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
        "profileSha256": "ac17ad61199c3761d5cc484f5ec258a55912981f2d1af83ce7587ee91e013915",
        "namespaceProbePolicyPath": "native/native-executor-policy.toml",
        "namespaceProbePolicySha256": "c270ab6ca37ae9d19d4c7dc1bde172f7194258c7eb5647d58f9dae84532370de",
        "namespaceProbeTranscriptSha256": "fd251aeb116fd8ced374df5c9887af8dcc3bde03002b4968181421a1682b9f81",
        "preparationReceiptKind": "ziv-native-build-preparation-v1",
        "preparationReceiptSha256": "e6417aed653b9056f580dd1c46a8b6b1e043a11695ddd99010aa243e62d4a4f6",
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
        "androidIdentNotePolicy": "record-all-and-require-built-artifact-ndk-major-29-if-present",
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


@dataclass
class PinnedBuildInputs:
    policy: LoadedBuildExecutorPolicy
    preparation: native_build_tool.PreparationInputs
    preparation_receipt_raw: bytes
    build_workspace: Path
    directories: dict[str, native_executor_tool.PinnedDirectory]
    files: dict[str, native_executor_tool.PinnedFile]
    composition_inputs: composition_tool.BoundInputs

    def close(self) -> None:
        native_executor_tool._close_partial_pins(  # noqa: SLF001
            self.files,
            self.directories,
            self.composition_inputs,
        )


@dataclass(frozen=True)
class BuildExecutionResult:
    stdout: bytes
    stderr: bytes
    return_code: int
    initial_free_bytes: int
    final_free_bytes: int
    initial_free_inodes: int
    final_free_inodes: int
    cgroup_empty_after_exit: bool
    cgroup_removed: bool


@dataclass
class PinnedArtifact:
    abi: str
    library: str
    source_kind: str
    source_relative_path: str
    descriptor: int
    identity: tuple[int, int]
    size: int
    sha256: str
    logical_path: Path
    resolved_path: Path
    logical_signature: tuple[int, ...]
    staged_path: Path | None = None
    staged_pin: PinnedArtifact | None = None
    audit: dict[str, object] = field(default_factory=dict)

    def close(self) -> None:
        failure: BaseException | None = None
        if self.staged_pin is not None:
            try:
                self.staged_pin.close()
            except BaseException as error:
                failure = error
            finally:
                self.staged_pin = None
        if self.descriptor >= 0:
            try:
                os.close(self.descriptor)
            except BaseException as error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    error,
                    f"{self.abi} {self.library} artifact cleanup also failed",
                )
            finally:
                self.descriptor = -1
        if failure is not None:
            raise failure


@dataclass(frozen=True)
class ParsedElf:
    elf_class: int
    machine: str
    elf_type: str
    load_segments: tuple[dict[str, int], ...]
    soname: str
    needed: tuple[str, ...]
    exports: frozenset[str]
    undefined: frozenset[str]
    android_ident: dict[str, object] | None
    readelf_size: int
    readelf_sha256: str


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
    if execution["leaderSigtermGraceSeconds"] != 2:
        _integrity("native build leader SIGTERM grace differs from the runtime contract")


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
) -> tuple[Path, bytes]:
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
    return target_resolved, raw


def _canonical_json(value: dict[str, object]) -> bytes:
    return native_build_tool._canonical_json(value)  # noqa: SLF001


def _link_descriptor_no_replace_at(
    source_descriptor: int,
    destination_directory_descriptor: int,
    destination_name: str,
    label: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = getattr(libc, "linkat", None)
    if linkat is None:
        _integrity("Linux libc does not expose linkat(AT_EMPTY_PATH)")
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    result = linkat(
        source_descriptor,
        b"",
        destination_directory_descriptor,
        os.fsencode(destination_name),
        0x1000,  # AT_EMPTY_PATH
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        _integrity(f"{label} already exists")
    _integrity(
        f"cannot publish {label} from its pinned descriptor: "
        f"{os.strerror(error_number)}"
    )


def _stat_signature(info: os.stat_result) -> tuple[int, ...]:
    return native_executor_tool._stat_signature(info)  # noqa: SLF001


def _pread_exact_large(descriptor: int, size: int, maximum: int, label: str) -> bytes:
    if size < 0 or size > maximum:
        _integrity(f"{label} exceeds its byte limit")
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not chunk:
            _integrity(f"{label} ended before its pinned size")
        chunks.append(chunk)
        offset += len(chunk)
    if os.pread(descriptor, 1, size):
        _integrity(f"{label} grew beyond its pinned size")
    return b"".join(chunks)


def _open_large_pinned_file(
    path: Path,
    expected_raw: bytes,
    *,
    maximum: int,
    label: str,
) -> native_executor_tool.PinnedFile:
    descriptor = -1
    try:
        before = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            len({_stat_signature(before), _stat_signature(opened), _stat_signature(current)})
            != 1
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size != len(expected_raw)
            or _pread_exact_large(descriptor, opened.st_size, maximum, label)
            != expected_raw
        ):
            _integrity(f"{label} changed while it was pinned")
        return native_executor_tool.PinnedFile(
            path=path,
            descriptor=descriptor,
            identity=(opened.st_dev, opened.st_ino),
            stat_signature=_stat_signature(opened),
            raw=expected_raw,
            label=label,
        )
    except source_tool.SourceToolError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        _integrity(f"cannot pin {label} {path}: {error}")


def _assert_large_pinned_file(
    pinned: native_executor_tool.PinnedFile,
    *,
    maximum: int,
) -> None:
    try:
        opened = os.fstat(pinned.descriptor)
        current = pinned.path.lstat()
        raw = _pread_exact_large(pinned.descriptor, opened.st_size, maximum, pinned.label)
        final = os.fstat(pinned.descriptor)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot recheck {pinned.label}: {error}")
    if (
        _stat_signature(opened) != pinned.stat_signature
        or _stat_signature(current) != pinned.stat_signature
        or _stat_signature(final) != pinned.stat_signature
        or (opened.st_dev, opened.st_ino) != pinned.identity
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or raw != pinned.raw
    ):
        _integrity(f"{pinned.label} identity, metadata, or bytes changed")


def _assert_build_file_pin(
    key: str,
    pinned: native_executor_tool.PinnedFile,
) -> None:
    if key == "audit-tool":
        _assert_large_pinned_file(pinned, maximum=MAX_ELF_TOOL_BYTES)
    else:
        native_executor_tool._assert_pinned_file(pinned)  # noqa: SLF001


def _assert_mutable_pinned_directory(
    pinned: native_executor_tool.PinnedDirectory,
) -> None:
    try:
        opened = os.fstat(pinned.descriptor)
        current = pinned.path.lstat()
    except OSError as error:
        _integrity(f"cannot recheck mutable {pinned.label}: {error}")
    original_mode = pinned.stat_signature[
        tuple(environment_tool._STABLE_STAT_FIELDS).index("st_mode")  # noqa: SLF001
    ]
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or (opened.st_dev, opened.st_ino) != pinned.identity
        or (current.st_dev, current.st_ino) != pinned.identity
        or (opened.st_uid, opened.st_gid) != (0, 0)
        or (current.st_uid, current.st_gid) != (0, 0)
        or stat.S_IMODE(opened.st_mode) != stat.S_IMODE(original_mode)
        or stat.S_IMODE(current.st_mode) != stat.S_IMODE(original_mode)
    ):
        _integrity(f"mutable {pinned.label} identity or policy changed")


def _assert_workspace_root_inventory(
    inputs: PinnedBuildInputs,
    *,
    receipt_present: bool,
) -> None:
    workspace_policy = inputs.policy.data["workspace"]
    receipt_policy = inputs.policy.data["receipt"]
    assert isinstance(workspace_policy, dict)
    assert isinstance(receipt_policy, dict)
    expected = {
        "source",
        "output",
        "home",
        "tmp",
        native_build_tool.PREPARATION_RECEIPT_NAME,
        str(workspace_policy["attemptMarkerName"]),
    }
    if receipt_present:
        expected.add(str(receipt_policy["name"]))
    try:
        actual = set(os.listdir(inputs.directories["workspace"].descriptor))
    except OSError as error:
        _integrity(f"cannot inspect native build workspace root inventory: {error}")
    if actual != expected:
        _integrity("native build workspace root inventory differs from its attempt state")


def _pin_build_inputs(
    policy: LoadedBuildExecutorPolicy,
    preparation: native_build_tool.PreparationInputs,
    preparation_receipt_raw: bytes,
    directory_baseline: dict[str, tuple[int, ...]],
    audit_tool_path: Path,
    audit_tool_raw: bytes,
    *,
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
    toolchain_cache: Path,
    source_workspace: Path,
    apt_root: Path,
    sdk_root: Path,
    composition_receipt_path: Path,
    build_workspace: Path,
) -> PinnedBuildInputs:
    files: dict[str, native_executor_tool.PinnedFile] = {}
    directories: dict[str, native_executor_tool.PinnedDirectory] = {}
    bound: composition_tool.BoundInputs | None = None
    try:
        bound = composition_tool._load_bound_inputs(  # noqa: SLF001
            toolchain_manifest_path,
            source_manifest_path,
            toolchain_cache,
            apt_root,
            sdk_root,
        )
        canonical_source = native_executor_tool._open_pinned_directory(  # noqa: SLF001
            source_workspace,
            label="canonical source workspace",
            expected_mode=0o755,
        )
        directories["canonical-source"] = canonical_source
        workspace = native_executor_tool._open_pinned_directory(  # noqa: SLF001
            build_workspace,
            label="native build workspace",
            expected_mode=0o700,
        )
        directories["workspace"] = workspace
        for name, mode in (
            ("source", 0o755),
            ("output", 0o700),
            ("home", 0o700),
            ("tmp", 0o700),
        ):
            directories[name] = native_executor_tool._open_pinned_directory(  # noqa: SLF001
                build_workspace / name,
                label=f"prepared {name} directory",
                expected_mode=mode,
                expected_device=workspace.identity[0],
                parent_descriptor=workspace.descriptor,
                child_name=name,
            )
        native_executor_tool._assert_directory_baseline(  # noqa: SLF001
            directory_baseline,
            directories,
        )

        source_manifest_raw = _stable_bytes(
            source_manifest_path,
            maximum=toolchain_tool.MAX_MANIFEST_BYTES,
            label="source manifest",
        )
        toolchain_manifest_raw = _stable_bytes(
            toolchain_manifest_path,
            maximum=toolchain_tool.MAX_MANIFEST_BYTES,
            label="toolchain manifest",
        )
        if (
            hashlib.sha256(source_manifest_raw).hexdigest()
            != policy.profile.project["sourceManifestSha256"]
            or hashlib.sha256(toolchain_manifest_raw).hexdigest()
            != policy.profile.project["toolchainManifestSha256"]
        ):
            _integrity("native build executor manifests differ from the locked profile")

        direct_files = (
            ("policy", policy_path, policy.raw, "native build executor policy", False),
            ("profile", profile_path, policy.profile.raw, "native build profile", False),
            ("source-manifest", source_manifest_path, source_manifest_raw, "source manifest", False),
            (
                "toolchain-manifest",
                toolchain_manifest_path,
                toolchain_manifest_raw,
                "toolchain manifest",
                False,
            ),
            (
                "composition-receipt",
                composition_receipt_path,
                preparation.composition_receipt_raw,
                "toolchain composition receipt",
                True,
            ),
        )
        for key, path, raw, label, canonical_receipt in direct_files:
            files[key] = native_executor_tool._open_pinned_file(  # noqa: SLF001
                path,
                raw,
                label=label,
                canonical_receipt=canonical_receipt,
            )
        files["source-receipt"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
            source_workspace / materialize_sources.RECEIPT_NAME,
            preparation.source_receipt_raw,
            label="source materialization receipt",
            canonical_receipt=True,
            parent_descriptor=canonical_source.descriptor,
            child_name=materialize_sources.RECEIPT_NAME,
        )
        files["preparation-receipt"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
            build_workspace / native_build_tool.PREPARATION_RECEIPT_NAME,
            preparation_receipt_raw,
            label="native build preparation receipt",
            canonical_receipt=True,
            parent_descriptor=workspace.descriptor,
            child_name=native_build_tool.PREPARATION_RECEIPT_NAME,
        )
        files["apt-receipt"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
            bound.apt_root / environment_tool.RECEIPT_NAME,
            bound.apt_receipt_raw,
            label="APT root receipt",
            canonical_receipt=True,
            parent_descriptor=bound.apt_fd,
            child_name=environment_tool.RECEIPT_NAME,
        )
        files["sdk-receipt"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
            bound.sdk_root / sdk_tool.RECEIPT_NAME,
            bound.sdk_receipt_raw,
            label="SDK projection receipt",
            canonical_receipt=True,
            parent_descriptor=bound.sdk_fd,
            child_name=sdk_tool.RECEIPT_NAME,
        )
        for index, (overlay, raw) in enumerate(
            zip(policy.profile.overlays, preparation.overlay_raws, strict=True)
        ):
            relative = str(overlay["replacement"])
            path = native_build_tool._resolve_repository_file(  # noqa: SLF001
                REPOSITORY_ROOT,
                relative,
                f"overlay[{index}].replacement",
            )
            files[f"overlay:{relative}"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
                path,
                raw,
                label=f"overlay replacement {relative}",
            )
        for relative, raw in policy.helper_raws.items():
            path = native_build_tool._resolve_repository_file(  # noqa: SLF001
                REPOSITORY_ROOT,
                relative,
                f"build executor helper {relative}",
            )
            files[f"helper:{relative}"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
                path,
                raw,
                label=f"build executor helper {relative}",
            )
        files["audit-tool"] = _open_large_pinned_file(
            audit_tool_path,
            audit_tool_raw,
            maximum=MAX_ELF_TOOL_BYTES,
            label="locked ELF audit tool target",
        )
        inputs = PinnedBuildInputs(
            policy=policy,
            preparation=preparation,
            preparation_receipt_raw=preparation_receipt_raw,
            build_workspace=build_workspace,
            directories=directories,
            files=files,
            composition_inputs=bound,
        )
        _assert_pinned_build_inputs(inputs, strict_workspace=True)
        return inputs
    except BaseException:
        native_executor_tool._close_partial_pins(files, directories, bound)  # noqa: SLF001
        raise


def _assert_pinned_build_inputs(
    inputs: PinnedBuildInputs,
    *,
    strict_workspace: bool,
) -> None:
    for key, pinned in inputs.directories.items():
        if strict_workspace or key == "canonical-source":
            native_executor_tool._assert_pinned_directory(pinned)  # noqa: SLF001
        else:
            _assert_mutable_pinned_directory(pinned)
    for key, pinned in inputs.files.items():
        _assert_build_file_pin(key, pinned)
    bound = inputs.composition_inputs
    composition_tool._assert_identity(  # noqa: SLF001
        bound.apt_root,
        bound.apt_fd,
        bound.apt_identity,
        "APT root",
    )
    composition_tool._assert_identity(  # noqa: SLF001
        bound.sdk_root,
        bound.sdk_fd,
        bound.sdk_identity,
        "SDK projection",
    )
    device_descriptors = (
        bound.apt_fd,
        bound.sdk_fd,
        *(pinned.descriptor for pinned in inputs.directories.values()),
    )
    if len({os.fstat(descriptor).st_dev for descriptor in device_descriptors}) != 1:
        _integrity("native build pinned inputs no longer share one I/O block device")
    helpers = inputs.policy.data["helpers"]
    assert isinstance(helpers, dict)
    preserved = (
        bound.apt_fd,
        bound.sdk_fd,
        *(inputs.directories[key].descriptor for key in ("workspace", "source", "output", "home", "tmp")),
        *(
            inputs.files[f"helper:{helpers[path_key]}"].descriptor
            for path_key in ("namespacePath", "runnerPath", "seccompPath")
        ),
    )
    execution = inputs.policy.data["execution"]
    assert isinstance(execution, dict)
    if (
        len(preserved) != execution["preservedDescriptorCount"]
        or len(set(preserved)) != len(preserved)
        or any(descriptor < 3 or descriptor == 255 for descriptor in preserved)
    ):
        _integrity("native build preserved descriptors are duplicated or outside policy")


def _reverify_build_inputs(
    inputs: PinnedBuildInputs,
    *,
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
    require_prepared_workspace: bool,
) -> None:
    _assert_pinned_build_inputs(inputs, strict_workspace=require_prepared_workspace)
    reloaded = load_build_execution_policy(
        policy_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    if (
        reloaded.raw != inputs.policy.raw
        or reloaded.data != inputs.policy.data
        or reloaded.profile.raw != inputs.policy.profile.raw
        or reloaded.helper_raws != inputs.policy.helper_raws
    ):
        _integrity("native build executor policy inputs changed during execution")
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
    if profile.raw != inputs.policy.profile.raw or profile.sha256 != inputs.policy.profile.sha256:
        _integrity("native build profile changed during execution")
    current = native_build_tool._snapshot_preparation_inputs(  # noqa: SLF001
        profile,
        source_workspace,
        composition_receipt_path,
    )
    native_build_tool._assert_same_preparation_inputs(  # noqa: SLF001
        inputs.preparation,
        current,
    )
    if require_prepared_workspace:
        _receipt, receipt_raw = native_build_tool._verify_prepared_workspace(  # noqa: SLF001
            inputs.build_workspace,
            current,
        )
        if receipt_raw != inputs.preparation_receipt_raw:
            _integrity("native build preparation receipt changed before execution")
    composition_tool._reverify_bound_inputs(  # noqa: SLF001
        toolchain_manifest_path,
        source_manifest_path,
        toolchain_cache,
        inputs.composition_inputs,
    )
    _verify_io_device_scope(
        inputs.policy,
        apt_root,
        sdk_root,
        source_workspace,
        inputs.build_workspace,
    )
    audit_path, audit_raw = _verify_audit_tool(inputs.policy, sdk_root)
    audit_pin = inputs.files["audit-tool"]
    if audit_path != audit_pin.path or audit_raw != audit_pin.raw:
        _integrity("locked ELF audit tool changed during execution")
    _assert_pinned_build_inputs(inputs, strict_workspace=require_prepared_workspace)
    if not require_prepared_workspace:
        _assert_workspace_root_inventory(inputs, receipt_present=False)


def _attempt_marker_data(inputs: PinnedBuildInputs) -> dict[str, object]:
    policy = inputs.policy.data
    workspace = policy["workspace"]
    binding = policy["binding"]
    helpers = policy["helpers"]
    assert isinstance(workspace, dict)
    assert isinstance(binding, dict)
    assert isinstance(helpers, dict)
    result: dict[str, object] = {
        "artifactAudited": workspace["attemptMarkerArtifactAudited"],
        "artifactStaged": workspace["attemptMarkerArtifactStaged"],
        "buildExecuted": workspace["attemptMarkerBuildExecuted"],
        "commands": copy.deepcopy(inputs.policy.profile.build["commands"]),
        "kind": workspace["attemptMarkerKind"],
        "launcherSha256": helpers["launcherSha256"],
        "namespaceProbePolicySha256": binding["namespaceProbePolicySha256"],
        "namespaceProbeTranscriptSha256": binding["namespaceProbeTranscriptSha256"],
        "namespaceSha256": helpers["namespaceSha256"],
        "policySha256": inputs.policy.sha256,
        "preparationReceiptSha256": hashlib.sha256(
            inputs.preparation_receipt_raw
        ).hexdigest(),
        "profileName": binding["profileName"],
        "profileSha256": inputs.policy.profile.sha256,
        "ready": workspace["attemptMarkerReady"],
        "releaseInput": workspace["attemptMarkerReleaseInput"],
        "runnerSha256": helpers["runnerSha256"],
        "schemaVersion": workspace["attemptMarkerSchemaVersion"],
        "seccompSha256": helpers["seccompSha256"],
        "state": workspace["attemptMarkerState"],
        "toolchainCompositionReceiptSha256": hashlib.sha256(
            inputs.preparation.composition_receipt_raw
        ).hexdigest(),
    }
    if sorted(result) != workspace["attemptMarkerFields"]:
        _integrity("native build attempt marker fields differ from policy")
    return result


def _publish_attempt_marker(
    inputs: PinnedBuildInputs,
) -> tuple[bytes, str]:
    workspace_policy = inputs.policy.data["workspace"]
    assert isinstance(workspace_policy, dict)
    name = str(workspace_policy["attemptMarkerName"])
    raw = _canonical_json(_attempt_marker_data(inputs))
    if len(raw) > int(workspace_policy["attemptMarkerMaximumBytes"]):
        _integrity("native build attempt marker exceeds its byte budget")
    workspace = inputs.directories["workspace"]
    descriptor = -1
    try:
        native_build_tool._assert_absent_at(  # noqa: SLF001
            workspace.descriptor,
            name,
            "native build attempt marker",
        )
        descriptor = os.open(
            name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            int(workspace_policy["attemptMarkerMode"]),
            dir_fd=workspace.descriptor,
        )
        created = os.fstat(descriptor)
        native_build_tool._write_all(descriptor, raw)  # noqa: SLF001
        os.fchown(
            descriptor,
            int(workspace_policy["attemptMarkerOwnerUid"]),
            int(workspace_policy["attemptMarkerOwnerGid"]),
        )
        os.fchmod(descriptor, int(workspace_policy["attemptMarkerMode"]))
        normalized = int(workspace_policy["attemptMarkerNormalizedMtimeNs"])
        os.utime(descriptor, ns=(normalized, normalized))
        native_build_tool._clear_fd_xattrs(  # noqa: SLF001
            descriptor,
            "native build attempt marker",
        )
        os.fsync(descriptor)
        current = os.fstat(descriptor)
        path_info = os.stat(name, dir_fd=workspace.descriptor, follow_symlinks=False)
        readback = _pread_exact_large(
            descriptor,
            current.st_size,
            int(workspace_policy["attemptMarkerMaximumBytes"]),
            "native build attempt marker",
        )
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino)
            or (path_info.st_dev, path_info.st_ino) != (created.st_dev, created.st_ino)
            or current.st_nlink != workspace_policy["attemptMarkerLinkCount"]
            or stat.S_IMODE(current.st_mode) != workspace_policy["attemptMarkerMode"]
            or (current.st_uid, current.st_gid)
            != (
                workspace_policy["attemptMarkerOwnerUid"],
                workspace_policy["attemptMarkerOwnerGid"],
            )
            or current.st_mtime_ns != normalized
            or current.st_size != len(raw)
            or readback != raw
            or os.listxattr(descriptor)
        ):
            _integrity("native build attempt marker changed before publication completed")
        os.fsync(workspace.descriptor)
        marker_path = inputs.build_workspace / name
        inputs.files["attempt-marker"] = native_executor_tool.PinnedFile(
            path=marker_path,
            descriptor=descriptor,
            identity=(current.st_dev, current.st_ino),
            stat_signature=_stat_signature(current),
            raw=raw,
            label="native build attempt marker",
        )
        descriptor = -1
        _assert_mutable_pinned_directory(workspace)
        _assert_build_file_pin("attempt-marker", inputs.files["attempt-marker"])
        return raw, hashlib.sha256(raw).hexdigest()
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot publish native build attempt marker: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_host_resource_limits(policy: LoadedBuildExecutorPolicy) -> None:
    resource = environment_tool.resource
    if resource is None:
        _schema("native build execution requires Linux resource support")
    execution = policy.data["execution"]
    assert isinstance(execution, dict)
    for limit, required, label in (
        (resource.RLIMIT_NOFILE, int(execution["rlimitNoFile"]), "open-file"),
        (resource.RLIMIT_FSIZE, int(execution["rlimitFileSizeBytes"]), "file-size"),
    ):
        _soft, hard = resource.getrlimit(limit)
        if hard != resource.RLIM_INFINITY and hard < required:
            _integrity(f"host hard {label} limit is below the locked build limit")


def _identity_text(identity: tuple[int, int]) -> str:
    return native_executor_tool._identity_text(identity)  # noqa: SLF001


def _build_process_arguments(
    inputs: PinnedBuildInputs,
    cgroup_procs_descriptor: int,
) -> tuple[list[str], tuple[int, ...]]:
    helpers = inputs.policy.data["helpers"]
    execution = inputs.policy.data["execution"]
    assert isinstance(helpers, dict)
    assert isinstance(execution, dict)

    def helper_descriptor(path_key: str) -> int:
        relative = str(helpers[path_key])
        return inputs.files[f"helper:{relative}"].descriptor

    launcher_descriptor = helper_descriptor("launcherPath")
    namespace_descriptor = helper_descriptor("namespacePath")
    runner_descriptor = helper_descriptor("runnerPath")
    seccomp_descriptor = helper_descriptor("seccompPath")
    bound = inputs.composition_inputs
    directories = inputs.directories
    preserved = (
        bound.apt_fd,
        bound.sdk_fd,
        directories["workspace"].descriptor,
        directories["source"].descriptor,
        directories["output"].descriptor,
        directories["home"].descriptor,
        directories["tmp"].descriptor,
        namespace_descriptor,
        runner_descriptor,
        seccomp_descriptor,
    )
    if len(preserved) != execution["preservedDescriptorCount"]:
        _integrity("native build preserved descriptor count differs from policy")
    pass_fds = (*preserved, launcher_descriptor, cgroup_procs_descriptor)
    if (
        len(set(pass_fds)) != len(pass_fds)
        or any(descriptor < 3 or descriptor == 255 for descriptor in pass_fds)
    ):
        _integrity("native build launch descriptors are duplicated or reserved")

    namespace_argv = [
        "/usr/bin/unshare",
        "--mount",
        "--net",
        "--pid",
        "--fork",
        "--kill-child=SIGKILL",
        "--uts",
        "--ipc",
        "--propagation",
        "private",
        "/bin/bash",
        "--noprofile",
        "--norc",
        f"/proc/self/fd/{namespace_descriptor}",
        native_executor_tool._namespace_path(bound.apt_root, "APT root"),  # noqa: SLF001
        native_executor_tool._namespace_path(bound.sdk_root, "SDK projection"),  # noqa: SLF001
        native_executor_tool._namespace_path(  # noqa: SLF001
            inputs.build_workspace,
            "native build workspace",
        ),
        *(str(descriptor) for descriptor in preserved),
        _identity_text(bound.apt_identity),
        _identity_text(bound.sdk_identity),
        *(
            _identity_text(directories[key].identity)
            for key in ("workspace", "source", "output", "home", "tmp")
        ),
        hashlib.sha256(inputs.files[f"helper:{helpers['namespacePath']}"].raw).hexdigest(),
        hashlib.sha256(inputs.files[f"helper:{helpers['runnerPath']}"].raw).hexdigest(),
        hashlib.sha256(inputs.files[f"helper:{helpers['seccompPath']}"].raw).hexdigest(),
        *(composition_tool._namespace_id(name) for name in ("mnt", "net", "pid", "uts", "ipc")),  # noqa: SLF001
        str(execution["namespaceProfile"]),
    ]
    script_index = namespace_argv.index(f"/proc/self/fd/{namespace_descriptor}")
    if len(namespace_argv[script_index + 1 :]) != execution["namespaceArgumentCount"]:
        _integrity("native build namespace argument count differs from policy")
    argv = [
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "-B",
        f"/proc/self/fd/{launcher_descriptor}",
        CHILD_PROFILE,
        str(os.getpid()),
        str(cgroup_procs_descriptor),
        str(launcher_descriptor),
        str(execution["rlimitNoFile"]),
        str(execution["rlimitFileSizeBytes"]),
        str(execution["rlimitCoreBytes"]),
        ",".join(str(descriptor) for descriptor in preserved),
        "--",
        *namespace_argv,
    ]
    return argv, pass_fds


def _diagnostic_tail(buffer: bytes | bytearray) -> str:
    return bytes(buffer[-4096:]).decode("utf-8", "replace").strip() or "<empty>"


def _run_locked_build(inputs: PinnedBuildInputs) -> BuildExecutionResult:
    _assert_pinned_build_inputs(inputs, strict_workspace=False)
    _assert_host_resource_limits(inputs.policy)
    signal_mask = getattr(signal, "pthread_sigmask", None)
    signal_block = getattr(signal, "SIG_BLOCK", None)
    signal_setmask = getattr(signal, "SIG_SETMASK", None)
    if (
        not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
        or signal_mask is None
        or signal_block is None
        or signal_setmask is None
    ):
        _schema("native build execution requires Linux pidfd and signal-mask control")
    execution = inputs.policy.data["execution"]
    filesystem_policy = inputs.policy.data["filesystem"]
    assert isinstance(execution, dict)
    assert isinstance(filesystem_policy, dict)
    timeout_seconds = int(execution["buildTimeoutSeconds"])
    stdout_limit = int(execution["stdoutLimitBytes"])
    stderr_limit = int(execution["stderrLimitBytes"])
    workspace_descriptor = inputs.directories["workspace"].descriptor
    workspace_source = Path(f"/proc/self/fd/{workspace_descriptor}")
    initial_filesystem = os.statvfs(workspace_source)
    initial_free_bytes = initial_filesystem.f_bavail * initial_filesystem.f_frsize
    if (
        initial_free_bytes < int(filesystem_policy["minimumFreeBytes"])
        or initial_filesystem.f_favail < int(filesystem_policy["minimumFreeInodes"])
    ):
        _integrity("native build workspace lacks the locked minimum capacity")

    cgroup: environment_tool.CgroupHandle | None = None
    process: subprocess.Popen[bytes] | None = None
    pidfd_reserve = -1
    pidfd = -1
    selector: selectors.BaseSelector | None = None
    streams: dict[BinaryIO, bytearray] = {}
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    previous_signal_mask: set[signal.Signals] | None = None
    failure: BaseException | None = None
    return_code = -1
    final_filesystem = initial_filesystem
    cgroup_empty_after_exit = False
    try:
        try:
            previous_signal_mask = signal_mask(signal_block, CONTROL_SIGNALS)
        except (OSError, ValueError) as error:
            _integrity(f"cannot block native build control signals: {error}")
        deadline = time.monotonic() + timeout_seconds
        try:
            pidfd_reserve = os.pidfd_open(os.getpid(), 0)
        except OSError as error:
            _integrity(f"cannot reserve native build pidfd capacity: {error}")
        cgroup = environment_tool._prepare_installer_cgroup(workspace_source)  # noqa: SLF001
        argv, pass_fds = _build_process_arguments(inputs, cgroup.procs_fd)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd="/",
                env=dict(BUILD_ENVIRONMENT),
                close_fds=True,
                pass_fds=pass_fds,
                start_new_session=True,
            )
        except OSError as error:
            _integrity(f"cannot launch locked native build: {error}")
        if process.stdout is None or process.stderr is None:
            _integrity("native build pipes were not created")
        streams = {process.stdout: stdout_buffer, process.stderr: stderr_buffer}
        try:
            os.close(pidfd_reserve)
            pidfd_reserve = -1
            pidfd = os.pidfd_open(process.pid, 0)
        except OSError as error:
            _integrity(f"cannot open native build leader pidfd: {error}")
        try:
            os.close(cgroup.procs_fd)
        except OSError as error:
            _integrity(f"cannot close parent cgroup.procs descriptor: {error}")
        finally:
            cgroup.procs_fd = -1
        selector = selectors.DefaultSelector()
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = source_tool.SourceToolError(
                    f"native build exceeded {timeout_seconds} seconds",
                    source_tool.EXIT_INTEGRITY,
                )
                break
            selected = (
                selector.select(timeout=min(0.25, remaining))
                if selector.get_map()
                else []
            )
            if not selector.get_map():
                time.sleep(min(0.05, remaining))
            for key, _events in selected:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                try:
                    native_executor_tool._append_bounded(  # noqa: SLF001
                        streams[stream],
                        chunk,
                        maximum=(stdout_limit if stream is process.stdout else stderr_limit),
                        label="build stdout" if stream is process.stdout else "build stderr",
                    )
                except source_tool.SourceToolError as error:
                    failure = error
                    break
            if failure is not None:
                break
            violation = environment_tool._cgroup_limit_violation(cgroup)  # noqa: SLF001
            if violation is not None:
                failure = environment_tool.InstallerIsolationError(
                    violation,
                    retain_staging=True,
                )
                break
            filesystem_violation = native_executor_tool._filesystem_violation(  # noqa: SLF001
                workspace_descriptor,
                initial_filesystem.f_favail,
                inputs.policy,  # type: ignore[arg-type]
            )
            if filesystem_violation is not None:
                failure = environment_tool.InstallerIsolationError(
                    filesystem_violation,
                    retain_staging=True,
                )
                break
        if failure is None:
            return_code = process.wait(timeout=10)
            if return_code != 0:
                stdout_tail = _diagnostic_tail(stdout_buffer)
                stderr_tail = _diagnostic_tail(stderr_buffer)
                failure = source_tool.SourceToolError(
                    f"locked native build exited {return_code}; "
                    f"stdout tail: {stdout_tail}; stderr tail: {stderr_tail}",
                    source_tool.EXIT_INTEGRITY,
                )
            else:
                violation = environment_tool._cgroup_limit_violation(cgroup)  # noqa: SLF001
                if violation is not None:
                    failure = environment_tool.InstallerIsolationError(
                        violation,
                        retain_staging=True,
                    )
                else:
                    cgroup_empty_after_exit = environment_tool._cgroup_is_empty(cgroup)  # noqa: SLF001
                    if not cgroup_empty_after_exit:
                        failure = environment_tool.InstallerIsolationError(
                            "native build left descendant processes running",
                            retain_staging=True,
                        )
                if failure is None:
                    filesystem_violation = native_executor_tool._filesystem_violation(  # noqa: SLF001
                        workspace_descriptor,
                        initial_filesystem.f_favail,
                        inputs.policy,  # type: ignore[arg-type]
                    )
                    if filesystem_violation is not None:
                        failure = environment_tool.InstallerIsolationError(
                            filesystem_violation,
                            retain_staging=True,
                        )
            final_filesystem = os.statvfs(workspace_source)
    except BaseException as error:
        failure = error
        if process is not None and process.returncode is not None:
            return_code = process.returncode
    finally:
        if cgroup is not None and cgroup.procs_fd >= 0:
            try:
                os.close(cgroup.procs_fd)
            except BaseException as close_error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    close_error,
                    "parent cgroup.procs cleanup also failed",
                )
            finally:
                cgroup.procs_fd = -1
        if cgroup is not None and not cgroup.removed:
            terminate = failure is not None
            if not terminate:
                try:
                    terminate = not environment_tool._cgroup_is_empty(cgroup)  # noqa: SLF001
                except BaseException as inspect_error:
                    failure = native_executor_tool._combine_failures(  # noqa: SLF001
                        failure,
                        inspect_error,
                        "cgroup population inspection failed before cleanup",
                    )
                    terminate = True
            try:
                if terminate:
                    native_executor_tool._terminate_probe_process(  # noqa: SLF001
                        process,
                        pidfd,
                        cgroup,
                    )
                else:
                    environment_tool._remove_installer_cgroup(cgroup)  # noqa: SLF001
            except BaseException as cleanup_error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    cleanup_error,
                    "native build cgroup cleanup also failed",
                )
        if selector is not None:
            try:
                selector.close()
            except BaseException as close_error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    close_error,
                    "selector cleanup also failed",
                )
        for stream in streams:
            if not stream.closed:
                try:
                    stream.close()
                except BaseException as close_error:
                    failure = native_executor_tool._combine_failures(  # noqa: SLF001
                        failure,
                        close_error,
                        "build stream cleanup also failed",
                    )
        for descriptor, context in (
            (pidfd, "pidfd cleanup also failed"),
            (pidfd_reserve, "reserved pidfd cleanup also failed"),
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except BaseException as close_error:
                    failure = native_executor_tool._combine_failures(  # noqa: SLF001
                        failure,
                        close_error,
                        context,
                    )
        if previous_signal_mask is not None:
            try:
                signal_mask(signal_setmask, previous_signal_mask)
            except BaseException as mask_error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    mask_error,
                    "control-signal mask restoration also failed",
                )
    if failure is not None:
        raise failure
    if cgroup is None or not cgroup.removed or return_code != 0:
        _integrity("native build lifecycle did not reach a clean zero-exit state")
    final_free_bytes = final_filesystem.f_bavail * final_filesystem.f_frsize
    return BuildExecutionResult(
        stdout=bytes(stdout_buffer),
        stderr=bytes(stderr_buffer),
        return_code=return_code,
        initial_free_bytes=initial_free_bytes,
        final_free_bytes=final_free_bytes,
        initial_free_inodes=initial_filesystem.f_favail,
        final_free_inodes=final_filesystem.f_favail,
        cgroup_empty_after_exit=cgroup_empty_after_exit,
        cgroup_removed=cgroup.removed,
    )


def _open_parent_beneath_descriptor(
    root_descriptor: int,
    relative: PurePosixPath,
    label: str,
) -> tuple[int, str]:
    if relative.is_absolute() or not relative.name or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        _integrity(f"{label} has an unsafe relative path")
    directory_flags = native_build_tool._directory_flags()  # noqa: SLF001
    descriptor = -1
    try:
        descriptor = os.dup(root_descriptor)
        root_info = os.fstat(descriptor)
        if not stat.S_ISDIR(root_info.st_mode):
            _integrity(f"{label} root descriptor is not a directory")
        for part in relative.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_dev != root_info.st_dev:
                _integrity(f"{label} traverses a non-directory or nested filesystem")
        return descriptor, relative.name
    except source_tool.SourceToolError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        _integrity(f"cannot resolve {label} beneath its pinned root: {error}")


def _digest_descriptor(descriptor: int, size: int, label: str) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not chunk:
            _integrity(f"{label} ended before its declared size")
        digest.update(chunk)
        offset += len(chunk)
    if os.pread(descriptor, 1, size):
        _integrity(f"{label} grew beyond its declared size")
    return digest.hexdigest()


def _open_pinned_artifact(
    *,
    abi: str,
    library: str,
    source_kind: str,
    root_path: Path,
    root_descriptor: int,
    logical_path: Path,
    maximum: int,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> PinnedArtifact:
    descriptor = -1
    parent_descriptor = -1
    label = f"{abi} {library} {source_kind} artifact"
    try:
        root_resolved = root_path.resolve(strict=True)
        root_current = root_path.lstat()
        root_opened = os.fstat(root_descriptor)
        if (
            root_resolved != root_path
            or root_path.is_symlink()
            or not stat.S_ISDIR(root_current.st_mode)
            or (root_current.st_dev, root_current.st_ino)
            != (root_opened.st_dev, root_opened.st_ino)
        ):
            _integrity(f"{label} root identity changed")
        logical_before = logical_path.lstat()
        if not (stat.S_ISREG(logical_before.st_mode) or stat.S_ISLNK(logical_before.st_mode)):
            _integrity(f"{label} logical source is not a regular file or symlink")
        resolved = logical_path.resolve(strict=True)
        try:
            relative = PurePosixPath(resolved.relative_to(root_resolved).as_posix())
        except ValueError:
            _integrity(f"{label} resolves outside its locked root")
        parent_descriptor, leaf = _open_parent_beneath_descriptor(
            root_descriptor,
            relative,
            label,
        )
        descriptor = os.open(
            leaf,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        path_info = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size < 0
            or opened.st_size > maximum
            or (opened.st_uid, opened.st_gid) != (0, 0)
            or opened.st_dev != root_opened.st_dev
            or _stat_signature(opened) != _stat_signature(path_info)
            or os.listxattr(descriptor)
        ):
            _integrity(f"{label} target metadata is unsafe")
        sha256 = _digest_descriptor(descriptor, opened.st_size, label)
        final = os.fstat(descriptor)
        logical_after = logical_path.lstat()
        resolved_after = logical_path.resolve(strict=True)
        if (
            _stat_signature(final) != _stat_signature(opened)
            or _stat_signature(logical_after) != _stat_signature(logical_before)
            or resolved_after != resolved
        ):
            _integrity(f"{label} changed while it was pinned")
        if expected_size is not None and opened.st_size != expected_size:
            _integrity(f"{label} size differs from the locked profile")
        if expected_sha256 is not None and sha256 != expected_sha256:
            _integrity(f"{label} digest differs from the locked profile")
        return PinnedArtifact(
            abi=abi,
            library=library,
            source_kind=source_kind,
            source_relative_path=logical_path.relative_to(root_path).as_posix(),
            descriptor=descriptor,
            identity=(opened.st_dev, opened.st_ino),
            size=opened.st_size,
            sha256=sha256,
            logical_path=logical_path,
            resolved_path=resolved,
            logical_signature=_stat_signature(logical_before),
        )
    except source_tool.SourceToolError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, RuntimeError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        _integrity(f"cannot pin {label}: {error}")
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _assert_pinned_artifact(artifact: PinnedArtifact) -> None:
    label = f"{artifact.abi} {artifact.library} {artifact.source_kind} artifact"
    try:
        opened = os.fstat(artifact.descriptor)
        logical = artifact.logical_path.lstat()
        resolved = artifact.logical_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        _integrity(f"cannot recheck {label}: {error}")
    if (
        not stat.S_ISREG(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != artifact.identity
        or opened.st_nlink != 1
        or opened.st_size != artifact.size
        or _stat_signature(logical) != artifact.logical_signature
        or resolved != artifact.resolved_path
        or _digest_descriptor(artifact.descriptor, artifact.size, label) != artifact.sha256
        or os.listxattr(artifact.descriptor)
    ):
        _integrity(f"{label} identity, metadata, or bytes changed")


def _host_path_from_mount(
    logical_path: PurePosixPath,
    mount: PurePosixPath,
    host_root: Path,
    label: str,
) -> Path:
    try:
        relative = logical_path.relative_to(mount)
    except ValueError:
        _schema(f"{label} is outside its locked mount")
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        _schema(f"{label} is not canonical")
    return host_root.joinpath(*relative.parts)


def _pin_expected_artifacts(inputs: PinnedBuildInputs) -> list[PinnedArtifact]:
    artifacts_policy = inputs.policy.data["artifacts"]
    filesystem = inputs.policy.data["filesystem"]
    binding = inputs.policy.data["binding"]
    assert isinstance(artifacts_policy, dict)
    assert isinstance(filesystem, dict)
    assert isinstance(binding, dict)
    output_fd = inputs.directories["output"].descriptor
    try:
        output_entries = os.listdir(output_fd)
    except OSError as error:
        _integrity(f"cannot inspect native build output before staging: {error}")
    if output_entries:
        _integrity("native build output is not empty before parent staging")

    expected = tuple(str(value) for value in artifacts_policy["expectedLibraries"])
    built = frozenset(str(value) for value in artifacts_policy["builtLibraries"])
    runtime = frozenset(str(value) for value in artifacts_policy["runtimeLibraries"])
    if built & runtime or built | runtime != frozenset(expected):
        _integrity("native build artifact source allowlists are not an exact partition")
    maximum_library = int(filesystem["maximumLibraryBytes"])
    maximum_total = int(filesystem["maximumArtifactBytes"])
    source_root = inputs.directories["source"]
    sdk_root = inputs.composition_inputs
    source_mount = PurePosixPath(str(binding["sourceMount"]))
    toolchain_mount = PurePosixPath(str(binding["toolchainMount"]))
    pinned: list[PinnedArtifact] = []
    total = 0
    try:
        for abi_record in inputs.policy.profile.abis:
            abi = str(abi_record["name"])
            built_root_logical = PurePosixPath(
                str(artifacts_policy["builtLibraryRootTemplate"]).format(
                    upstreamArch=abi_record["upstreamArch"]
                )
            )
            runtime_root_logical = PurePosixPath(
                str(artifacts_policy["runtimeLibraryRootTemplate"]).format(
                    ndkRuntimeDirectory=abi_record["ndkRuntimeDirectory"]
                )
            )
            built_root = _host_path_from_mount(
                built_root_logical,
                source_mount,
                source_root.path,
                f"{abi} built-library root",
            )
            runtime_root = _host_path_from_mount(
                runtime_root_logical,
                toolchain_mount,
                sdk_root.sdk_root,
                f"{abi} runtime-library root",
            )
            for library in expected:
                if library in built:
                    source_kind = "built"
                    root_path = source_root.path
                    root_fd = source_root.descriptor
                    logical_path = built_root / library
                    expected_size = None
                    expected_digest = None
                else:
                    source_kind = "locked-ndk-runtime"
                    root_path = sdk_root.sdk_root
                    root_fd = sdk_root.sdk_fd
                    logical_path = runtime_root / library
                    expected_size = int(abi_record["runtimeSize"])
                    expected_digest = str(abi_record["runtimeSha256"])
                artifact = _open_pinned_artifact(
                    abi=abi,
                    library=library,
                    source_kind=source_kind,
                    root_path=root_path,
                    root_descriptor=root_fd,
                    logical_path=logical_path,
                    maximum=maximum_library,
                    expected_size=expected_size,
                    expected_sha256=expected_digest,
                )
                total += artifact.size
                if total > maximum_total:
                    artifact.close()
                    _integrity("native build artifacts exceed the aggregate byte budget")
                pinned.append(artifact)
        return pinned
    except BaseException:
        for artifact in reversed(pinned):
            try:
                artifact.close()
            except OSError:
                pass
        raise


def _copy_pinned_artifact(
    artifact: PinnedArtifact,
    destination_parent_fd: int,
    *,
    mode: int,
    normalized_mtime_ns: int,
) -> tuple[int, int]:
    destination_fd = -1
    try:
        destination_fd = os.open(
            artifact.library,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=destination_parent_fd,
        )
        created = os.fstat(destination_fd)
        source_digest = hashlib.sha256()
        destination_digest = hashlib.sha256()
        offset = 0
        while offset < artifact.size:
            chunk = os.pread(
                artifact.descriptor,
                min(1024 * 1024, artifact.size - offset),
                offset,
            )
            if not chunk:
                _integrity(f"source artifact changed while staging: {artifact.library}")
            source_digest.update(chunk)
            native_build_tool._write_all(destination_fd, chunk)  # noqa: SLF001
            offset += len(chunk)
        if source_digest.hexdigest() != artifact.sha256:
            _integrity(f"source artifact digest changed while staging: {artifact.library}")
        os.fchown(destination_fd, 0, 0)
        os.fchmod(destination_fd, mode)
        os.utime(destination_fd, ns=(normalized_mtime_ns, normalized_mtime_ns))
        native_build_tool._clear_fd_xattrs(  # noqa: SLF001
            destination_fd,
            f"staged artifact {artifact.abi}/{artifact.library}",
        )
        os.fsync(destination_fd)
        offset = 0
        while offset < artifact.size:
            chunk = os.pread(
                destination_fd,
                min(1024 * 1024, artifact.size - offset),
                offset,
            )
            if not chunk:
                _integrity(f"staged artifact ended early: {artifact.library}")
            destination_digest.update(chunk)
            offset += len(chunk)
        final = os.fstat(destination_fd)
        path_info = os.stat(
            artifact.library,
            dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
        if (
            destination_digest.hexdigest() != artifact.sha256
            or not stat.S_ISREG(final.st_mode)
            or (final.st_dev, final.st_ino) != (created.st_dev, created.st_ino)
            or (path_info.st_dev, path_info.st_ino) != (created.st_dev, created.st_ino)
            or final.st_nlink != 1
            or final.st_size != artifact.size
            or stat.S_IMODE(final.st_mode) != mode
            or (final.st_uid, final.st_gid) != (0, 0)
            or final.st_mtime_ns != normalized_mtime_ns
            or os.listxattr(destination_fd)
        ):
            _integrity(f"staged artifact metadata or bytes differ: {artifact.library}")
        return created.st_dev, created.st_ino
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot stage {artifact.abi}/{artifact.library}: {error}")
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)


def _stage_artifacts(
    inputs: PinnedBuildInputs,
    artifacts: list[PinnedArtifact],
) -> None:
    policy = inputs.policy.data["artifacts"]
    assert isinstance(policy, dict)
    output = inputs.directories["output"]
    by_abi: dict[str, list[PinnedArtifact]] = {}
    for artifact in artifacts:
        by_abi.setdefault(artifact.abi, []).append(artifact)
    expected_abis = [str(record["name"]) for record in inputs.policy.profile.abis]
    if list(by_abi) != expected_abis:
        _integrity("native build artifact ABI order differs from the profile")
    for abi in expected_abis:
        _assert_mutable_pinned_directory(output)
        temporary = f".ziv-{abi}.part"
        native_build_tool._assert_absent_at(output.descriptor, temporary, f"{abi} staging tree")  # noqa: SLF001
        native_build_tool._assert_absent_at(output.descriptor, abi, f"{abi} output tree")  # noqa: SLF001
        temporary_fd = -1
        try:
            os.mkdir(temporary, mode=0o700, dir_fd=output.descriptor)
            temporary_fd = os.open(
                temporary,
                native_build_tool._directory_flags(),  # noqa: SLF001
                dir_fd=output.descriptor,
            )
            opened = os.fstat(temporary_fd)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_nlink != 2
                or opened.st_dev != output.identity[0]
                or (opened.st_uid, opened.st_gid) != (0, 0)
            ):
                _integrity(f"{abi} staging tree metadata is unsafe")
            identities: set[tuple[int, int]] = set()
            for artifact in by_abi[abi]:
                _assert_pinned_artifact(artifact)
                identity = _copy_pinned_artifact(
                    artifact,
                    temporary_fd,
                    mode=int(policy["libraryMode"]),
                    normalized_mtime_ns=int(policy["normalizedMtimeNs"]),
                )
                if identity in identities:
                    _integrity(f"{abi} output artifacts unexpectedly share an inode")
                identities.add(identity)
            entries = sorted(os.listdir(temporary_fd))
            expected_entries = sorted(artifact.library for artifact in by_abi[abi])
            if entries != expected_entries:
                _integrity(f"{abi} staging tree differs from the exact allowlist")
            os.fchown(temporary_fd, 0, 0)
            os.fchmod(temporary_fd, int(policy["abiDirectoryMode"]))
            normalized = int(policy["normalizedMtimeNs"])
            os.utime(temporary_fd, ns=(normalized, normalized))
            native_build_tool._clear_fd_xattrs(  # noqa: SLF001
                temporary_fd,
                f"{abi} staging tree",
            )
            os.fsync(temporary_fd)
            final_temporary = os.fstat(temporary_fd)
            temporary_path_info = os.stat(
                temporary,
                dir_fd=output.descriptor,
                follow_symlinks=False,
            )
            if (
                (final_temporary.st_dev, final_temporary.st_ino)
                != (temporary_path_info.st_dev, temporary_path_info.st_ino)
                or stat.S_IMODE(final_temporary.st_mode) != policy["abiDirectoryMode"]
                or (final_temporary.st_uid, final_temporary.st_gid) != (0, 0)
                or final_temporary.st_mtime_ns != normalized
                or os.listxattr(temporary_fd)
            ):
                _integrity(f"{abi} staging tree changed before publication")
            native_build_tool._rename_workspace_no_replace_at(  # noqa: SLF001
                output.descriptor,
                temporary,
                abi,
            )
            published = os.stat(abi, dir_fd=output.descriptor, follow_symlinks=False)
            if (
                not stat.S_ISDIR(published.st_mode)
                or (published.st_dev, published.st_ino)
                != (final_temporary.st_dev, final_temporary.st_ino)
                or stat.S_IMODE(published.st_mode) != policy["abiDirectoryMode"]
                or (published.st_uid, published.st_gid) != (0, 0)
                or published.st_mtime_ns != normalized
            ):
                _integrity(f"{abi} output tree changed during publication")
            os.fsync(output.descriptor)
            for artifact in by_abi[abi]:
                artifact.staged_path = output.path / abi / artifact.library
        except source_tool.SourceToolError:
            raise
        except OSError as error:
            _integrity(f"cannot publish {abi} output tree: {error}")
        finally:
            if temporary_fd >= 0:
                os.close(temporary_fd)


def _audit_child_setup(
    expected_parent: int,
    inherited_signal_mask: set[signal.Signals],
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    if os.getppid() != expected_parent:
        os.kill(os.getpid(), signal.SIGKILL)
    signal.pthread_sigmask(
        signal.SIG_SETMASK,
        set(inherited_signal_mask) - set(CONTROL_SIGNALS),
    )


def _terminate_audit_process(
    process: subprocess.Popen[bytes],
    pidfd: int,
) -> None:
    failure: BaseException | None = None
    if pidfd >= 0:
        try:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except BaseException as error:
            failure = error
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except BaseException as error:
        failure = native_executor_tool._combine_failures(  # noqa: SLF001
            failure,
            error,
            "ELF audit process-group kill also failed",
        )
    try:
        process.wait(timeout=10)
    except BaseException as error:
        failure = native_executor_tool._combine_failures(  # noqa: SLF001
            failure,
            error,
            "ELF audit process wait also failed",
        )
    if failure is not None:
        raise failure


def _run_audit_tool(
    tool_descriptor: int,
    target_descriptor: int,
    arguments: list[str],
    label: str,
) -> bytes:
    if (
        tool_descriptor < 3
        or target_descriptor < 3
        or 255 in {tool_descriptor, target_descriptor}
        or tool_descriptor == target_descriptor
    ):
        _integrity("ELF audit descriptors are duplicated or reserved")
    signal_mask = getattr(signal, "pthread_sigmask", None)
    signal_block = getattr(signal, "SIG_BLOCK", None)
    signal_setmask = getattr(signal, "SIG_SETMASK", None)
    if (
        not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
        or signal_mask is None
        or signal_block is None
        or signal_setmask is None
    ):
        _schema("ELF audit requires Linux pidfd and signal-mask control")
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    streams: dict[BinaryIO, bytearray] = {}
    stdout = bytearray()
    stderr = bytearray()
    failure: BaseException | None = None
    return_code = -1
    pidfd_reserve = -1
    pidfd = -1
    previous_signal_mask: set[signal.Signals] | None = None
    try:
        try:
            previous_signal_mask = signal_mask(signal_block, CONTROL_SIGNALS)
        except (OSError, ValueError) as error:
            _integrity(f"cannot block {label} audit control signals: {error}")
        deadline = time.monotonic() + AUDIT_TIMEOUT_SECONDS
        try:
            pidfd_reserve = os.pidfd_open(os.getpid(), 0)
        except OSError as error:
            _integrity(f"cannot reserve {label} audit pidfd capacity: {error}")
        parent_pid = os.getpid()
        assert previous_signal_mask is not None
        process = subprocess.Popen(
            [
                "llvm-readelf",
                *arguments,
                f"/proc/self/fd/{target_descriptor}",
            ],
            executable=f"/proc/self/fd/{tool_descriptor}",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=dict(BUILD_ENVIRONMENT),
            close_fds=True,
            pass_fds=(tool_descriptor, target_descriptor),
            start_new_session=True,
            preexec_fn=lambda: _audit_child_setup(parent_pid, previous_signal_mask),
        )
        if process.stdout is None or process.stderr is None:
            _integrity(f"{label} audit pipes were not created")
        streams = {process.stdout: stdout, process.stderr: stderr}
        try:
            os.close(pidfd_reserve)
            pidfd_reserve = -1
            pidfd = os.pidfd_open(process.pid, 0)
        except OSError as error:
            _integrity(f"cannot open {label} audit leader pidfd: {error}")
        selector = selectors.DefaultSelector()
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _integrity(f"{label} ELF audit exceeded {AUDIT_TIMEOUT_SECONDS} seconds")
            selected = (
                selector.select(timeout=min(0.25, remaining))
                if selector.get_map()
                else []
            )
            if not selector.get_map():
                time.sleep(min(0.05, remaining))
            for key, _events in selected:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                native_executor_tool._append_bounded(  # noqa: SLF001
                    streams[stream],
                    chunk,
                    maximum=MAX_AUDIT_OUTPUT_BYTES,
                    label=f"{label} audit {'stdout' if stream is process.stdout else 'stderr'}",
                )
        return_code = process.wait(timeout=10)
    except BaseException as error:
        failure = error
    finally:
        if process is not None and failure is not None:
            try:
                _terminate_audit_process(process, pidfd)
            except BaseException as error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    error,
                    f"{label} audit process cleanup also failed",
                )
        if selector is not None:
            try:
                selector.close()
            except BaseException as error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    error,
                    f"{label} audit selector cleanup also failed",
                )
        for stream in streams:
            if not stream.closed:
                try:
                    stream.close()
                except BaseException as error:
                    failure = native_executor_tool._combine_failures(  # noqa: SLF001
                        failure,
                        error,
                        f"{label} audit stream cleanup also failed",
                    )
        for descriptor, context in (
            (pidfd, f"{label} audit pidfd cleanup also failed"),
            (pidfd_reserve, f"{label} reserved audit pidfd cleanup also failed"),
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    failure = native_executor_tool._combine_failures(  # noqa: SLF001
                        failure,
                        error,
                        context,
                    )
        if previous_signal_mask is not None:
            try:
                signal_mask(signal_setmask, previous_signal_mask)
            except BaseException as error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    error,
                    f"{label} audit control-signal restoration also failed",
                )
    if failure is not None:
        raise failure
    if return_code != 0:
        tail = bytes(stderr[-4096:]).decode("utf-8", "replace").strip()
        _integrity(f"{label} ELF audit exited {return_code}; stderr tail: {tail}")
    if stderr:
        tail = bytes(stderr[-4096:]).decode("utf-8", "replace").strip()
        _integrity(f"{label} ELF audit emitted stderr: {tail}")
    return bytes(stdout)


def _single_readelf_field(lines: list[str], prefix: str, label: str) -> str:
    values = [line.split(":", 1)[1].strip() for line in lines if line.strip().startswith(prefix)]
    if len(values) != 1 or not values[0]:
        _integrity(f"{label} ELF audit has no unique {prefix.rstrip(':')} field")
    return values[0]


def _normalized_symbol(name: str) -> str:
    value = name.split()[0] if name.split() else ""
    return value.split("@", 1)[0]


def _android_ident_from_readelf(lines: list[str], label: str) -> dict[str, object] | None:
    indices = [
        index
        for index, line in enumerate(lines)
        if line.strip() == "Displaying notes found in: .note.android.ident"
    ]
    if not indices:
        return None
    if len(indices) != 1:
        _integrity(f"{label} has duplicate Android ident notes")
    description: str | None = None
    for line in lines[indices[0] + 1 : indices[0] + 8]:
        if "description data:" in line:
            description = line.split("description data:", 1)[1].strip()
            break
    if description is None or not re.fullmatch(r"(?:[0-9a-fA-F]{2})(?: [0-9a-fA-F]{2})*", description):
        _integrity(f"{label} Android ident note is malformed")
    raw = bytes.fromhex(description)
    if len(raw) < 68:
        _integrity(f"{label} Android ident note is too short")
    api_level = int.from_bytes(raw[:4], "little")
    ndk_version = raw[4:68].split(b"\0", 1)[0].decode("ascii", "strict")
    ndk_build = raw[68:132].split(b"\0", 1)[0].decode("ascii", "strict") if len(raw) >= 132 else ""
    return {
        "apiLevel": api_level,
        "ndkBuild": ndk_build,
        "ndkVersion": ndk_version,
    }


def _parse_readelf_output(raw: bytes, label: str) -> ParsedElf:
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        _integrity(f"{label} ELF audit output is not UTF-8: {error}")
    lines = text.splitlines()
    class_value = _single_readelf_field(lines, "Class:", label)
    type_value = _single_readelf_field(lines, "Type:", label)
    machine = _single_readelf_field(lines, "Machine:", label)
    if class_value not in {"ELF32", "ELF64"}:
        _integrity(f"{label} ELF class is unsupported: {class_value}")
    elf_type_token = type_value.split()[0]
    elf_type = "ET_DYN" if elf_type_token == "DYN" else elf_type_token
    load_segments: list[dict[str, int]] = []
    for line in lines:
        fields = line.split()
        if fields and fields[0] == "LOAD":
            if len(fields) < 7:
                _integrity(f"{label} has a malformed LOAD program header")
            try:
                offset = int(fields[1], 16)
                virtual_address = int(fields[2], 16)
                alignment = int(fields[-1], 16)
            except ValueError:
                _integrity(f"{label} has a non-hexadecimal LOAD program header")
            load_segments.append(
                {
                    "alignmentBytes": alignment,
                    "offsetBytes": offset,
                    "virtualAddress": virtual_address,
                }
            )
    sonames = []
    needed = []
    for line in lines:
        soname_match = re.search(r"\(SONAME\).*\[([^\]]+)\]", line)
        needed_match = re.search(r"\(NEEDED\).*\[([^\]]+)\]", line)
        if soname_match:
            sonames.append(soname_match.group(1))
        if needed_match:
            needed.append(needed_match.group(1))
    if len(sonames) != 1:
        _integrity(f"{label} must contain exactly one SONAME")
    if len(set(needed)) != len(needed):
        _integrity(f"{label} repeats a NEEDED entry")
    symbol_pattern = re.compile(
        r"^\s*\d+:\s+[0-9a-fA-F]+\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(.*?))?\s*$"
    )
    exports: set[str] = set()
    undefined: set[str] = set()
    for line in lines:
        match = symbol_pattern.match(line)
        if match is None:
            continue
        _symbol_type, binding, visibility, index, raw_name = match.groups()
        if binding not in {"GLOBAL", "WEAK"} or visibility not in {"DEFAULT", "PROTECTED"}:
            continue
        name = _normalized_symbol(raw_name or "")
        if not name:
            continue
        if index == "UND":
            undefined.add(name)
        else:
            exports.add(name)
    return ParsedElf(
        elf_class=int(class_value.removeprefix("ELF")),
        machine=machine,
        elf_type=elf_type,
        load_segments=tuple(load_segments),
        soname=sonames[0],
        needed=tuple(needed),
        exports=frozenset(exports),
        undefined=frozenset(undefined),
        android_ident=_android_ident_from_readelf(lines, label),
        readelf_size=len(raw),
        readelf_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _symbol_set_digest(symbols: set[str] | frozenset[str]) -> str:
    raw = json.dumps(
        sorted(symbols),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _audit_platform_stubs(
    inputs: PinnedBuildInputs,
    abi_record: dict[str, object],
) -> tuple[dict[str, ParsedElf], list[dict[str, object]]]:
    audit_policy = inputs.policy.data["audit"]
    filesystem = inputs.policy.data["filesystem"]
    binding = inputs.policy.data["binding"]
    assert isinstance(audit_policy, dict)
    assert isinstance(filesystem, dict)
    assert isinstance(binding, dict)
    abi = str(abi_record["name"])
    root_logical = PurePosixPath(
        str(audit_policy["platformStubRootTemplate"]).format(
            ndkRuntimeDirectory=abi_record["ndkRuntimeDirectory"]
        )
    )
    root_path = _host_path_from_mount(
        root_logical,
        PurePosixPath(str(binding["toolchainMount"])),
        inputs.composition_inputs.sdk_root,
        f"{abi} API 26 platform stub root",
    )
    tool = inputs.files["audit-tool"]
    arguments = [str(value) for value in audit_policy["elfToolArguments"]]
    parsed_by_soname: dict[str, ParsedElf] = {}
    evidence: list[dict[str, object]] = []
    for soname in audit_policy["platformSonameAllowlist"]:
        name = str(soname)
        stub = _open_pinned_artifact(
            abi=abi,
            library=name,
            source_kind="api26-platform-stub",
            root_path=inputs.composition_inputs.sdk_root,
            root_descriptor=inputs.composition_inputs.sdk_fd,
            logical_path=root_path / name,
            maximum=int(filesystem["maximumLibraryBytes"]),
        )
        try:
            output = _run_audit_tool(
                tool.descriptor,
                stub.descriptor,
                arguments,
                f"{abi} {name}",
            )
            parsed = _parse_readelf_output(output, f"{abi} {name}")
            if (
                parsed.elf_class != abi_record["elfClass"]
                or parsed.machine != abi_record["elfMachine"]
                or parsed.elf_type != audit_policy["elfType"]
                or parsed.soname != name
            ):
                _integrity(f"{abi} {name} platform stub identity differs")
            parsed_by_soname[name] = parsed
            evidence.append(
                {
                    "exportedSymbolCount": len(parsed.exports),
                    "exportedSymbolsSha256": _symbol_set_digest(parsed.exports),
                    "readelfSha256": parsed.readelf_sha256,
                    "sha256": stub.sha256,
                    "sizeBytes": stub.size,
                    "soname": name,
                }
            )
        finally:
            stub.close()
    return parsed_by_soname, evidence


def _validate_android_ident(artifact: PinnedArtifact, parsed: ParsedElf) -> None:
    if artifact.source_kind != "built" or parsed.android_ident is None:
        return
    ndk_version = str(parsed.android_ident["ndkVersion"])
    if not re.fullmatch(r"r29(?:[a-z0-9._-]*)?", ndk_version):
        _integrity(
            f"{artifact.abi} {artifact.library} Android ident does not report NDK r29"
        )


def _audit_artifacts(
    inputs: PinnedBuildInputs,
    artifacts: list[PinnedArtifact],
) -> list[dict[str, object]]:
    audit_policy = inputs.policy.data["audit"]
    artifact_policy = inputs.policy.data["artifacts"]
    filesystem = inputs.policy.data["filesystem"]
    assert isinstance(audit_policy, dict)
    assert isinstance(artifact_policy, dict)
    assert isinstance(filesystem, dict)
    tool = inputs.files["audit-tool"]
    arguments = [str(value) for value in audit_policy["elfToolArguments"]]
    expected_page_size = int(audit_policy["pageSizeBytes"])
    by_abi = {
        str(record["name"]): [artifact for artifact in artifacts if artifact.abi == record["name"]]
        for record in inputs.policy.profile.abis
    }
    platform_evidence: list[dict[str, object]] = []
    for abi_record in inputs.policy.profile.abis:
        abi = str(abi_record["name"])
        platform, abi_platform_evidence = _audit_platform_stubs(inputs, abi_record)
        platform_evidence.append({"abi": abi, "stubs": abi_platform_evidence})
        parsed_artifacts: dict[str, tuple[PinnedArtifact, ParsedElf]] = {}
        for artifact in by_abi[abi]:
            if artifact.staged_path is None:
                _integrity(f"{abi} {artifact.library} was not staged before audit")
            if artifact.staged_pin is not None:
                _integrity(f"{abi} {artifact.library} already has a staged audit pin")
            staged = _open_pinned_artifact(
                abi=abi,
                library=artifact.library,
                source_kind="staged",
                root_path=inputs.directories["output"].path,
                root_descriptor=inputs.directories["output"].descriptor,
                logical_path=artifact.staged_path,
                maximum=int(filesystem["maximumLibraryBytes"]),
                expected_size=artifact.size,
                expected_sha256=artifact.sha256,
            )
            artifact.staged_pin = staged
            staged_info = os.fstat(staged.descriptor)
            if (
                stat.S_IMODE(staged_info.st_mode) != artifact_policy["libraryMode"]
                or staged_info.st_mtime_ns != artifact_policy["normalizedMtimeNs"]
            ):
                _integrity(f"{abi} {artifact.library} staged metadata differs")
            output = _run_audit_tool(
                tool.descriptor,
                staged.descriptor,
                arguments,
                f"{abi} {artifact.library}",
            )
            parsed = _parse_readelf_output(output, f"{abi} {artifact.library}")
            if (
                parsed.elf_class != abi_record["elfClass"]
                or parsed.machine != abi_record["elfMachine"]
                or parsed.elf_type != audit_policy["elfType"]
            ):
                _integrity(f"{abi} {artifact.library} ELF identity differs from policy")
            if not parsed.load_segments or any(
                segment["alignmentBytes"] != expected_page_size
                or segment["offsetBytes"] % expected_page_size
                != segment["virtualAddress"] % expected_page_size
                for segment in parsed.load_segments
            ):
                _integrity(f"{abi} {artifact.library} is not 16 KiB page-size compatible")
            if parsed.soname != artifact.library or "/" in parsed.soname or "\\" in parsed.soname:
                _integrity(f"{abi} {artifact.library} SONAME is not its staged basename")
            jni_exports = sorted(symbol for symbol in parsed.exports if symbol.startswith("Java_"))
            if jni_exports:
                _integrity(f"{abi} {artifact.library} unexpectedly exports Java_ JNI symbols")
            _validate_android_ident(artifact, parsed)
            if parsed.soname in parsed_artifacts:
                _integrity(f"{abi} repeats staged SONAME {parsed.soname}")
            parsed_artifacts[parsed.soname] = (artifact, parsed)

        staged_sonames = set(parsed_artifacts)
        platform_sonames = set(platform)
        for artifact, parsed in parsed_artifacts.values():
            provider_kinds: list[dict[str, str]] = []
            provider_exports: dict[str, frozenset[str]] = {}
            for needed in parsed.needed:
                if "/" in needed or "\\" in needed or Path(needed).name != needed:
                    _integrity(f"{abi} {artifact.library} has unsafe NEEDED entry {needed}")
                if needed in parsed_artifacts:
                    provider_kinds.append({"kind": "staged", "soname": needed})
                    provider_exports[needed] = parsed_artifacts[needed][1].exports
                elif needed in platform:
                    provider_kinds.append({"kind": "api26-platform-stub", "soname": needed})
                    provider_exports[needed] = platform[needed].exports
                else:
                    _integrity(
                        f"{abi} {artifact.library} NEEDED entry is outside the closed set: {needed}"
                    )
            unresolved: set[str] = set()
            resolved_counts = {soname: 0 for soname in provider_exports}
            for symbol in parsed.undefined:
                providers = [
                    soname for soname, exports in provider_exports.items() if symbol in exports
                ]
                if not providers:
                    unresolved.add(symbol)
                else:
                    resolved_counts[sorted(providers)[0]] += 1
            if unresolved:
                sample = ", ".join(sorted(unresolved)[:8])
                _integrity(
                    f"{abi} {artifact.library} has undefined symbols absent from its API 26 dependency closure: {sample}"
                )
            artifact.audit = {
                "androidIdent": parsed.android_ident,
                "elfClass": parsed.elf_class,
                "elfMachine": parsed.machine,
                "elfType": parsed.elf_type,
                "jniExports": [],
                "loadSegments": list(parsed.load_segments),
                "needed": list(parsed.needed),
                "neededProviders": provider_kinds,
                "readelfSha256": parsed.readelf_sha256,
                "readelfSizeBytes": parsed.readelf_size,
                "resolvedSymbolProviders": [
                    {"resolvedSymbolCount": count, "soname": soname}
                    for soname, count in sorted(resolved_counts.items())
                ],
                "soname": parsed.soname,
                "undefinedSymbolCount": len(parsed.undefined),
                "undefinedSymbolsSha256": _symbol_set_digest(parsed.undefined),
            }
        if staged_sonames != set(str(value) for value in artifact_policy["expectedLibraries"]):
            _integrity(f"{abi} staged SONAME set differs from the exact allowlist")
        if platform_sonames != set(str(value) for value in audit_policy["platformSonameAllowlist"]):
            _integrity(f"{abi} platform SONAME set differs from the locked allowlist")
    return platform_evidence


def _reverify_staged_artifacts(
    inputs: PinnedBuildInputs,
    artifacts: list[PinnedArtifact],
) -> None:
    policy = inputs.policy.data["artifacts"]
    assert isinstance(policy, dict)
    output = inputs.directories["output"]
    expected_abis = [str(record["name"]) for record in inputs.policy.profile.abis]
    try:
        output_entries = sorted(os.listdir(output.descriptor))
    except OSError as error:
        _integrity(f"cannot recheck staged output inventory: {error}")
    if output_entries != sorted(expected_abis):
        _integrity("staged output ABI inventory differs from the exact allowlist")

    by_abi = {
        abi: [artifact for artifact in artifacts if artifact.abi == abi]
        for abi in expected_abis
    }
    staged_identities: set[tuple[int, int]] = set()
    for abi in expected_abis:
        abi_descriptor = -1
        try:
            abi_descriptor = os.open(
                abi,
                native_build_tool._directory_flags(),  # noqa: SLF001
                dir_fd=output.descriptor,
            )
            abi_info = os.fstat(abi_descriptor)
            abi_path_info = os.stat(
                abi,
                dir_fd=output.descriptor,
                follow_symlinks=False,
            )
            expected_libraries = sorted(artifact.library for artifact in by_abi[abi])
            if (
                not stat.S_ISDIR(abi_info.st_mode)
                or _stat_signature(abi_info) != _stat_signature(abi_path_info)
                or abi_info.st_dev != output.identity[0]
                or abi_info.st_nlink != 2
                or (abi_info.st_uid, abi_info.st_gid) != (0, 0)
                or stat.S_IMODE(abi_info.st_mode) != policy["abiDirectoryMode"]
                or abi_info.st_mtime_ns != policy["normalizedMtimeNs"]
                or os.listxattr(abi_descriptor)
                or sorted(os.listdir(abi_descriptor)) != expected_libraries
            ):
                _integrity(f"{abi} staged output tree changed after audit")
        except source_tool.SourceToolError:
            raise
        except OSError as error:
            _integrity(f"cannot recheck {abi} staged output tree: {error}")
        finally:
            if abi_descriptor >= 0:
                os.close(abi_descriptor)

        for artifact in by_abi[abi]:
            _assert_pinned_artifact(artifact)
            staged = artifact.staged_pin
            expected_path = output.path / abi / artifact.library
            if (
                staged is None
                or artifact.staged_path != expected_path
                or staged.logical_path != expected_path
                or staged.source_kind != "staged"
                or staged.size != artifact.size
                or staged.sha256 != artifact.sha256
                or staged.identity in staged_identities
            ):
                _integrity(f"{abi} {artifact.library} staged audit pin differs")
            _assert_pinned_artifact(staged)
            staged_identities.add(staged.identity)


def _build_receipt_data(
    inputs: PinnedBuildInputs,
    attempt_marker_sha256: str,
    execution_result: BuildExecutionResult,
    artifacts: list[PinnedArtifact],
    platform_evidence: list[dict[str, object]],
) -> dict[str, object]:
    policy = inputs.policy.data
    receipt_policy = policy["receipt"]
    binding = policy["binding"]
    helpers = policy["helpers"]
    execution = policy["execution"]
    assert isinstance(receipt_policy, dict)
    assert isinstance(binding, dict)
    assert isinstance(helpers, dict)
    assert isinstance(execution, dict)
    profile = inputs.policy.profile
    artifact_records = [
        {
            "abi": artifact.abi,
            "audit": artifact.audit,
            "library": artifact.library,
            "outputRelativePath": f"output/{artifact.abi}/{artifact.library}",
            "sha256": artifact.sha256,
            "sizeBytes": artifact.size,
            "sourceKind": artifact.source_kind,
            "sourceRelativePath": artifact.source_relative_path,
        }
        for artifact in artifacts
    ]
    if any(not record["audit"] for record in artifact_records):
        _integrity("native build receipt cannot record an unaudited artifact")
    overlay_records = [
        {
            "destination": str(overlay["destination"]),
            "replacement": str(overlay["replacement"]),
            "replacementSha256": str(overlay["replacementSha256"]),
            "replacementSize": int(overlay["replacementSize"]),
        }
        for overlay in profile.overlays
    ]
    result: dict[str, object] = {
        "artifactAudited": receipt_policy["artifactAudited"],
        "artifactStaged": receipt_policy["artifactStaged"],
        "artifacts": artifact_records,
        "attempt": {
            "kind": policy["workspace"]["attemptMarkerKind"],  # type: ignore[index]
            "sha256": attempt_marker_sha256,
            "state": policy["workspace"]["attemptMarkerState"],  # type: ignore[index]
        },
        "buildExecuted": receipt_policy["buildExecuted"],
        "buildOptionsAndOverlays": {
            "jobs": profile.policy["jobs"],
            "nativeApi": profile.project["nativeApi"],
            "networkAtBuild": profile.policy["networkAtBuild"],
            "overlays": overlay_records,
            "pageSizeBytes": profile.project["pageSizeBytes"],
            "sourceDateEpoch": profile.policy["sourceDateEpoch"],
            "target": profile.build["target"],
            "toolchain": {
                "hostPlatform": profile.toolchain["hostPlatform"],
                "ndkVersion": profile.toolchain["ndkVersion"],
                "sdkBuildTools": profile.toolchain["sdkBuildTools"],
                "sdkPlatform": profile.toolchain["sdkPlatform"],
            },
        },
        "commandEvidence": {
            "childStdoutMarkers": execution["childStdoutMarkers"],
            "commands": copy.deepcopy(profile.build["commands"]),
            "eventSource": execution["commandEventSource"],
            "runnerExitCode": execution_result.return_code,
            "stderr": {
                "sha256": hashlib.sha256(execution_result.stderr).hexdigest(),
                "sizeBytes": len(execution_result.stderr),
            },
            "stdout": {
                "sha256": hashlib.sha256(execution_result.stdout).hexdigest(),
                "sizeBytes": len(execution_result.stdout),
            },
        },
        "inputLocks": {
            "elfToolSha256": policy["audit"]["elfToolTargetSha256"],  # type: ignore[index]
            "helpers": {
                "launcherSha256": helpers["launcherSha256"],
                "namespaceSha256": helpers["namespaceSha256"],
                "runnerSha256": helpers["runnerSha256"],
                "seccompSha256": helpers["seccompSha256"],
            },
            "namespaceProbePolicySha256": binding["namespaceProbePolicySha256"],
            "namespaceProbeTranscriptSha256": binding["namespaceProbeTranscriptSha256"],
            "preparationReceiptSha256": hashlib.sha256(
                inputs.preparation_receipt_raw
            ).hexdigest(),
            "sourceManifestSha256": profile.project["sourceManifestSha256"],
            "sourceReceiptSha256": hashlib.sha256(
                inputs.preparation.source_receipt_raw
            ).hexdigest(),
            "toolchainCompositionReceiptSha256": hashlib.sha256(
                inputs.preparation.composition_receipt_raw
            ).hexdigest(),
            "toolchainManifestSha256": profile.project["toolchainManifestSha256"],
        },
        "kind": receipt_policy["kind"],
        "pendingReleaseBlockers": [
            profile.build["jniWrapperStatus"],
            profile.build["gradleIntegrationStatus"],
            profile.build["complianceStatus"],
            "pending-release-gate",
        ],
        "platformApi26": platform_evidence,
        "policySha256": inputs.policy.sha256,
        "profileName": binding["profileName"],
        "profileSha256": inputs.policy.profile.sha256,
        "ready": receipt_policy["ready"],
        "releaseInput": receipt_policy["releaseInput"],
        "resourceOutcome": {
            "cgroup": {
                "emptyAfterExit": execution_result.cgroup_empty_after_exit,
                "limits": copy.deepcopy(policy["cgroup"]),
                "removed": execution_result.cgroup_removed,
                "violations": [],
            },
            "filesystem": {
                "finalFreeBytes": execution_result.final_free_bytes,
                "finalFreeInodes": execution_result.final_free_inodes,
                "freeByteDelta": execution_result.final_free_bytes
                - execution_result.initial_free_bytes,
                "freeInodeDelta": execution_result.final_free_inodes
                - execution_result.initial_free_inodes,
                "initialFreeBytes": execution_result.initial_free_bytes,
                "initialFreeInodes": execution_result.initial_free_inodes,
            },
        },
        "schemaVersion": receipt_policy["schemaVersion"],
    }
    if tuple(sorted(result)) != BUILD_RECEIPT_FIELDS:
        _integrity("native build receipt top-level fields are not exact")
    if (
        len(artifact_records)
        != len(profile.abis) * len(profile.build["expectedLibraries"])
        or result["ready"] is not False
        or result["releaseInput"] is not False
    ):
        _integrity("native build receipt status or artifact count differs from policy")
    return result


def _publish_build_receipt(
    inputs: PinnedBuildInputs,
    receipt: dict[str, object],
) -> tuple[bytes, str]:
    policy = inputs.policy.data["receipt"]
    assert isinstance(policy, dict)
    raw = _canonical_json(receipt)
    if len(raw) > int(policy["maximumBytes"]):
        _integrity("native build receipt exceeds its byte budget")
    workspace = inputs.directories["workspace"]
    name = str(policy["name"])
    suffix = str(policy["temporarySuffix"])
    temporary = f".{name}-{secrets.token_hex(16)}{suffix}"
    descriptor = -1
    temporary_identity: tuple[int, int] | None = None
    published = False
    completed = False
    try:
        _assert_workspace_root_inventory(inputs, receipt_present=False)
        native_build_tool._assert_absent_at(  # noqa: SLF001
            workspace.descriptor,
            name,
            "native build receipt",
        )
        descriptor = os.open(
            temporary,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=workspace.descriptor,
        )
        created = os.fstat(descriptor)
        temporary_identity = (created.st_dev, created.st_ino)
        native_build_tool._write_all(descriptor, raw)  # noqa: SLF001
        os.fchown(descriptor, int(policy["ownerUid"]), int(policy["ownerGid"]))
        os.fchmod(descriptor, int(policy["mode"]))
        normalized = int(policy["normalizedMtimeNs"])
        os.utime(descriptor, ns=(normalized, normalized))
        native_build_tool._clear_fd_xattrs(descriptor, "native build receipt")  # noqa: SLF001
        os.fsync(descriptor)
        current = os.fstat(descriptor)
        staged_path_info = os.stat(
            temporary,
            dir_fd=workspace.descriptor,
            follow_symlinks=False,
        )
        readback = _pread_exact_large(
            descriptor,
            current.st_size,
            int(policy["maximumBytes"]),
            "native build receipt",
        )
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != temporary_identity
            or (staged_path_info.st_dev, staged_path_info.st_ino) != temporary_identity
            or current.st_nlink != policy["linkCount"]
            or stat.S_IMODE(current.st_mode) != policy["mode"]
            or (current.st_uid, current.st_gid)
            != (policy["ownerUid"], policy["ownerGid"])
            or current.st_mtime_ns != normalized
            or current.st_size != len(raw)
            or readback != raw
            or os.listxattr(descriptor)
        ):
            _integrity("staged native build receipt changed before publication")
        _link_descriptor_no_replace_at(
            descriptor,
            workspace.descriptor,
            name,
            "native build receipt",
        )
        published = True
        linked_temporary = os.stat(
            temporary,
            dir_fd=workspace.descriptor,
            follow_symlinks=False,
        )
        if (linked_temporary.st_dev, linked_temporary.st_ino) != temporary_identity:
            _integrity("native build receipt temporary name changed during publication")
        os.unlink(temporary, dir_fd=workspace.descriptor)
        published_info = os.stat(name, dir_fd=workspace.descriptor, follow_symlinks=False)
        published_fd_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(published_info.st_mode)
            or (published_info.st_dev, published_info.st_ino) != temporary_identity
            or _stat_signature(published_fd_info) != _stat_signature(published_info)
            or published_info.st_nlink != policy["linkCount"]
            or stat.S_IMODE(published_info.st_mode) != policy["mode"]
            or (published_info.st_uid, published_info.st_gid)
            != (policy["ownerUid"], policy["ownerGid"])
            or published_info.st_mtime_ns != normalized
            or published_info.st_size != len(raw)
        ):
            _integrity("native build receipt changed during publication")
        os.fsync(workspace.descriptor)
        _assert_workspace_root_inventory(inputs, receipt_present=True)
        receipt_pin = native_executor_tool.PinnedFile(
            path=inputs.build_workspace / name,
            descriptor=descriptor,
            identity=temporary_identity,
            stat_signature=_stat_signature(published_fd_info),
            raw=raw,
            label="native build receipt",
        )
        _assert_build_file_pin("build-receipt", receipt_pin)
        inputs.files["build-receipt"] = receipt_pin
        descriptor = -1
        _assert_mutable_pinned_directory(workspace)
        completed = True
        return raw, hashlib.sha256(raw).hexdigest()
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot publish native build receipt: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not completed and temporary_identity is not None:
            rollback_failures: list[str] = []
            for cleanup_name in ((name, temporary) if published else (temporary,)):
                try:
                    current = os.stat(
                        cleanup_name,
                        dir_fd=workspace.descriptor,
                        follow_symlinks=False,
                    )
                    if (current.st_dev, current.st_ino) == temporary_identity:
                        os.unlink(cleanup_name, dir_fd=workspace.descriptor)
                        os.fsync(workspace.descriptor)
                    else:
                        rollback_failures.append(f"{cleanup_name}: identity changed")
                except FileNotFoundError:
                    pass
                except OSError as error:
                    rollback_failures.append(f"{cleanup_name}: {error}")
            if rollback_failures:
                _integrity(
                    "native build receipt rollback could not be confirmed: "
                    + "; ".join(rollback_failures)
                )


def execute(
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
) -> dict[str, object]:
    native_build_tool._require_linux_root("native build execution")  # noqa: SLF001
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
        _integrity("native build profile changed during execution admission")
    preparation = native_build_tool._snapshot_preparation_inputs(  # noqa: SLF001
        profile,
        source_workspace,
        composition_receipt_path,
    )
    directory_baseline = native_executor_tool._snapshot_probe_directories(  # noqa: SLF001
        source_workspace,
        build_workspace,
    )
    _preparation_receipt, preparation_raw = native_build_tool._verify_prepared_workspace(  # noqa: SLF001
        build_workspace,
        preparation,
    )
    binding = policy.data["binding"]
    assert isinstance(binding, dict)
    if hashlib.sha256(preparation_raw).hexdigest() != binding["preparationReceiptSha256"]:
        _integrity("native build preparation receipt digest differs from policy")
    if (
        hashlib.sha256(preparation.composition_receipt_raw).hexdigest()
        != binding["toolchainCompositionReceiptSha256"]
    ):
        _integrity("toolchain composition receipt digest differs from build policy")
    _verify_io_device_scope(policy, apt_root, sdk_root, source_workspace, build_workspace)
    audit_tool_path, audit_tool_raw = _verify_audit_tool(policy, sdk_root)
    inputs = _pin_build_inputs(
        policy,
        preparation,
        preparation_raw,
        directory_baseline,
        audit_tool_path,
        audit_tool_raw,
        policy_path=policy_path,
        profile_path=profile_path,
        source_manifest_path=source_manifest_path,
        toolchain_manifest_path=toolchain_manifest_path,
        toolchain_cache=toolchain_cache,
        source_workspace=source_workspace,
        apt_root=apt_root,
        sdk_root=sdk_root,
        composition_receipt_path=composition_receipt_path,
        build_workspace=build_workspace,
    )
    artifacts: list[PinnedArtifact] = []
    failure: BaseException | None = None
    receipt: dict[str, object] | None = None
    receipt_sha256: str | None = None
    try:
        _reverify_build_inputs(
            inputs,
            policy_path=policy_path,
            profile_path=profile_path,
            source_manifest_path=source_manifest_path,
            toolchain_manifest_path=toolchain_manifest_path,
            source_cache=source_cache,
            toolchain_cache=toolchain_cache,
            source_workspace=source_workspace,
            apt_root=apt_root,
            sdk_root=sdk_root,
            composition_receipt_path=composition_receipt_path,
            require_prepared_workspace=True,
        )
        _attempt_raw, attempt_sha256 = _publish_attempt_marker(inputs)
        _reverify_build_inputs(
            inputs,
            policy_path=policy_path,
            profile_path=profile_path,
            source_manifest_path=source_manifest_path,
            toolchain_manifest_path=toolchain_manifest_path,
            source_cache=source_cache,
            toolchain_cache=toolchain_cache,
            source_workspace=source_workspace,
            apt_root=apt_root,
            sdk_root=sdk_root,
            composition_receipt_path=composition_receipt_path,
            require_prepared_workspace=False,
        )
        execution_result = _run_locked_build(inputs)
        _reverify_build_inputs(
            inputs,
            policy_path=policy_path,
            profile_path=profile_path,
            source_manifest_path=source_manifest_path,
            toolchain_manifest_path=toolchain_manifest_path,
            source_cache=source_cache,
            toolchain_cache=toolchain_cache,
            source_workspace=source_workspace,
            apt_root=apt_root,
            sdk_root=sdk_root,
            composition_receipt_path=composition_receipt_path,
            require_prepared_workspace=False,
        )
        artifacts = _pin_expected_artifacts(inputs)
        _stage_artifacts(inputs, artifacts)
        platform_evidence = _audit_artifacts(inputs, artifacts)
        _reverify_build_inputs(
            inputs,
            policy_path=policy_path,
            profile_path=profile_path,
            source_manifest_path=source_manifest_path,
            toolchain_manifest_path=toolchain_manifest_path,
            source_cache=source_cache,
            toolchain_cache=toolchain_cache,
            source_workspace=source_workspace,
            apt_root=apt_root,
            sdk_root=sdk_root,
            composition_receipt_path=composition_receipt_path,
            require_prepared_workspace=False,
        )
        _reverify_staged_artifacts(inputs, artifacts)
        receipt = _build_receipt_data(
            inputs,
            attempt_sha256,
            execution_result,
            artifacts,
            platform_evidence,
        )
        for artifact in reversed(artifacts):
            artifact.close()
        artifacts.clear()
        _receipt_raw, receipt_sha256 = _publish_build_receipt(inputs, receipt)
    except BaseException as error:
        failure = error
    finally:
        for artifact in reversed(artifacts):
            try:
                artifact.close()
            except BaseException as error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    error,
                    "artifact pin cleanup also failed",
                )
        try:
            inputs.close()
        except BaseException as error:
            if receipt_sha256 is None:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    error,
                    "native build input cleanup also failed",
                )
    if failure is not None:
        raise failure
    assert receipt is not None and receipt_sha256 is not None
    try:
        print(
            "native inspection build completed with a non-release receipt: "
            f"artifacts={len(receipt['artifacts'])}; "
            f"receipt={receipt_sha256}; ready=false; releaseInput=false"
        )
    except OSError:
        pass
    return receipt


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
    execute_command = commands.add_parser(
        "execute",
        help=(
            "consume the prepared workspace, run the two locked builds, stage and audit "
            "the exact artifact set, and publish a non-release receipt"
        ),
    )
    execute_command.add_argument(
        "--source-cache",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_CACHE,
    )
    execute_command.add_argument(
        "--toolchain-cache",
        type=Path,
        default=native_build_tool.DEFAULT_TOOLCHAIN_CACHE,
    )
    execute_command.add_argument(
        "--source-workspace",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_WORKSPACE,
    )
    execute_command.add_argument(
        "--apt-root",
        type=Path,
        default=native_build_tool.DEFAULT_APT_ROOT,
    )
    execute_command.add_argument(
        "--sdk-root",
        type=Path,
        default=native_build_tool.DEFAULT_SDK_ROOT,
    )
    execute_command.add_argument(
        "--composition-receipt",
        type=Path,
        default=native_build_tool.DEFAULT_COMPOSITION_RECEIPT,
    )
    execute_command.add_argument(
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
        elif arguments.command == "execute":
            execute(
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
