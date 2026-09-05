#!/usr/bin/env python3
"""Validate and run the locked wrapper-inclusive native inspection build."""

from __future__ import annotations

import argparse
import copy
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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn

import composition_tool
import environment_tool
import materialize_sources
import native_build_executor_tool as stack_executor
import native_build_tool
import native_executor_tool
import sdk_tool
import source_tool
import toolchain_tool
import wrapper_contract_core


NATIVE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = NATIVE_DIR.parent
DEFAULT_POLICY = NATIVE_DIR / "native-wrapper-build-executor-policy.toml"
DEFAULT_PROFILE = NATIVE_DIR / "native-wrapper-build-profile.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_TOOLCHAIN_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"

MAX_POLICY_BYTES = stack_executor.MAX_POLICY_BYTES
MAX_HELPER_BYTES = stack_executor.MAX_HELPER_BYTES
MAX_ELF_TOOL_BYTES = stack_executor.MAX_ELF_TOOL_BYTES
MAX_AUDIT_OUTPUT_BYTES = stack_executor.MAX_AUDIT_OUTPUT_BYTES
AUDIT_TIMEOUT_SECONDS = stack_executor.AUDIT_TIMEOUT_SECONDS
POLICY_KIND = "ziv-native-wrapper-build-executor-policy-v1"
CHILD_PROFILE = native_executor_tool.WRAPPER_CHILD_PROFILE
BUILD_ENVIRONMENT = dict(native_executor_tool.PROBE_ENVIRONMENT)
CONTROL_SIGNALS = native_executor_tool.CONTROL_SIGNALS
BUILD_RECEIPT_FIELDS = (*stack_executor.BUILD_RECEIPT_FIELDS, "wrapperContract")
ARTIFACT_AUDIT_FIELDS = stack_executor.ARTIFACT_AUDIT_FIELDS
JNI_EXPORT_FIELDS = (
    "binding",
    "name",
    "sectionIndex",
    "symbolType",
    "visibility",
)
WRAPPER_CONTRACT_FIELDS = (
    "bindingsClass",
    "fullProjectionSha256",
    "libraryName",
    "methodCount",
    "methodsProjectionSha256",
    "registrationMethodCount",
    "registrationProofScope",
    "runtimeRegistrationEvidence",
    "schemaVersion",
    "sourceInputs",
    "wireProjectionSha256",
)
PLATFORM_STUB_EVIDENCE_FIELDS = (
    "exportedSymbolCount",
    "exportedSymbolsSha256",
    "readelfSha256",
    "sha256",
    "sizeBytes",
    "soname",
)
PARENT_RUNNER_EVENT_FIELDS = (
    "event",
    "returnCode",
    "sequence",
)
EXPECTED_POLICY_SHA256 = (
    "1d46ecc732f4aa548fb77b2bf63e96be66b217a8aee869ce8bbcd4626ec1401c"
)
HELPER_KEYS = stack_executor.HELPER_KEYS


@dataclass(frozen=True)
class ParentRunnerEvent:
    sequence: int
    event: str
    return_code: int | None


@dataclass(frozen=True)
class WrapperBuildExecutionResult:
    stdout: bytes
    stderr: bytes
    return_code: int
    initial_free_bytes: int
    final_free_bytes: int
    initial_free_inodes: int
    final_free_inodes: int
    cgroup_empty_after_exit: bool
    cgroup_removed: bool
    parent_events: tuple[ParentRunnerEvent, ...]


def _expected_policy() -> dict[str, object]:
    result = copy.deepcopy(stack_executor.EXPECTED_POLICY)
    result["kind"] = POLICY_KIND
    result["binding"] = {
        "profileName": native_build_tool.WRAPPER_PROFILE_NAME,
        "profileSha256": native_executor_tool.WRAPPER_EXPECTED_PROFILE_SHA256,
        "namespaceProbePolicyPath": "native/native-wrapper-executor-policy.toml",
        "namespaceProbePolicySha256": native_executor_tool.WRAPPER_EXPECTED_POLICY_SHA256,
        "namespaceProbeTranscriptSha256": hashlib.sha256(
            native_executor_tool.WRAPPER_EXPECTED_PROBE_TRANSCRIPT
        ).hexdigest(),
        "preparationReceiptKind": native_build_tool.WRAPPER_PREPARATION_RECEIPT_KIND,
        "preparationReceiptSha256": (
            "e51dae00b7f145de9663ee1b90010bf9904f8775ff2f12624454040c941132b4"
        ),
        "toolchainCompositionReceiptSha256": (
            "e9b88860b8e6d043a80a7f3d6aa7e3e641574e7da4c66a0541db129a4e081989"
        ),
        "hostPlatform": "linux/amd64",
        "toolchainMount": "/opt/zivplayer/toolchain",
        "sourceMount": "/build/source",
        "outputMount": "/build/output",
        "homeMount": "/build/home",
        "temporaryMount": "/build/tmp",
        "wrapperInputMount": "/build/wrapper",
        "wrapperInputReadOnly": True,
    }
    gate = result["gate"]
    assert isinstance(gate, dict)
    gate["phase"] = "offline-wrapper-inspection-build"
    execution = result["execution"]
    assert isinstance(execution, dict)
    execution.update(
        {
            "commandsSource": "native-wrapper-build-profile",
            "namespaceProfile": "ziv-native-wrapper-build-namespace-execution-v1",
            "namespaceArgumentCount": 31,
            "preservedDescriptorCount": 11,
            "runnerInvocation": [
                "/run/ziv-native-wrapper-build-runner.bash",
                "--execute-locked-wrapper-build",
            ],
        }
    )
    workspace = result["workspace"]
    assert isinstance(workspace, dict)
    workspace.update(
        {
            "attemptMarkerName": "ziv-native-wrapper-build-attempt.json",
            "attemptMarkerKind": "ziv-native-wrapper-build-attempt-v1",
            "immutableInputsPostcondition": (
                "reverify-policy-profile-receipts-toolchain-helpers-wrapper-bytes-and-identity"
            ),
        }
    )
    artifacts = result["artifacts"]
    assert isinstance(artifacts, dict)
    artifacts["expectedLibraries"] = list(native_build_tool.WRAPPER_EXPECTED_LIBRARIES)
    artifacts["builtLibraries"] = list(
        native_build_tool.WRAPPER_EXPECTED_BUILT_LIBRARIES
    )
    audit = result["audit"]
    assert isinstance(audit, dict)
    audit.update(
        {
            "jniExportPolicy": (
                "require-wrapper-exact-jni-lifecycle-and-no-other-jni-or-java-prefix"
            ),
            "nativeApiEvidence": (
                "locked-api26-driver-plus-built-ident-api26-ndk-r29-and-api26-version-aware-symbol-closure"
            ),
            "androidIdentNotePolicy": (
                "require-built-artifact-api26-and-ndk-r29-record-locked-runtime"
            ),
        }
    )
    result["contract"] = {
        "schemaVersion": 1,
        "validation": "pinned-source-structure-v1",
        "bindingsClass": wrapper_contract_core.EXPECTED_BINDINGS_CLASS,
        "libraryName": wrapper_contract_core.EXPECTED_LIBRARY_NAME,
        "wrapperSoname": "libzivplayer_mpv.so",
        "methodCount": wrapper_contract_core.EXPECTED_METHOD_COUNT,
        "registrationMethodCount": wrapper_contract_core.EXPECTED_METHOD_COUNT,
        "requiredJniExports": ["JNI_OnLoad", "JNI_OnUnload"],
        "requiredSymbolType": "FUNC",
        "requiredBinding": "GLOBAL",
        "requiredVisibility": "DEFAULT",
        "requiredDefinition": "numeric-section-index",
        "forbiddenPrefixes": ["Java_"],
        "methodsProjectionSha256": (
            "856d5535a02d8570ce0c5f56e7c0a7b72ae4ef31ad84e2e96b7c49539ceacab3"
        ),
        "wireProjectionSha256": (
            "9b93c5e539535802455e06fca286c20d61d6668e0063190a47a1216902f90e0b"
        ),
        "fullProjectionSha256": (
            "fe7687bacc79e7c091dc7d95d300ff0f61cc3da49885b802ff14c93960f0a780"
        ),
        "registrationProofScope": (
            "pinned-source-table-plus-compiled-lifecycle-exports"
        ),
        "runtimeRegistrationEvidence": "pending-device-art-smoke",
    }
    receipt = result["receipt"]
    assert isinstance(receipt, dict)
    receipt.update(
        {
            "name": "ziv-native-wrapper-build-receipt.json",
            "kind": "ziv-native-wrapper-build-receipt-v1",
            "records": [
                (
                    "jni-lifecycle-export-and-pinned-source-registration-contract"
                    if value == "jni-export-observation"
                    else value
                )
                for value in receipt["records"]
            ],
            "pendingReleaseBlockers": [
                "pending-wrapper-runtime-validation",
                "pending-offline-closure",
                "pending-actual-build-graph",
                "pending-accepted-release-builder",
                "pending-release-gate",
            ],
        }
    )
    result["helpers"] = {
        "launcherPath": "native/toolchain/native-wrapper-executor-child.py",
        "launcherSha256": (
            "1264e5b7d40c3451ef82f42cb1145dfdbf7da65e91be20a34d185a6a084a37bb"
        ),
        "namespacePath": (
            "native/toolchain/native-wrapper-build-execution-namespace.bash"
        ),
        "namespaceSha256": (
            "90ce7e39bc784d42e7bda64457dd323ca436c3434a7bfb579b12ca04e0112ad9"
        ),
        "runnerPath": "native/toolchain/native-wrapper-build-runner.bash",
        "runnerSha256": (
            "1aa8282b5695b221e9c159afd50fdb53206453da847748c9afe86b6edfa78298"
        ),
        "seccompPath": "native/toolchain/install-seccomp.pl",
        "seccompSha256": (
            "ab2e6d21a2768a585b2bc53a2495c78f46ab9a4dbac32d09bf58e311091c597d"
        ),
    }
    return result


