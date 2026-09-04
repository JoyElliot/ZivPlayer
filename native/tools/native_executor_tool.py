#!/usr/bin/env python3
"""Validate and eventually run the isolated native-build executor."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import native_build_tool
import source_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = NATIVE_DIR.parent
DEFAULT_EXECUTOR_POLICY = NATIVE_DIR / "native-executor-policy.toml"
DEFAULT_BUILD_PROFILE = NATIVE_DIR / "native-build-profile.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_TOOLCHAIN_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"

MAX_POLICY_BYTES = 1024 * 1024
MAX_HELPER_BYTES = 1024 * 1024
POLICY_KIND = "ziv-native-executor-policy-v1"
EXPECTED_PROFILE_SHA256 = (
    "538fe37887840c4e23acdb189d3464878dd006dfc42f6e4c4565490f88252413"
)
EXPECTED_POLICY_SHA256 = (
    "5811258e9ec2356e9ab1c4196f71831381983f628774dd5a9200502b40618c45"
)
HELPER_KEYS = (
    ("namespacePath", "namespaceSha256"),
    ("probePath", "probeSha256"),
    ("seccompPath", "seccompSha256"),
)

EXPECTED_POLICY: dict[str, object] = {
    "schemaVersion": 1,
    "kind": POLICY_KIND,
    "binding": {
        "profileName": native_build_tool.PROFILE_NAME,
        "profileSha256": EXPECTED_PROFILE_SHA256,
        "preparationReceiptKind": native_build_tool.PREPARATION_RECEIPT_KIND,
        "hostPlatform": "linux/amd64",
        "toolchainMount": "/opt/zivplayer/toolchain",
        "sourceMount": "/build/source",
        "outputMount": "/build/output",
        "homeMount": "/build/home",
        "temporaryMount": "/build/tmp",
    },
    "gate": {
        "phase": "namespace-probe",
        "buildCommands": False,
        "artifactStaging": False,
        "buildReceipt": False,
        "ready": False,
        "releaseInput": False,
    },
    "process": {
        "environmentMode": "empty",
        "probeTimeoutSeconds": 180,
        "stdoutLimitBytes": 1048576,
        "stderrLimitBytes": 1048576,
        "stderrAllowed": False,
        "parentDeathSignal": "SIGKILL",
        "newSession": True,
        "rlimitNoFile": 4096,
        "rlimitFileSizeBytes": 2147483648,
        "rlimitCoreBytes": 0,
    },
    "cgroup": {
        "version": 2,
        "controllers": ["cpu", "io", "memory", "pids"],
        "pidsMax": 512,
        "memoryMaxBytes": 2147483648,
        "memorySwapMaxBytes": 0,
        "cpuQuotaMicros": 200000,
        "cpuPeriodMicros": 100000,
        "ioReadBytesPerSecond": 8388608,
        "ioWriteBytesPerSecond": 8388608,
        "ioReadOperationsPerSecond": 2048,
        "ioWriteOperationsPerSecond": 2048,
    },
    "filesystem": {
        "minimumFreeBytes": 8589934592,
        "reserveFreeBytes": 4294967296,
        "minimumFreeInodes": 250000,
        "reserveFreeInodes": 100000,
        "maximumEntryDelta": 104096,
    },
    "namespace": {
        "profile": "ziv-native-build-namespace-probe-v1",
        "unshare": ["mount", "net", "pid", "uts", "ipc"],
        "propagation": "private",
        "network": "loopback-device-only-no-external-routes",
        "root": "overlay:ro,nosuid,nodev,exec",
        "aptLower": "ext4:immutable",
        "buildRoot": "tmpfs:ro,nosuid,nodev,noexec:size=16777216:mode=0755",
        "toolchain": "ext4:ro,nosuid,nodev,exec",
        "source": "ext4:rw,nosuid,nodev,exec",
        "output": "ext4:rw,nosuid,nodev,noexec",
        "home": "ext4:rw,nosuid,nodev,noexec",
        "temporary": "ext4:rw,nosuid,nodev,exec",
        "transientTmpfsBytes": 67108864,
        "expectedMountCount": 15,
        "capabilities": "none",
        "noNewPrivileges": True,
        "seccomp": True,
        "oldRootDetached": True,
    },
    "helpers": {
        "namespacePath": "native/toolchain/native-build-namespace.bash",
        "namespaceSha256": (
            "8a5177f0664b2909dd75e922843c3f430b36f495c7ea9ef38e85d54173485959"
        ),
        "probePath": "native/toolchain/native-build-probe.bash",
        "probeSha256": (
            "3efa8bffa6dbe38c7264a1ae3b1aa8ebf0f00a1e07bbebf5df0172e58a5b4485"
        ),
        "seccompPath": "native/toolchain/install-seccomp.pl",
        "seccompSha256": (
            "ab2e6d21a2768a585b2bc53a2495c78f46ab9a4dbac32d09bf58e311091c597d"
        ),
    },
}


@dataclass(frozen=True)
class LoadedExecutorPolicy:
    data: dict[str, object]
    raw: bytes
    sha256: str
    profile: native_build_tool.LoadedProfile
    helper_raws: dict[str, bytes]


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _assert_exact(actual: object, expected: object, location: str) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            _schema(f"{location} must be a table")
        actual_keys = set(actual)
        expected_keys = set(expected)
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            extra = sorted(actual_keys - expected_keys)
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
        if not isinstance(actual, list) or len(actual) != len(expected):
            _schema(f"{location} must be the fixed ordered array")
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
            _assert_exact(actual_value, expected_value, f"{location}[{index}]")
        return
    if type(actual) is not type(expected) or actual != expected:
        _schema(f"{location} must be {expected!r}")


def validate_policy_data(data: object) -> dict[str, object]:
    _assert_exact(data, EXPECTED_POLICY, "executor policy")
    assert isinstance(data, dict)
    return copy.deepcopy(data)


def _stable_bytes(path: Path, *, maximum: int, label: str, missing_exit: int) -> bytes:
    return native_build_tool._stable_bytes(  # noqa: SLF001 - shared locked reader
        path,
        maximum=maximum,
        label=label,
        missing_exit=missing_exit,
    )


def _helper_snapshot(
    data: dict[str, object],
    *,
    repository_root: Path,
) -> dict[str, bytes]:
    helpers = data["helpers"]
    assert isinstance(helpers, dict)
    result: dict[str, bytes] = {}
    for path_key, digest_key in HELPER_KEYS:
        relative = helpers[path_key]
        expected_digest = helpers[digest_key]
        assert isinstance(relative, str) and isinstance(expected_digest, str)
        path = native_build_tool._resolve_repository_file(  # noqa: SLF001
            repository_root,
            relative,
            f"executor helper {relative}",
        )
        raw = _stable_bytes(
            path,
            maximum=MAX_HELPER_BYTES,
            label=f"executor helper {relative}",
            missing_exit=source_tool.EXIT_MISSING,
        )
        if hashlib.sha256(raw).hexdigest() != expected_digest:
            _integrity(f"executor helper digest differs: {relative}")
        result[relative] = raw
    return result


def load_execution_policy(
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> LoadedExecutorPolicy:
    raw = _stable_bytes(
        policy_path,
        maximum=MAX_POLICY_BYTES,
        label="native executor policy",
        missing_exit=source_tool.EXIT_SCHEMA,
    )
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        _schema(f"cannot parse native executor policy {policy_path}: {error}")
    data = validate_policy_data(parsed)
    policy_sha256 = hashlib.sha256(raw).hexdigest()
    if policy_sha256 != EXPECTED_POLICY_SHA256:
        _integrity("native executor policy digest differs from the locked policy")
    profile = native_build_tool.load_profile(
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
        repository_root=repository_root,
    )
    binding = data["binding"]
    assert isinstance(binding, dict)
    if (
        binding["profileName"] != profile.project["profile"]
        or binding["profileSha256"] != profile.sha256
        or binding["preparationReceiptKind"]
        != native_build_tool.PREPARATION_RECEIPT_KIND
        or binding["hostPlatform"] != profile.toolchain["hostPlatform"]
        or binding["toolchainMount"] != profile.toolchain["mount"]
        or binding["sourceMount"] != profile.policy["sourceCopyMount"]
        or binding["outputMount"] != profile.policy["outputMount"]
        or binding["homeMount"] != profile.policy["buildHomeMount"]
        or binding["temporaryMount"] != profile.policy["temporaryMount"]
    ):
        _integrity("native executor policy is not bound to the selected build profile")
    helper_raws = _helper_snapshot(data, repository_root=repository_root)
    return LoadedExecutorPolicy(
        data=data,
        raw=raw,
        sha256=policy_sha256,
        profile=profile,
        helper_raws=helper_raws,
    )


def validate(
    policy_path: Path,
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
) -> LoadedExecutorPolicy:
    policy = load_execution_policy(
        policy_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    gate = policy.data["gate"]
    assert isinstance(gate, dict)
    print(
        "native executor policy valid: "
        f"phase={gate['phase']}; buildCommands={json.dumps(gate['buildCommands'])}; "
        f"sha256={policy.sha256}"
    )
    return policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_EXECUTOR_POLICY)
    parser.add_argument("--profile", type=Path, default=DEFAULT_BUILD_PROFILE)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--toolchain-manifest", type=Path, default=DEFAULT_TOOLCHAIN_MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate the locked namespace-probe policy")
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
        else:  # pragma: no cover - argparse owns this invariant.
            _schema(f"unknown command: {arguments.command}")
        return source_tool.EXIT_OK
    except source_tool.SourceToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return source_tool.EXIT_INTERNAL
    except Exception as error:  # pragma: no cover - stable CLI boundary.
        print(f"error: internal native executor tool failure: {error}", file=sys.stderr)
        return source_tool.EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