EXPECTED_POLICY = _expected_policy()


@dataclass(frozen=True)
class DynamicSymbol:
    name: str
    symbol_type: str
    binding: str
    visibility: str
    section_index: str


@dataclass(frozen=True)
class WrapperParsedElf:
    base: stack_executor.ParsedElf
    dynamic_symbols: tuple[DynamicSymbol, ...]


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def validate_policy_data(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        _schema("native wrapper build executor policy must be a table")
    stack_executor._assert_exact(  # noqa: SLF001
        data,
        EXPECTED_POLICY,
        "native wrapper build executor policy",
    )
    return copy.deepcopy(data)


def _stable_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    return stack_executor._stable_bytes(path, maximum=maximum, label=label)  # noqa: SLF001


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
    pairs = (
        (binding["profileName"], profile.project["profile"], "profile name"),
        (binding["profileSha256"], profile.sha256, "profile SHA-256"),
        (binding["hostPlatform"], profile.toolchain["hostPlatform"], "host platform"),
        (binding["toolchainMount"], profile.toolchain["mount"], "toolchain mount"),
        (binding["sourceMount"], profile.policy["sourceCopyMount"], "source mount"),
        (binding["outputMount"], profile.policy["outputMount"], "output mount"),
        (binding["homeMount"], profile.policy["buildHomeMount"], "HOME mount"),
        (binding["temporaryMount"], profile.policy["temporaryMount"], "temporary mount"),
        (binding["wrapperInputMount"], profile.policy["wrapperInputMount"], "wrapper mount"),
        (
            binding["wrapperInputReadOnly"],
            profile.policy["wrapperInputReadOnly"],
            "wrapper read-only policy",
        ),
        (
            binding["preparationReceiptKind"],
            native_build_tool.WRAPPER_PREPARATION_RECEIPT_KIND,
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
        (audit["pageSizeBytes"], profile.project["pageSizeBytes"], "page size"),
    )
    for actual, expected, label in pairs:
        if actual != expected:
            _integrity(f"native wrapper build executor {label} is not profile-bound")
    expected_abis = [
        {
            "name": abi["name"],
            "clangTriple": abi["clangTriple"],
            "elfClass": abi["elfClass"],
            "elfMachine": abi["elfMachine"],
        }
        for abi in profile.abis
    ]
    if audit["abi"] != expected_abis:
        _integrity("native wrapper build ABI audit is not profile-bound")
    expected_commands = [
        [
            "/build/source/buildscripts/buildall.sh",
            "--arch",
            "arm64",
            "mpv+zivplayer_mpv",
        ],
        [
            "/build/source/buildscripts/buildall.sh",
            "--arch",
            "x86_64",
            "mpv+zivplayer_mpv",
        ],
    ]
    if profile.build["commands"] != expected_commands:
        _integrity("native wrapper build commands are not the exact profile sequence")
    if (
        profile.sha256 != native_executor_tool.WRAPPER_EXPECTED_PROFILE_SHA256
        or len(profile.wrapper_inputs) != 6
        or len(profile.build["expectedLibraries"]) != 10
        or len(profile.abis) * len(profile.build["expectedLibraries"]) != 20
    ):
        _integrity("native wrapper build profile cardinality differs")


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
        "wrapper namespace-probe policy",
    )
    probe = native_executor_tool.load_execution_policy(
        probe_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    native_executor_tool._assert_runtime_policy(probe)  # noqa: SLF001
    if (
        not probe.contract.includes_wrapper
        or probe.sha256 != binding["namespaceProbePolicySha256"]
        or hashlib.sha256(
            native_executor_tool.WRAPPER_EXPECTED_PROBE_TRANSCRIPT
        ).hexdigest()
        != binding["namespaceProbeTranscriptSha256"]
    ):
        _integrity("accepted wrapper namespace-probe binding differs")
    return probe


def _contract_projections(contract_raw: bytes) -> tuple[dict[str, object], str, str, str]:
    try:
        parsed = tomllib.loads(contract_raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        _integrity(f"cannot parse pinned wrapper contract: {error}")
    methods = parsed.get("methods")
    wire = parsed.get("wire")
    if not isinstance(methods, list) or not isinstance(wire, dict):
        _integrity("pinned wrapper contract projections are malformed")
    projection = {
        "bindingsClass": parsed.get("bindingsClass"),
        "libraryName": parsed.get("libraryName"),
        "methodCount": len(methods),
        "methods": methods,
    }

    def digest(value: object) -> str:
        raw = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(raw).hexdigest()

    return parsed, digest(projection), digest(wire), digest(parsed)


def _wrapper_contract_evidence(
    profile: native_build_tool.LoadedProfile,
    wrapper_input_raws: tuple[bytes, ...],
    policy_data: dict[str, object],
) -> dict[str, object]:
    if len(wrapper_input_raws) != len(profile.wrapper_inputs):
        _integrity("wrapper contract input snapshot count differs")
    for record, raw in zip(profile.wrapper_inputs, wrapper_input_raws, strict=True):
        if (
            len(raw) != record["size"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            _integrity(
                f"wrapper contract source bytes differ: {record['destination']}"
            )
    by_destination = {
        str(record["destination"]): raw
        for record, raw in zip(profile.wrapper_inputs, wrapper_input_raws, strict=True)
    }
    expected_destinations = {
        "Android.mk",
        "Application.mk",
        "CMakeLists.txt",
        "MpvNativeBindings.kt",
        "jni-contract.toml",
        "zivplayer_mpv.cpp",
    }
    if set(by_destination) != expected_destinations:
        _integrity("wrapper contract input destination set differs")
    try:
        method_count = wrapper_contract_core.validate_sources(
            by_destination["jni-contract.toml"].decode("utf-8"),
            by_destination["zivplayer_mpv.cpp"].decode("utf-8"),
            by_destination["MpvNativeBindings.kt"].decode("utf-8"),
            by_destination["CMakeLists.txt"].decode("utf-8"),
        )
    except (UnicodeDecodeError, wrapper_contract_core.ContractError) as error:
        _integrity(f"pinned wrapper source contract differs: {error}")
    parsed, methods_sha, wire_sha, full_sha = _contract_projections(
        by_destination["jni-contract.toml"]
    )
    contract = policy_data["contract"]
    assert isinstance(contract, dict)
    if (
        parsed.get("schemaVersion") != contract["schemaVersion"]
        or parsed.get("bindingsClass") != contract["bindingsClass"]
        or parsed.get("libraryName") != contract["libraryName"]
        or method_count != contract["methodCount"]
        or method_count != contract["registrationMethodCount"]
        or methods_sha != contract["methodsProjectionSha256"]
        or wire_sha != contract["wireProjectionSha256"]
        or full_sha != contract["fullProjectionSha256"]
    ):
        _integrity("pinned wrapper contract fingerprint differs from policy")
    source_inputs = [
        {
            "destination": str(record["destination"]),
            "mode": int(record["mode"]),
            "role": str(record["role"]),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "sizeBytes": len(raw),
            "source": str(record["source"]),
        }
        for record, raw in zip(profile.wrapper_inputs, wrapper_input_raws, strict=True)
    ]
    return {
        "bindingsClass": contract["bindingsClass"],
        "fullProjectionSha256": full_sha,
        "libraryName": contract["libraryName"],
        "methodCount": method_count,
        "methodsProjectionSha256": methods_sha,
        "registrationMethodCount": method_count,
        "registrationProofScope": contract["registrationProofScope"],
        "runtimeRegistrationEvidence": contract["runtimeRegistrationEvidence"],
        "schemaVersion": contract["schemaVersion"],
        "sourceInputs": source_inputs,
        "wireProjectionSha256": wire_sha,
    }


def load_build_execution_policy(
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
) -> stack_executor.LoadedBuildExecutorPolicy:
    raw = _stable_bytes(
        policy_path,
        maximum=MAX_POLICY_BYTES,
        label="native wrapper build executor policy",
    )
    sha256 = hashlib.sha256(raw).hexdigest()
    if sha256 != EXPECTED_POLICY_SHA256:
        _integrity("native wrapper build executor policy digest differs")
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        _schema(f"cannot parse native wrapper build executor policy: {error}")
    data = validate_policy_data(parsed)
    profile = native_build_tool.load_profile(
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    _assert_profile_binding(data, profile)
    probe = _load_probe_policy(
        data,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    stack_executor._assert_runtime_binding(data, probe)  # noqa: SLF001
    helpers = stack_executor._load_helpers(data)  # noqa: SLF001
    wrapper_raws = native_build_tool._wrapper_input_snapshots(profile)  # noqa: SLF001
    _wrapper_contract_evidence(profile, wrapper_raws, data)
    return stack_executor.LoadedBuildExecutorPolicy(
        data=data,
        raw=raw,
        sha256=sha256,
        profile=profile,
        probe_policy=probe,
        helper_raws=helpers,
    )


def _pinned_wrapper_raws(
    inputs: stack_executor.PinnedBuildInputs,
) -> tuple[bytes, ...]:
    result: list[bytes] = []
    for record in inputs.policy.profile.wrapper_inputs:
        destination = str(record["destination"])
        key = f"wrapper:{destination}"
        pinned = inputs.files.get(key)
        if pinned is None:
            _integrity(f"prepared wrapper input is not pinned: {destination}")
        result.append(pinned.raw)
    return tuple(result)


def _assert_workspace_root_inventory(
    inputs: stack_executor.PinnedBuildInputs,
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
        "wrapper",
        native_build_tool.PREPARATION_RECEIPT_NAME,
        str(workspace_policy["attemptMarkerName"]),
    }
    if receipt_present:
        expected.add(str(receipt_policy["name"]))
    try:
        actual = set(os.listdir(inputs.directories["workspace"].descriptor))
    except OSError as error:
        _integrity(f"cannot inspect native wrapper build workspace inventory: {error}")
    if actual != expected:
        _integrity(
            "native wrapper build workspace inventory differs from its attempt state"
        )


def _pin_build_inputs(
    policy: stack_executor.LoadedBuildExecutorPolicy,
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
) -> stack_executor.PinnedBuildInputs:
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
            label="native wrapper build workspace",
            expected_mode=0o700,
        )
        directories["workspace"] = workspace
        for name, mode in (
            ("source", 0o755),
            ("output", 0o700),
            ("home", 0o700),
            ("tmp", 0o700),
            ("wrapper", 0o555),
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
            _integrity("native wrapper build manifests differ from the locked profile")

        direct_files = (
            (
                "policy",
                policy_path,
                policy.raw,
                "native wrapper build executor policy",
                False,
            ),
            (
                "profile",
                profile_path,
                policy.profile.raw,
                "native wrapper build profile",
                False,
            ),
            (
                "source-manifest",
                source_manifest_path,
                source_manifest_raw,
                "source manifest",
                False,
            ),
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
            label="native wrapper build preparation receipt",
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
        wrapper = directories["wrapper"]
        for index, (record, raw) in enumerate(
            zip(policy.profile.wrapper_inputs, preparation.wrapper_input_raws, strict=True)
        ):
            destination = str(record["destination"])
            files[f"wrapper:{destination}"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
                build_workspace / "wrapper" / destination,
                raw,
                label=f"prepared wrapperInput[{index}] {destination}",
                parent_descriptor=wrapper.descriptor,
                child_name=destination,
            )
        for relative, raw in policy.helper_raws.items():
            path = native_build_tool._resolve_repository_file(  # noqa: SLF001
                REPOSITORY_ROOT,
                relative,
                f"wrapper build executor helper {relative}",
            )
            files[f"helper:{relative}"] = native_executor_tool._open_pinned_file(  # noqa: SLF001
                path,
                raw,
                label=f"wrapper build executor helper {relative}",
            )
        files["audit-tool"] = stack_executor._open_large_pinned_file(  # noqa: SLF001
            audit_tool_path,
            audit_tool_raw,
            maximum=MAX_ELF_TOOL_BYTES,
            label="locked ELF audit tool target",
        )
        inputs = stack_executor.PinnedBuildInputs(
            policy=policy,
            preparation=preparation,
            preparation_receipt_raw=preparation_receipt_raw,
            build_workspace=build_workspace,
            directories=directories,
            files=files,
            composition_inputs=bound,
        )
        _assert_pinned_build_inputs(inputs, strict_workspace=True)
        _wrapper_contract_evidence(
            policy.profile,
            _pinned_wrapper_raws(inputs),
            policy.data,
        )
        return inputs
    except BaseException:
        native_executor_tool._close_partial_pins(  # noqa: SLF001
            files,
            directories,
            bound,
        )
        raise


def _assert_pinned_build_inputs(
    inputs: stack_executor.PinnedBuildInputs,
    *,
    strict_workspace: bool,
) -> None:
    for key, pinned in inputs.directories.items():
        if strict_workspace or key in {"canonical-source", "wrapper"}:
            native_executor_tool._assert_pinned_directory(pinned)  # noqa: SLF001
        else:
            stack_executor._assert_mutable_pinned_directory(pinned)  # noqa: SLF001
    for key, pinned in inputs.files.items():
        stack_executor._assert_build_file_pin(key, pinned)  # noqa: SLF001
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
        _integrity("native wrapper build inputs no longer share one I/O block device")
    helpers = inputs.policy.data["helpers"]
    execution = inputs.policy.data["execution"]
    assert isinstance(helpers, dict)
    assert isinstance(execution, dict)
    preserved = (
        bound.apt_fd,
        bound.sdk_fd,
        *(
            inputs.directories[key].descriptor
            for key in ("workspace", "source", "output", "home", "tmp", "wrapper")
        ),
        *(
            inputs.files[f"helper:{helpers[path_key]}"].descriptor
            for path_key in ("namespacePath", "runnerPath", "seccompPath")
        ),
    )
    if (
        len(preserved) != execution["preservedDescriptorCount"]
        or len(set(preserved)) != len(preserved)
        or any(
            descriptor < 3
            or descriptor == 255
            or len(str(descriptor)) > 9
            for descriptor in preserved
        )
    ):
        _integrity(
            "native wrapper build preserved descriptors are duplicated or outside policy"
        )


def _reverify_build_inputs(
    inputs: stack_executor.PinnedBuildInputs,
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
        _integrity("native wrapper build policy inputs changed during execution")
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
        _integrity("native wrapper build profile changed during execution")
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
            _integrity("native wrapper build preparation receipt changed before execution")
    else:
        snapshot = native_build_tool._verify_wrapper_input_tree(  # noqa: SLF001
            inputs.build_workspace / "wrapper",
            current.profile,
            current.wrapper_input_raws,
        )
        if (
            snapshot is None
            or snapshot.identities["."] != inputs.directories["wrapper"].identity
        ):
            _integrity("prepared wrapper input root identity changed during execution")
    _wrapper_contract_evidence(
        profile,
        _pinned_wrapper_raws(inputs),
        inputs.policy.data,
    )
    composition_tool._reverify_bound_inputs(  # noqa: SLF001
        toolchain_manifest_path,
        source_manifest_path,
        toolchain_cache,
        inputs.composition_inputs,
    )
    stack_executor._verify_io_device_scope(  # noqa: SLF001
        inputs.policy,
        apt_root,
        sdk_root,
        source_workspace,
        inputs.build_workspace,
    )
    audit_path, audit_raw = stack_executor._verify_audit_tool(  # noqa: SLF001
        inputs.policy,
        sdk_root,
    )
    audit_pin = inputs.files["audit-tool"]
    if audit_path != audit_pin.path or audit_raw != audit_pin.raw:
        _integrity("locked ELF audit tool changed during wrapper build")
    _assert_pinned_build_inputs(inputs, strict_workspace=require_prepared_workspace)
    if not require_prepared_workspace:
        _assert_workspace_root_inventory(inputs, receipt_present=False)


def _build_process_arguments(
    inputs: stack_executor.PinnedBuildInputs,
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
        directories["wrapper"].descriptor,
        namespace_descriptor,
        runner_descriptor,
        seccomp_descriptor,
    )
    if len(preserved) != execution["preservedDescriptorCount"]:
        _integrity("native wrapper build preserved descriptor count differs")
    pass_fds = (*preserved, launcher_descriptor, cgroup_procs_descriptor)
    if (
        len(set(pass_fds)) != len(pass_fds)
        or any(
            descriptor < 3
            or descriptor == 255
            or len(str(descriptor)) > 9
            for descriptor in pass_fds
        )
    ):
        _integrity("native wrapper build launch descriptors are duplicated or reserved")

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
            "native wrapper build workspace",
        ),
        *(str(descriptor) for descriptor in preserved),
        stack_executor._identity_text(bound.apt_identity),  # noqa: SLF001
        stack_executor._identity_text(bound.sdk_identity),  # noqa: SLF001
        *(
            stack_executor._identity_text(directories[key].identity)  # noqa: SLF001
            for key in ("workspace", "source", "output", "home", "tmp", "wrapper")
        ),
        hashlib.sha256(
            inputs.files[f"helper:{helpers['namespacePath']}"].raw
        ).hexdigest(),
        hashlib.sha256(
            inputs.files[f"helper:{helpers['runnerPath']}"].raw
        ).hexdigest(),
        hashlib.sha256(
            inputs.files[f"helper:{helpers['seccompPath']}"].raw
        ).hexdigest(),
        *(
            composition_tool._namespace_id(name)  # noqa: SLF001
            for name in ("mnt", "net", "pid", "uts", "ipc")
        ),
        str(execution["namespaceProfile"]),
    ]
    script_index = namespace_argv.index(f"/proc/self/fd/{namespace_descriptor}")
    if len(namespace_argv[script_index + 1 :]) != execution["namespaceArgumentCount"]:
        _integrity("native wrapper build namespace argument count differs")
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


def _terminate_wrapper_build_process(
    process: subprocess.Popen[bytes] | None,
    pidfd: int,
    cgroup: environment_tool.CgroupHandle,
) -> None:
    """Apply the shared pidfd/cgroup teardown contract to the wrapper build."""
    native_executor_tool._terminate_probe_process(  # noqa: SLF001
        process,
        pidfd,
        cgroup,
    )


def _run_locked_build(
    inputs: stack_executor.PinnedBuildInputs,
) -> WrapperBuildExecutionResult:
    _assert_pinned_build_inputs(inputs, strict_workspace=False)
    stack_executor._assert_host_resource_limits(inputs.policy)  # noqa: SLF001
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
        _schema("native wrapper build requires Linux pidfd and signal-mask control")
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
        _integrity("native wrapper build workspace lacks the locked minimum capacity")

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
    parent_events: list[ParentRunnerEvent] = []
    try:
        try:
            previous_signal_mask = signal_mask(signal_block, CONTROL_SIGNALS)
        except (OSError, ValueError) as error:
            _integrity(f"cannot block native wrapper build control signals: {error}")
        deadline = time.monotonic() + timeout_seconds
        try:
            pidfd_reserve = os.pidfd_open(os.getpid(), 0)
        except OSError as error:
            _integrity(f"cannot reserve native wrapper build pidfd capacity: {error}")
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
            _integrity(f"cannot launch locked native wrapper build: {error}")
        parent_events.append(
            ParentRunnerEvent(
                sequence=1,
                event="runner-launched",
                return_code=None,
            )
        )
        if process.stdout is None or process.stderr is None:
            _integrity("native wrapper build pipes were not created")
        streams = {process.stdout: stdout_buffer, process.stderr: stderr_buffer}
        try:
            os.close(pidfd_reserve)
            pidfd_reserve = -1
            pidfd = os.pidfd_open(process.pid, 0)
        except OSError as error:
            _integrity(f"cannot open native wrapper build leader pidfd: {error}")
        try:
            os.close(cgroup.procs_fd)
        except OSError as error:
            _integrity(f"cannot close parent wrapper build cgroup.procs: {error}")
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
                    f"native wrapper build exceeded {timeout_seconds} seconds",
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
                        maximum=(
                            stdout_limit if stream is process.stdout else stderr_limit
                        ),
                        label=(
                            "wrapper build stdout"
                            if stream is process.stdout
                            else "wrapper build stderr"
                        ),
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
            parent_events.append(
                ParentRunnerEvent(
                    sequence=2,
                    event="runner-exited",
                    return_code=return_code,
                )
            )
            if return_code != 0:
                stdout_tail = stack_executor._diagnostic_tail(stdout_buffer)  # noqa: SLF001
                stderr_tail = stack_executor._diagnostic_tail(stderr_buffer)  # noqa: SLF001
                failure = source_tool.SourceToolError(
                    f"locked native wrapper build exited {return_code}; "
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
                            "native wrapper build left descendant processes running",
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
                    "parent wrapper build cgroup.procs cleanup also failed",
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
                        "wrapper build cgroup inspection failed before cleanup",
                    )
                    terminate = True
            try:
                if terminate:
                    _terminate_wrapper_build_process(
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
                    "native wrapper build cgroup cleanup also failed",
                )
        if selector is not None:
            try:
                selector.close()
            except BaseException as close_error:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    close_error,
                    "wrapper build selector cleanup also failed",
                )
        for stream in streams:
            if not stream.closed:
                try:
                    stream.close()
                except BaseException as close_error:
                    failure = native_executor_tool._combine_failures(  # noqa: SLF001
                        failure,
                        close_error,
                        "wrapper build stream cleanup also failed",
                    )
        for descriptor, context in (
            (pidfd, "wrapper build pidfd cleanup also failed"),
            (pidfd_reserve, "reserved wrapper build pidfd cleanup also failed"),
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
                    "wrapper build signal-mask restoration also failed",
                )
    if failure is not None:
        raise failure
    if cgroup is None or not cgroup.removed or return_code != 0:
        _integrity("native wrapper build did not reach a clean zero-exit state")
    final_free_bytes = final_filesystem.f_bavail * final_filesystem.f_frsize
    return WrapperBuildExecutionResult(
        stdout=bytes(stdout_buffer),
        stderr=bytes(stderr_buffer),
        return_code=return_code,
        initial_free_bytes=initial_free_bytes,
        final_free_bytes=final_free_bytes,
        initial_free_inodes=initial_filesystem.f_favail,
        final_free_inodes=final_filesystem.f_favail,
        cgroup_empty_after_exit=cgroup_empty_after_exit,
        cgroup_removed=cgroup.removed,
        parent_events=tuple(parent_events),
    )


def _parse_dynamic_symbols(raw: bytes, label: str) -> tuple[DynamicSymbol, ...]:
    try:
        lines = raw.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as error:
        _integrity(f"{label} ELF audit output is not UTF-8: {error}")
    header_pattern = re.compile(
        r"^\s*Symbol table '\.dynsym' contains (\d+) entries:\s*$"
    )
    headers = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := header_pattern.fullmatch(line)) is not None
    ]
    if len(headers) != 1:
        _integrity(f"{label} ELF audit must contain exactly one .dynsym table")
    header_index, header = headers[0]
    expected_count = int(header.group(1))
    if expected_count <= 0:
        _integrity(f"{label} ELF audit .dynsym table must not be empty")
    row_prefix = re.compile(r"^\s*\d+:")
    rows: list[str] = []
    rows_ended = False
    for line in lines[header_index + 1 :]:
        is_row = row_prefix.match(line) is not None
        if not rows and not is_row:
            continue
        if is_row:
            if rows_ended:
                _integrity(
                    f"{label} ELF audit .dynsym rows are not one contiguous table"
                )
            rows.append(line)
        else:
            rows_ended = True
    if len(rows) != expected_count:
        _integrity(
            f"{label} ELF audit .dynsym rows differ from the declared count"
        )
    row_pattern = re.compile(
        r"^\s*(\d+):\s+[0-9a-fA-F]+\s+\d+\s+(\S+)\s+(\S+)\s+"
        r"(\S+)\s+(\S+)(?:\s+(.*?))?\s*$"
    )
    symbols: list[DynamicSymbol] = []
    for expected_index, line in enumerate(rows):
        match = row_pattern.fullmatch(line)
        if match is None:
            _integrity(f"{label} ELF audit has a malformed .dynsym row")
        (
            row_index,
            symbol_type,
            binding,
            visibility,
            section_index,
            raw_name,
        ) = match.groups()
        if int(row_index) != expected_index:
            _integrity(f"{label} ELF audit .dynsym indices are not contiguous")
        name = stack_executor._normalized_symbol(raw_name or "")  # noqa: SLF001
        if name:
            symbols.append(
                DynamicSymbol(
                    name=name,
                    symbol_type=symbol_type,
                    binding=binding,
                    visibility=visibility,
                    section_index=section_index,
                )
            )
    return tuple(symbols)


def _parse_wrapper_readelf_output(raw: bytes, label: str) -> WrapperParsedElf:
    return WrapperParsedElf(
        base=stack_executor._parse_readelf_output(raw, label),  # noqa: SLF001
        dynamic_symbols=_parse_dynamic_symbols(raw, label),
    )


def _jni_export_evidence(
    inputs: stack_executor.PinnedBuildInputs,
    artifact: stack_executor.PinnedArtifact,
    parsed: WrapperParsedElf,
) -> list[dict[str, str]]:
    contract = inputs.policy.data["contract"]
    assert isinstance(contract, dict)
    forbidden_prefixes = tuple(str(value) for value in contract["forbiddenPrefixes"])
    forbidden = [
        symbol.name
        for symbol in parsed.dynamic_symbols
        if symbol.name.startswith(forbidden_prefixes)
    ]
    if forbidden:
        sample = ", ".join(sorted(forbidden)[:8])
        _integrity(
            f"{artifact.abi} {artifact.library} contains forbidden dynamic symbols: {sample}"
        )
    jni_symbols = [
        symbol for symbol in parsed.dynamic_symbols if symbol.name.startswith("JNI_")
    ]
    is_wrapper = artifact.library == contract["wrapperSoname"]
    if not is_wrapper:
        if jni_symbols:
            sample = ", ".join(sorted(symbol.name for symbol in jni_symbols)[:8])
            _integrity(
                f"{artifact.abi} {artifact.library} unexpectedly contains JNI symbols: {sample}"
            )
        return []

    required = [str(value) for value in contract["requiredJniExports"]]
    by_name: dict[str, list[DynamicSymbol]] = {}
    for symbol in jni_symbols:
        by_name.setdefault(symbol.name, []).append(symbol)
    if set(by_name) != set(required) or any(
        len(by_name[name]) != 1 for name in by_name
    ):
        _integrity(
            f"{artifact.abi} {artifact.library} JNI lifecycle export set differs"
        )
    evidence: list[dict[str, str]] = []
    for name in sorted(required):
        symbol = by_name[name][0]
        if (
            symbol.symbol_type != contract["requiredSymbolType"]
            or symbol.binding != contract["requiredBinding"]
            or symbol.visibility != contract["requiredVisibility"]
            or re.fullmatch(r"[1-9][0-9]*", symbol.section_index) is None
        ):
            _integrity(
                f"{artifact.abi} {artifact.library} {name} definition differs"
            )
        record = {
            "binding": symbol.binding,
            "name": symbol.name,
            "sectionIndex": symbol.section_index,
            "symbolType": symbol.symbol_type,
            "visibility": symbol.visibility,
        }
        if tuple(sorted(record)) != JNI_EXPORT_FIELDS:
            _integrity("wrapper JNI lifecycle evidence fields differ")
        evidence.append(record)
    return evidence


def _validate_android_ident(
    artifact: stack_executor.PinnedArtifact,
    android_ident: object,
) -> None:
    if artifact.source_kind != "built":
        return
    if not isinstance(android_ident, dict):
        _integrity(
            f"{artifact.abi} {artifact.library} has no Android ident build note"
        )
    if set(android_ident) != {"apiLevel", "ndkBuild", "ndkVersion"}:
        _integrity(
            f"{artifact.abi} {artifact.library} Android ident fields differ"
        )
    if android_ident["apiLevel"] != 26:
        _integrity(
            f"{artifact.abi} {artifact.library} Android ident is not API 26"
        )
    ndk_version = str(android_ident["ndkVersion"])
    if not re.fullmatch(r"r29(?:[a-z0-9._-]*)?", ndk_version):
        _integrity(
            f"{artifact.abi} {artifact.library} Android ident is not NDK r29"
        )


def _audit_artifacts(
    inputs: stack_executor.PinnedBuildInputs,
    artifacts: list[stack_executor.PinnedArtifact],
) -> list[dict[str, object]]:
    audit_policy = inputs.policy.data["audit"]
    artifact_policy = inputs.policy.data["artifacts"]
    contract = inputs.policy.data["contract"]
    filesystem = inputs.policy.data["filesystem"]
    assert isinstance(audit_policy, dict)
    assert isinstance(artifact_policy, dict)
    assert isinstance(contract, dict)
    assert isinstance(filesystem, dict)
    tool = inputs.files["audit-tool"]
    arguments = [str(value) for value in audit_policy["elfToolArguments"]]
    expected_page_size = int(audit_policy["pageSizeBytes"])
    by_abi = {
        str(record["name"]): [
            artifact for artifact in artifacts if artifact.abi == record["name"]
        ]
        for record in inputs.policy.profile.abis
    }
    platform_evidence: list[dict[str, object]] = []
    for abi_record in inputs.policy.profile.abis:
        abi = str(abi_record["name"])
        platform, abi_platform_evidence = stack_executor._audit_platform_stubs(  # noqa: SLF001
            inputs,
            abi_record,
        )
        platform_evidence.append({"abi": abi, "stubs": abi_platform_evidence})
        parsed_artifacts: dict[
            str,
            tuple[stack_executor.PinnedArtifact, WrapperParsedElf],
        ] = {}
        for artifact in by_abi[abi]:
            if artifact.staged_path is None:
                _integrity(f"{abi} {artifact.library} was not staged before audit")
            if artifact.staged_pin is not None:
                _integrity(f"{abi} {artifact.library} already has a staged audit pin")
            staged = stack_executor._open_pinned_artifact(  # noqa: SLF001
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
            output = stack_executor._run_audit_tool(  # noqa: SLF001
                tool.descriptor,
                staged.descriptor,
                arguments,
                f"{abi} {artifact.library}",
            )
            parsed = _parse_wrapper_readelf_output(output, f"{abi} {artifact.library}")
            base = parsed.base
            if (
                base.elf_class != abi_record["elfClass"]
                or base.machine != abi_record["elfMachine"]
                or base.elf_type != audit_policy["elfType"]
            ):
                _integrity(f"{abi} {artifact.library} ELF identity differs")
            if not base.load_segments or any(
                segment["alignmentBytes"] != expected_page_size
                or segment["offsetBytes"] % expected_page_size
                != segment["virtualAddress"] % expected_page_size
                for segment in base.load_segments
            ):
                _integrity(
                    f"{abi} {artifact.library} is not 16 KiB page-size compatible"
                )
            if (
                base.soname != artifact.library
                or "/" in base.soname
                or "\\" in base.soname
            ):
                _integrity(f"{abi} {artifact.library} SONAME is not its basename")
            if artifact.library == contract["wrapperSoname"] and (
                base.soname != contract["wrapperSoname"]
            ):
                _integrity(f"{abi} wrapper SONAME differs from its contract")
            jni_exports = _jni_export_evidence(inputs, artifact, parsed)
            _validate_android_ident(artifact, base.android_ident)
            if base.soname in parsed_artifacts:
                _integrity(f"{abi} repeats staged SONAME {base.soname}")
            parsed_artifacts[base.soname] = (artifact, parsed)
            artifact.audit = {"jniExports": jni_exports}

        staged_sonames = set(parsed_artifacts)
        platform_sonames = set(platform)
        for artifact, parsed in parsed_artifacts.values():
            base = parsed.base
            provider_kinds: list[dict[str, str]] = []
            provider_exports: dict[str, frozenset[str]] = {}
            for needed in base.needed:
                if "/" in needed or "\\" in needed or Path(needed).name != needed:
                    _integrity(
                        f"{abi} {artifact.library} has unsafe NEEDED entry {needed}"
                    )
                if needed in parsed_artifacts:
                    provider_kinds.append({"kind": "staged", "soname": needed})
                    provider_exports[needed] = parsed_artifacts[needed][1].base.exports
                elif needed in platform:
                    provider_kinds.append(
                        {"kind": "api26-platform-stub", "soname": needed}
                    )
                    provider_exports[needed] = platform[needed].exports
                else:
                    _integrity(
                        f"{abi} {artifact.library} NEEDED is outside the closed set: {needed}"
                    )
            resolved_counts, unresolved_weak = stack_executor._resolve_artifact_symbols(  # noqa: SLF001
                base,
                provider_exports,
                stack_executor._allowed_unresolved_weak_symbols(  # noqa: SLF001
                    audit_policy,
                    abi,
                    artifact.library,
                ),
                f"{abi} {artifact.library}",
            )
            artifact.audit.update(
                {
                    "androidIdent": base.android_ident,
                    "elfClass": base.elf_class,
                    "elfMachine": base.machine,
                    "elfType": base.elf_type,
                    "loadSegments": list(base.load_segments),
                    "needed": list(base.needed),
                    "neededProviders": provider_kinds,
                    "readelfSha256": base.readelf_sha256,
                    "readelfSizeBytes": base.readelf_size,
                    "resolvedSymbolProviders": [
                        {"resolvedSymbolCount": count, "soname": soname}
                        for soname, count in sorted(resolved_counts.items())
                    ],
                    "soname": base.soname,
                    "strongUndefinedSymbolCount": len(base.strong_undefined),
                    "strongUndefinedSymbolsSha256": stack_executor._symbol_set_digest(  # noqa: SLF001
                        base.strong_undefined
                    ),
                    "undefinedSymbolCount": len(base.undefined),
                    "undefinedSymbolsSha256": stack_executor._symbol_set_digest(  # noqa: SLF001
                        base.undefined
                    ),
                    "unresolvedWeakSymbols": unresolved_weak,
                    "weakUndefinedSymbolCount": len(base.weak_undefined),
                    "weakUndefinedSymbolsSha256": stack_executor._symbol_set_digest(  # noqa: SLF001
                        base.weak_undefined
                    ),
                }
            )
            if tuple(sorted(artifact.audit)) != ARTIFACT_AUDIT_FIELDS:
                _integrity(
                    f"{abi} {artifact.library} artifact audit fields differ"
                )
        if staged_sonames != set(
            str(value) for value in artifact_policy["expectedLibraries"]
        ):
            _integrity(f"{abi} staged SONAME set differs from the allowlist")
        if platform_sonames != set(
            str(value) for value in audit_policy["platformSonameAllowlist"]
        ):
            _integrity(f"{abi} platform SONAME set differs from the allowlist")
    return platform_evidence


def _build_receipt_data(
    inputs: stack_executor.PinnedBuildInputs,
    attempt_marker_sha256: str,
    execution_result: WrapperBuildExecutionResult,
    artifacts: list[stack_executor.PinnedArtifact],
    platform_evidence: list[dict[str, object]],
) -> dict[str, object]:
    policy = inputs.policy.data
    receipt_policy = policy["receipt"]
    binding = policy["binding"]
    helpers = policy["helpers"]
    execution = policy["execution"]
    contract = policy["contract"]
    assert isinstance(receipt_policy, dict)
    assert isinstance(binding, dict)
    assert isinstance(helpers, dict)
    assert isinstance(execution, dict)
    assert isinstance(contract, dict)
    expected_parent_events = (
        ParentRunnerEvent(
            sequence=1,
            event="runner-launched",
            return_code=None,
        ),
        ParentRunnerEvent(
            sequence=2,
            event="runner-exited",
            return_code=0,
        ),
    )
    if (
        re.fullmatch(r"[0-9a-f]{64}", attempt_marker_sha256) is None
        or not isinstance(execution_result.stdout, bytes)
        or not isinstance(execution_result.stderr, bytes)
        or len(execution_result.stdout) > int(execution["stdoutLimitBytes"])
        or len(execution_result.stderr) > int(execution["stderrLimitBytes"])
        or execution_result.return_code != 0
        or execution_result.parent_events != expected_parent_events
        or not execution_result.cgroup_empty_after_exit
        or not execution_result.cgroup_removed
        or any(
            type(value) is not int or value < 0
            for value in (
                execution_result.initial_free_bytes,
                execution_result.final_free_bytes,
                execution_result.initial_free_inodes,
                execution_result.final_free_inodes,
            )
        )
    ):
        _integrity("native wrapper build successful execution evidence differs")
    parent_event_records = [
        {
            "event": event.event,
            "returnCode": event.return_code,
            "sequence": event.sequence,
        }
        for event in execution_result.parent_events
    ]
    if any(
        tuple(sorted(record)) != PARENT_RUNNER_EVENT_FIELDS
        for record in parent_event_records
    ):
        _integrity("native wrapper build parent runner event fields differ")
    profile = inputs.policy.profile
    expected_order = [
        (str(abi["name"]), str(library))
        for abi in profile.abis
        for library in profile.build["expectedLibraries"]
    ]
    actual_order = [(artifact.abi, artifact.library) for artifact in artifacts]
    if actual_order != expected_order:
        _integrity("native wrapper build artifact order differs from the profile")
    abi_by_name = {str(record["name"]): record for record in profile.abis}
    built_libraries = {str(value) for value in profile.build["builtLibraries"]}
    staged_sonames = {str(value) for value in profile.build["expectedLibraries"]}
    platform_sonames = {
        str(value) for value in policy["audit"]["platformSonameAllowlist"]  # type: ignore[index]
    }
    maximum_library_bytes = int(policy["filesystem"]["maximumLibraryBytes"])  # type: ignore[index]
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
    wrapper_count = 0
    for artifact, record in zip(artifacts, artifact_records, strict=True):
        audit = record["audit"]
        if not isinstance(audit, dict) or tuple(sorted(audit)) != ARTIFACT_AUDIT_FIELDS:
            _integrity("native wrapper build artifact audit fields differ")
        abi_record = abi_by_name[artifact.abi]
        expected_source_kind = (
            "built" if artifact.library in built_libraries else "locked-ndk-runtime"
        )
        if expected_source_kind == "built":
            source_mount = PurePosixPath(str(binding["sourceMount"]))
            source_root = PurePosixPath(
                str(policy["artifacts"]["builtLibraryRootTemplate"]).format(  # type: ignore[index]
                    upstreamArch=abi_record["upstreamArch"]
                )
            )
        else:
            source_mount = PurePosixPath(str(binding["toolchainMount"]))
            source_root = PurePosixPath(
                str(policy["artifacts"]["runtimeLibraryRootTemplate"]).format(  # type: ignore[index]
                    ndkRuntimeDirectory=abi_record["ndkRuntimeDirectory"]
                )
            )
        try:
            expected_source_relative_path = (
                source_root.relative_to(source_mount) / artifact.library
            ).as_posix()
        except ValueError:
            _integrity(
                f"{artifact.abi} {artifact.library} source root escapes its mount"
            )
        source_relative_path = PurePosixPath(artifact.source_relative_path)
        if (
            artifact.source_kind != expected_source_kind
            or type(artifact.size) is not int
            or artifact.size <= 0
            or artifact.size > maximum_library_bytes
            or re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None
            or source_relative_path.is_absolute()
            or artifact.source_relative_path != source_relative_path.as_posix()
            or artifact.source_relative_path != expected_source_relative_path
            or any(part in {"", ".", ".."} for part in source_relative_path.parts)
            or source_relative_path.name != artifact.library
        ):
            _integrity(
                f"{artifact.abi} {artifact.library} receipt artifact identity differs"
            )
        load_segments = audit["loadSegments"]
        if (
            audit["elfClass"] != abi_record["elfClass"]
            or audit["elfMachine"] != abi_record["elfMachine"]
            or audit["elfType"] != policy["audit"]["elfType"]  # type: ignore[index]
            or audit["soname"] != artifact.library
            or not isinstance(load_segments, list)
            or not load_segments
            or any(
                not isinstance(segment, dict)
                or set(segment)
                != {"alignmentBytes", "offsetBytes", "virtualAddress"}
                or segment["alignmentBytes"] != profile.project["pageSizeBytes"]
                or segment["offsetBytes"] % profile.project["pageSizeBytes"]
                != segment["virtualAddress"] % profile.project["pageSizeBytes"]
                for segment in load_segments
            )
        ):
            _integrity(
                f"{artifact.abi} {artifact.library} receipt ELF evidence differs"
            )
        digest_fields = (
            "readelfSha256",
            "strongUndefinedSymbolsSha256",
            "undefinedSymbolsSha256",
            "weakUndefinedSymbolsSha256",
        )
        count_fields = (
            "strongUndefinedSymbolCount",
            "undefinedSymbolCount",
            "weakUndefinedSymbolCount",
        )
        needed = audit["needed"]
        needed_providers = audit["neededProviders"]
        resolved_providers = audit["resolvedSymbolProviders"]
        if (
            any(
                not isinstance(audit[field], str)
                or re.fullmatch(r"[0-9a-f]{64}", audit[field]) is None
                for field in digest_fields
            )
            or type(audit["readelfSizeBytes"]) is not int
            or not 0 < audit["readelfSizeBytes"] <= MAX_AUDIT_OUTPUT_BYTES
            or any(
                type(audit[field]) is not int or audit[field] < 0
                for field in count_fields
            )
            or not isinstance(needed, list)
            or any(
                not isinstance(value, str)
                or not value
                or PurePosixPath(value).name != value
                or "/" in value
                or "\\" in value
                for value in needed
            )
            or not isinstance(needed_providers, list)
            or len(needed_providers) != len(needed)
            or any(
                not isinstance(provider, dict)
                or set(provider) != {"kind", "soname"}
                or provider["soname"] != needed[index]
                or provider["kind"]
                != (
                    "staged"
                    if needed[index] in staged_sonames
                    else "api26-platform-stub"
                    if needed[index] in platform_sonames
                    else None
                )
                for index, provider in enumerate(needed_providers)
            )
            or not isinstance(resolved_providers, list)
            or [
                provider.get("soname") if isinstance(provider, dict) else None
                for provider in resolved_providers
            ]
            != sorted(set(needed))
            or any(
                not isinstance(provider, dict)
                or set(provider) != {"resolvedSymbolCount", "soname"}
                or type(provider["resolvedSymbolCount"]) is not int
                or provider["resolvedSymbolCount"] < 0
                for provider in resolved_providers
            )
        ):
            _integrity(
                f"{artifact.abi} {artifact.library} receipt dependency evidence differs"
            )
        _validate_android_ident(artifact, audit["androidIdent"])
        jni_exports = audit["jniExports"]
        if artifact.library == contract["wrapperSoname"]:
            wrapper_count += 1
            if (
                not isinstance(jni_exports, list)
                or any(
                    not isinstance(value, dict)
                    or tuple(sorted(value)) != JNI_EXPORT_FIELDS
                    for value in jni_exports
                )
                or [value["name"] for value in jni_exports]
                != sorted(str(value) for value in contract["requiredJniExports"])
                or any(
                    value["binding"] != contract["requiredBinding"]
                    or value["symbolType"] != contract["requiredSymbolType"]
                    or value["visibility"] != contract["requiredVisibility"]
                    or re.fullmatch(r"[1-9][0-9]*", str(value["sectionIndex"]))
                    is None
                    for value in jni_exports
                )
            ):
                _integrity("wrapper artifact JNI lifecycle receipt evidence differs")
        elif jni_exports != []:
            _integrity("non-wrapper artifact unexpectedly records JNI exports")
    if wrapper_count != len(profile.abis):
        _integrity("wrapper artifact count differs from the ABI count")

    expected_platform_sonames = [
        str(value) for value in policy["audit"]["platformSonameAllowlist"]  # type: ignore[index]
    ]
    if (
        not isinstance(platform_evidence, list)
        or len(platform_evidence) != len(profile.abis)
    ):
        _integrity("API 26 platform evidence ABI count differs")
    for abi_record, evidence in zip(profile.abis, platform_evidence, strict=True):
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {"abi", "stubs"}
            or evidence["abi"] != abi_record["name"]
            or not isinstance(evidence["stubs"], list)
            or [stub.get("soname") if isinstance(stub, dict) else None for stub in evidence["stubs"]]
            != expected_platform_sonames
            or any(
                not isinstance(stub, dict)
                or tuple(sorted(stub)) != PLATFORM_STUB_EVIDENCE_FIELDS
                or type(stub["exportedSymbolCount"]) is not int
                or stub["exportedSymbolCount"] < 0
                or type(stub["sizeBytes"]) is not int
                or stub["sizeBytes"] <= 0
                or stub["sizeBytes"] > maximum_library_bytes
                or any(
                    not isinstance(stub[field], str)
                    or re.fullmatch(r"[0-9a-f]{64}", stub[field]) is None
                    for field in (
                        "exportedSymbolsSha256",
                        "readelfSha256",
                        "sha256",
                    )
                )
                for stub in evidence["stubs"]
            )
        ):
            _integrity(
                f"{abi_record['name']} API 26 platform receipt evidence differs"
            )

    overlay_records = [
        {
            "destination": str(overlay["destination"]),
            "replacement": str(overlay["replacement"]),
            "replacementSha256": str(overlay["replacementSha256"]),
            "replacementSize": int(overlay["replacementSize"]),
        }
        for overlay in profile.overlays
    ]
    wrapper_contract = _wrapper_contract_evidence(
        profile,
        _pinned_wrapper_raws(inputs),
        policy,
    )
    if tuple(sorted(wrapper_contract)) != WRAPPER_CONTRACT_FIELDS:
        _integrity("native wrapper build contract receipt fields differ")
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
            "parentEvents": parent_event_records,
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
            "namespaceProbeTranscriptSha256": binding[
                "namespaceProbeTranscriptSha256"
            ],
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
        "pendingReleaseBlockers": copy.deepcopy(
            receipt_policy["pendingReleaseBlockers"]
        ),
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
        "wrapperContract": wrapper_contract,
    }
    if tuple(sorted(result)) != BUILD_RECEIPT_FIELDS:
        _integrity("native wrapper build receipt fields are not exact")
    if (
        len(artifact_records)
        != len(profile.abis) * len(profile.build["expectedLibraries"])
        or result["ready"] is not False
        or result["releaseInput"] is not False
        or result["pendingReleaseBlockers"]
        != receipt_policy["pendingReleaseBlockers"]
    ):
        _integrity("native wrapper build receipt status or count differs")
    return result


def _publish_build_receipt(
    inputs: stack_executor.PinnedBuildInputs,
    receipt: dict[str, object],
) -> tuple[bytes, str]:
    policy = inputs.policy.data["receipt"]
    assert isinstance(policy, dict)
    raw = stack_executor._canonical_json(receipt)  # noqa: SLF001
    if len(raw) > int(policy["maximumBytes"]):
        _integrity("native wrapper build receipt exceeds its byte budget")
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
            "native wrapper build receipt",
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
        native_build_tool._clear_fd_xattrs(  # noqa: SLF001
            descriptor,
            "native wrapper build receipt",
        )
        os.fsync(descriptor)
        current = os.fstat(descriptor)
        staged_path_info = os.stat(
            temporary,
            dir_fd=workspace.descriptor,
            follow_symlinks=False,
        )
        readback = stack_executor._pread_exact_large(  # noqa: SLF001
            descriptor,
            current.st_size,
            int(policy["maximumBytes"]),
            "native wrapper build receipt",
        )
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != temporary_identity
            or (staged_path_info.st_dev, staged_path_info.st_ino)
            != temporary_identity
            or current.st_nlink != policy["linkCount"]
            or stat.S_IMODE(current.st_mode) != policy["mode"]
            or (current.st_uid, current.st_gid)
            != (policy["ownerUid"], policy["ownerGid"])
            or current.st_mtime_ns != normalized
            or current.st_size != len(raw)
            or readback != raw
            or os.listxattr(descriptor)
        ):
            _integrity("staged native wrapper build receipt changed")
        stack_executor._link_descriptor_no_replace_at(  # noqa: SLF001
            descriptor,
            workspace.descriptor,
            name,
            "native wrapper build receipt",
        )
        published = True
        linked_temporary = os.stat(
            temporary,
            dir_fd=workspace.descriptor,
            follow_symlinks=False,
        )
        if (linked_temporary.st_dev, linked_temporary.st_ino) != temporary_identity:
            _integrity("wrapper receipt temporary name changed during publication")
        os.unlink(temporary, dir_fd=workspace.descriptor)
        published_info = os.stat(
            name,
            dir_fd=workspace.descriptor,
            follow_symlinks=False,
        )
        published_fd_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(published_info.st_mode)
            or (published_info.st_dev, published_info.st_ino) != temporary_identity
            or stack_executor._stat_signature(published_fd_info)  # noqa: SLF001
            != stack_executor._stat_signature(published_info)  # noqa: SLF001
            or published_info.st_nlink != policy["linkCount"]
            or stat.S_IMODE(published_info.st_mode) != policy["mode"]
            or (published_info.st_uid, published_info.st_gid)
            != (policy["ownerUid"], policy["ownerGid"])
            or published_info.st_mtime_ns != normalized
            or published_info.st_size != len(raw)
        ):
            _integrity("native wrapper build receipt changed during publication")
        os.fsync(workspace.descriptor)
        _assert_workspace_root_inventory(inputs, receipt_present=True)
        receipt_pin = native_executor_tool.PinnedFile(
            path=inputs.build_workspace / name,
            descriptor=descriptor,
            identity=temporary_identity,
            stat_signature=stack_executor._stat_signature(  # noqa: SLF001
                published_fd_info
            ),
            raw=raw,
            label="native wrapper build receipt",
        )
        stack_executor._assert_build_file_pin("build-receipt", receipt_pin)  # noqa: SLF001
        inputs.files["build-receipt"] = receipt_pin
        descriptor = -1
        stack_executor._assert_mutable_pinned_directory(workspace)  # noqa: SLF001
        completed = True
        return raw, hashlib.sha256(raw).hexdigest()
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot publish native wrapper build receipt: {error}")
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
                    "native wrapper build receipt rollback could not be confirmed: "
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
    native_build_tool._require_linux_root("native wrapper build execution")  # noqa: SLF001
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
        _integrity("native wrapper build profile changed during admission")
    preparation = native_build_tool._snapshot_preparation_inputs(  # noqa: SLF001
        profile,
        source_workspace,
        composition_receipt_path,
    )
    directory_baseline = native_executor_tool._snapshot_probe_directories(  # noqa: SLF001
        source_workspace,
        build_workspace,
        include_wrapper=True,
    )
    _preparation_receipt, preparation_raw = native_build_tool._verify_prepared_workspace(  # noqa: SLF001
        build_workspace,
        preparation,
    )
    binding = policy.data["binding"]
    assert isinstance(binding, dict)
    if hashlib.sha256(preparation_raw).hexdigest() != binding["preparationReceiptSha256"]:
        _integrity("native wrapper preparation receipt differs from policy")
    if (
        hashlib.sha256(preparation.composition_receipt_raw).hexdigest()
        != binding["toolchainCompositionReceiptSha256"]
    ):
        _integrity("toolchain composition receipt differs from wrapper build policy")
    stack_executor._verify_io_device_scope(  # noqa: SLF001
        policy,
        apt_root,
        sdk_root,
        source_workspace,
        build_workspace,
    )
    audit_tool_path, audit_tool_raw = stack_executor._verify_audit_tool(  # noqa: SLF001
        policy,
        sdk_root,
    )
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
    artifacts: list[stack_executor.PinnedArtifact] = []
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
        _attempt_raw, attempt_sha256 = stack_executor._publish_attempt_marker(  # noqa: SLF001
            inputs
        )
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
        artifacts = stack_executor._pin_expected_artifacts(inputs)  # noqa: SLF001
        stack_executor._stage_artifacts(inputs, artifacts)  # noqa: SLF001
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
        stack_executor._reverify_staged_artifacts(inputs, artifacts)  # noqa: SLF001
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
                    "wrapper artifact pin cleanup also failed",
                )
        try:
            inputs.close()
        except BaseException as error:
            if receipt_sha256 is None:
                failure = native_executor_tool._combine_failures(  # noqa: SLF001
                    failure,
                    error,
                    "native wrapper build input cleanup also failed",
                )
    if failure is not None:
        raise failure
    assert receipt is not None and receipt_sha256 is not None
    try:
        print(
            "native wrapper inspection build completed with a non-release receipt: "
            f"artifacts={len(receipt['artifacts'])}; receipt={receipt_sha256}; "
            "ready=false; releaseInput=false"
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
) -> stack_executor.LoadedBuildExecutorPolicy:
    native_build_tool._require_linux_root(  # noqa: SLF001
        "native wrapper build input verification"
    )
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
        _integrity("native wrapper build profile changed during input verification")
    preparation = native_build_tool._snapshot_preparation_inputs(  # noqa: SLF001
        profile,
        source_workspace,
        composition_receipt_path,
    )
    directory_baseline = native_executor_tool._snapshot_probe_directories(  # noqa: SLF001
        source_workspace,
        build_workspace,
        include_wrapper=True,
    )
    _receipt, preparation_raw = native_build_tool._verify_prepared_workspace(  # noqa: SLF001
        build_workspace,
        preparation,
    )
    binding = policy.data["binding"]
    assert isinstance(binding, dict)
    if hashlib.sha256(preparation_raw).hexdigest() != binding["preparationReceiptSha256"]:
        _integrity("native wrapper preparation receipt differs from policy")
    if (
        hashlib.sha256(preparation.composition_receipt_raw).hexdigest()
        != binding["toolchainCompositionReceiptSha256"]
    ):
        _integrity("toolchain composition receipt differs from wrapper build policy")
    stack_executor._verify_io_device_scope(  # noqa: SLF001
        policy,
        apt_root,
        sdk_root,
        source_workspace,
        build_workspace,
    )
    audit_tool_path, audit_tool_raw = stack_executor._verify_audit_tool(  # noqa: SLF001
        policy,
        sdk_root,
    )
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
    failure: BaseException | None = None
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
    except BaseException as error:
        failure = error
    finally:
        try:
            inputs.close()
        except BaseException as error:
            failure = native_executor_tool._combine_failures(  # noqa: SLF001
                failure,
                error,
                "native wrapper input verification cleanup also failed",
            )
    if failure is not None:
        raise failure
    print(
        "native wrapper build inputs verified without execution: "
        f"policy={policy.sha256}; preparation={binding['preparationReceiptSha256']}; "
        f"composition={binding['toolchainCompositionReceiptSha256']}; "
        f"elfTool={policy.data['audit']['elfToolTargetSha256']}"  # type: ignore[index]
    )
    return policy


def validate(
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
) -> stack_executor.LoadedBuildExecutorPolicy:
    policy = load_build_execution_policy(
        policy_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    gate = policy.data["gate"]
    assert isinstance(gate, dict)
    print(
        "native wrapper build executor policy valid: "
        f"phase={gate['phase']}; buildCommands={json.dumps(gate['buildCommands'])}; "
        f"artifactAudit={json.dumps(gate['artifactAudit'])}; "
        f"ready={json.dumps(gate['ready'])}; "
        f"releaseInput={json.dumps(gate['releaseInput'])}; sha256={policy.sha256}"
    )
    return policy


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-cache",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_CACHE,
    )
    parser.add_argument(
        "--toolchain-cache",
        type=Path,
        default=native_build_tool.DEFAULT_TOOLCHAIN_CACHE,
    )
    parser.add_argument(
        "--source-workspace",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_WORKSPACE,
    )
    parser.add_argument(
        "--apt-root",
        type=Path,
        default=native_build_tool.DEFAULT_APT_ROOT,
    )
    parser.add_argument(
        "--sdk-root",
        type=Path,
        default=native_build_tool.DEFAULT_SDK_ROOT,
    )
    parser.add_argument(
        "--composition-receipt",
        type=Path,
        default=native_build_tool.DEFAULT_COMPOSITION_RECEIPT,
    )
    parser.add_argument(
        "--build-workspace",
        type=Path,
        required=True,
        help="explicit prepared wrapper workspace; it has no default",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument(
        "--toolchain-manifest",
        type=Path,
        default=DEFAULT_TOOLCHAIN_MANIFEST,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "validate",
        help="validate the wrapper build policy without entering a namespace",
    )
    verify_command = commands.add_parser(
        "verify-inputs",
        help="pin and reverify one prepared wrapper workspace without executing it",
    )
    _add_input_arguments(verify_command)
    execute_command = commands.add_parser(
        "execute",
        help=(
            "consume one prepared wrapper workspace, build, stage and audit its "
            "20 non-release artifacts"
        ),
    )
    _add_input_arguments(execute_command)
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
        elif arguments.command in {"verify-inputs", "execute"}:
            function = verify_inputs if arguments.command == "verify-inputs" else execute
            function(
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
        else:  # pragma: no cover - argparse owns this branch.
            _schema(f"unknown command: {arguments.command}")
        return source_tool.EXIT_OK
    except source_tool.SourceToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return source_tool.EXIT_INTERNAL
    except Exception as error:  # pragma: no cover - stable CLI boundary.
        print(
            f"error: internal native wrapper build executor failure: {error}",
            file=sys.stderr,
        )
        return source_tool.EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
