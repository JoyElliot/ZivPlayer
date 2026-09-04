#!/usr/bin/env python3
"""Validate and eventually run the isolated native-build executor."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn

import composition_tool
import environment_tool
import materialize_sources
import native_build_tool
import sdk_tool
import source_tool
import toolchain_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = NATIVE_DIR.parent
DEFAULT_EXECUTOR_POLICY = NATIVE_DIR / "native-executor-policy.toml"
DEFAULT_BUILD_PROFILE = NATIVE_DIR / "native-build-profile.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_TOOLCHAIN_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"

MAX_POLICY_BYTES = 1024 * 1024
MAX_HELPER_BYTES = 1024 * 1024
MAX_PINNED_FILE_BYTES = 4 * 1024 * 1024
POLICY_KIND = "ziv-native-executor-policy-v1"
NAMESPACE_PROFILE = "ziv-native-build-namespace-probe-v1"
CHILD_PROFILE = "ziv-native-executor-child-v1"
PROBE_TIMEOUT_SECONDS = 180
PROBE_STREAM_LIMIT_BYTES = 1024 * 1024
PROBE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "TZ": "UTC",
    "SOURCE_DATE_EPOCH": "946684800",
}
CONTROL_SIGNALS = frozenset(
    {
        signal.SIGINT,
        signal.SIGTERM,
        getattr(signal, "SIGHUP", signal.SIGTERM),
    }
)
EXPECTED_PROFILE_SHA256 = (
    "538fe37887840c4e23acdb189d3464878dd006dfc42f6e4c4565490f88252413"
)
EXPECTED_POLICY_SHA256 = (
    "fe3db9f8397e2cacd96077228b5dabc8fb9fa788492d523beea62da0530b17b9"
)
HELPER_KEYS = (
    ("launcherPath", "launcherSha256"),
    ("namespacePath", "namespaceSha256"),
    ("probePath", "probeSha256"),
    ("seccompPath", "seccompSha256"),
)
PROBE_RECORDS = (
    ("namespace", "private:mnt,net,pid,uts,ipc"),
    (
        "mounts",
        "overlay-root-ro;apt-lower-ro;sdk-ro;source-rw;output-rw;home-rw;tmp-rw",
    ),
    ("environment", "empty-inheritance;fixed-build-variables"),
    ("network", "loopback-device-only;no-external-routes;af-inet-denied"),
    ("sandbox", "no-caps;no-new-privs;seccomp;no-host-fds"),
    ("workspace", "mounted-unmodified;build-not-executed"),
)
EXPECTED_PROBE_TRANSCRIPT = "".join(
    f"{key}\t{value}\n" for key, value in PROBE_RECORDS
).encode("utf-8")

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
        "probeTimeoutSeconds": PROBE_TIMEOUT_SECONDS,
        "stdoutLimitBytes": PROBE_STREAM_LIMIT_BYTES,
        "stderrLimitBytes": PROBE_STREAM_LIMIT_BYTES,
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
        "launcherPath": "native/toolchain/native-executor-child.py",
        "launcherSha256": (
            "02bdfc1357fbf533364f12829caa7f763f422c0053b8841499ae63166c272e3a"
        ),
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


@dataclass
class PinnedDirectory:
    path: Path
    descriptor: int
    identity: tuple[int, int]
    stat_signature: tuple[int, ...]
    label: str


@dataclass
class PinnedFile:
    path: Path
    descriptor: int
    identity: tuple[int, int]
    stat_signature: tuple[int, ...]
    raw: bytes
    label: str


@dataclass
class PinnedProbeInputs:
    policy: LoadedExecutorPolicy
    preparation: native_build_tool.PreparationInputs
    preparation_receipt_raw: bytes
    build_workspace: Path
    directories: dict[str, PinnedDirectory]
    files: dict[str, PinnedFile]
    composition_inputs: composition_tool.BoundInputs

    def close(self) -> None:
        failure: BaseException | None = None
        for pinned in reversed(tuple(self.files.values())):
            if pinned.descriptor >= 0:
                try:
                    os.close(pinned.descriptor)
                except OSError as error:
                    failure = failure or error
                finally:
                    pinned.descriptor = -1
        for pinned in reversed(tuple(self.directories.values())):
            if pinned.descriptor >= 0:
                try:
                    os.close(pinned.descriptor)
                except OSError as error:
                    failure = failure or error
                finally:
                    pinned.descriptor = -1
        try:
            self.composition_inputs.close()
        except BaseException as error:
            failure = failure or error
        if failure is not None:
            if isinstance(failure, source_tool.SourceToolError):
                raise failure
            _integrity(f"cannot close a pinned native executor input: {failure}")


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


def _stat_signature(info: os.stat_result) -> tuple[int, ...]:
    return tuple(
        int(getattr(info, field))
        for field in environment_tool._STABLE_STAT_FIELDS  # noqa: SLF001
    )


def _directory_stat(
    path: Path,
    *,
    parent_descriptor: int | None,
    child_name: str | None,
) -> os.stat_result:
    if parent_descriptor is None:
        return path.lstat()
    assert child_name is not None
    return os.stat(
        child_name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )


def _snapshot_probe_directories(
    source_workspace: Path,
    build_workspace: Path,
) -> dict[str, tuple[int, ...]]:
    paths = {
        "canonical-source": source_workspace,
        "workspace": build_workspace,
        "source": build_workspace / "source",
        "output": build_workspace / "output",
        "home": build_workspace / "home",
        "tmp": build_workspace / "tmp",
    }
    try:
        return {key: _stat_signature(path.lstat()) for key, path in paths.items()}
    except OSError as error:
        _integrity(f"cannot snapshot native executor directories: {error}")


def _assert_directory_baseline(
    baseline: dict[str, tuple[int, ...]],
    directories: dict[str, PinnedDirectory],
) -> None:
    if set(baseline) != set(directories) or any(
        directories[key].stat_signature != signature
        for key, signature in baseline.items()
    ):
        _integrity(
            "native executor directory identity changed between verification and pinning"
        )


def _open_pinned_directory(
    path: Path,
    *,
    label: str,
    expected_mode: int,
    expected_device: int | None = None,
    parent_descriptor: int | None = None,
    child_name: str | None = None,
) -> PinnedDirectory:
    descriptor = -1
    try:
        before = _directory_stat(
            path,
            parent_descriptor=parent_descriptor,
            child_name=child_name,
        )
        target: str | Path = child_name if parent_descriptor is not None else path
        assert target is not None
        descriptor = os.open(
            target,
            native_build_tool._directory_flags(),  # noqa: SLF001
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        current = _directory_stat(
            path,
            parent_descriptor=parent_descriptor,
            child_name=child_name,
        )
        path_current = path.lstat()
        signatures = {
            _stat_signature(before),
            _stat_signature(opened),
            _stat_signature(current),
            _stat_signature(path_current),
        }
        if (
            len(signatures) != 1
            or not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(path_current.st_mode)
            or stat.S_IMODE(opened.st_mode) != expected_mode
            or (opened.st_uid, opened.st_gid) != (0, 0)
            or (expected_device is not None and opened.st_dev != expected_device)
        ):
            _integrity(f"{label} changed while it was pinned")
        return PinnedDirectory(
            path=path,
            descriptor=descriptor,
            identity=(opened.st_dev, opened.st_ino),
            stat_signature=_stat_signature(opened),
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


def _assert_pinned_directory(pinned: PinnedDirectory) -> None:
    try:
        opened = os.fstat(pinned.descriptor)
        current = pinned.path.lstat()
    except OSError as error:
        _integrity(f"cannot recheck {pinned.label}: {error}")
    if (
        _stat_signature(opened) != pinned.stat_signature
        or _stat_signature(current) != pinned.stat_signature
        or (opened.st_dev, opened.st_ino) != pinned.identity
        or not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
    ):
        _integrity(f"{pinned.label} identity or metadata changed")


def _pread_exact(descriptor: int, size: int, label: str) -> bytes:
    if size < 0 or size > MAX_PINNED_FILE_BYTES:
        _integrity(f"{label} exceeds the pinned-file limit")
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(64 * 1024, size - offset), offset)
        if not chunk:
            _integrity(f"{label} ended before its pinned size")
        chunks.append(chunk)
        offset += len(chunk)
    if os.pread(descriptor, 1, size):
        _integrity(f"{label} grew beyond its pinned size")
    return b"".join(chunks)


def _open_pinned_file(
    path: Path,
    expected_raw: bytes,
    *,
    label: str,
    canonical_receipt: bool = False,
    parent_descriptor: int | None = None,
    child_name: str | None = None,
) -> PinnedFile:
    if len(expected_raw) > MAX_PINNED_FILE_BYTES:
        _integrity(f"{label} exceeds the pinned-file limit")
    descriptor = -1
    try:
        before = _directory_stat(
            path,
            parent_descriptor=parent_descriptor,
            child_name=child_name,
        )
        target: str | Path = child_name if parent_descriptor is not None else path
        assert target is not None
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(target, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        current = _directory_stat(
            path,
            parent_descriptor=parent_descriptor,
            child_name=child_name,
        )
        path_current = path.lstat()
        signatures = {
            _stat_signature(before),
            _stat_signature(opened),
            _stat_signature(current),
            _stat_signature(path_current),
        }
        if (
            len(signatures) != 1
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_ISLNK(path_current.st_mode)
            or opened.st_size != len(expected_raw)
        ):
            _integrity(f"{label} changed while it was pinned")
        if canonical_receipt and (
            stat.S_IMODE(opened.st_mode) != 0o644
            or (opened.st_uid, opened.st_gid) != (0, 0)
            or opened.st_mtime_ns != native_build_tool.NORMALIZED_MTIME_NS
            or os.listxattr(descriptor)
        ):
            _integrity(f"{label} metadata is not canonical")
        if _pread_exact(descriptor, opened.st_size, label) != expected_raw:
            _integrity(f"{label} differs from its verified bytes")
        final_fd = os.fstat(descriptor)
        final_path = path.lstat()
        if (
            _stat_signature(final_fd) != _stat_signature(opened)
            or _stat_signature(final_path) != _stat_signature(opened)
        ):
            _integrity(f"{label} changed while its bytes were pinned")
        return PinnedFile(
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
    except (AttributeError, OSError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        _integrity(f"cannot pin {label} {path}: {error}")


def _assert_pinned_file(pinned: PinnedFile) -> None:
    try:
        opened = os.fstat(pinned.descriptor)
        current = pinned.path.lstat()
        raw = _pread_exact(pinned.descriptor, opened.st_size, pinned.label)
        final_fd = os.fstat(pinned.descriptor)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot recheck {pinned.label}: {error}")
    if (
        _stat_signature(opened) != pinned.stat_signature
        or _stat_signature(current) != pinned.stat_signature
        or _stat_signature(final_fd) != pinned.stat_signature
        or (opened.st_dev, opened.st_ino) != pinned.identity
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or opened.st_nlink != 1
        or current.st_nlink != 1
        or raw != pinned.raw
    ):
        _integrity(f"{pinned.label} identity, metadata, or bytes changed")


def _assert_runtime_policy(policy: LoadedExecutorPolicy) -> None:
    process = policy.data["process"]
    cgroup = policy.data["cgroup"]
    filesystem = policy.data["filesystem"]
    namespace = policy.data["namespace"]
    assert isinstance(process, dict)
    assert isinstance(cgroup, dict)
    assert isinstance(filesystem, dict)
    assert isinstance(namespace, dict)
    expected_cgroup = {
        "version": 2,
        "controllers": ["cpu", "io", "memory", "pids"],
        "pidsMax": environment_tool.CGROUP_PIDS_MAX,
        "memoryMaxBytes": environment_tool.CGROUP_MEMORY_MAX,
        "memorySwapMaxBytes": environment_tool.CGROUP_MEMORY_SWAP_MAX,
        "cpuQuotaMicros": environment_tool.CGROUP_CPU_QUOTA_US,
        "cpuPeriodMicros": environment_tool.CGROUP_CPU_PERIOD_US,
        "ioReadBytesPerSecond": environment_tool.CGROUP_IO_BYTES_PER_SECOND,
        "ioWriteBytesPerSecond": environment_tool.CGROUP_IO_BYTES_PER_SECOND,
        "ioReadOperationsPerSecond": environment_tool.CGROUP_IO_OPERATIONS_PER_SECOND,
        "ioWriteOperationsPerSecond": environment_tool.CGROUP_IO_OPERATIONS_PER_SECOND,
    }
    expected_filesystem = {
        "minimumFreeBytes": environment_tool.MIN_INSTALL_FREE_BYTES,
        "reserveFreeBytes": environment_tool.INSTALL_FREE_RESERVE_BYTES,
        "minimumFreeInodes": environment_tool.MIN_INSTALL_FREE_INODES,
        "reserveFreeInodes": environment_tool.INSTALL_FREE_RESERVE_INODES,
        "maximumEntryDelta": environment_tool.MAX_INSTALL_INODE_DELTA,
    }
    expected_process = {
        "environmentMode": "empty",
        "probeTimeoutSeconds": PROBE_TIMEOUT_SECONDS,
        "stdoutLimitBytes": PROBE_STREAM_LIMIT_BYTES,
        "stderrLimitBytes": PROBE_STREAM_LIMIT_BYTES,
        "stderrAllowed": False,
        "parentDeathSignal": "SIGKILL",
        "newSession": True,
        "rlimitNoFile": environment_tool.INSTALLER_NOFILE_LIMIT,
        "rlimitFileSizeBytes": environment_tool.INSTALLER_FILE_SIZE_LIMIT,
        "rlimitCoreBytes": 0,
    }
    expected_namespace = EXPECTED_POLICY["namespace"]
    assert isinstance(expected_namespace, dict)
    if (
        process != expected_process
        or cgroup != expected_cgroup
        or filesystem != expected_filesystem
        or namespace != expected_namespace
    ):
        _integrity("native executor runtime constants differ from the locked policy")


def _assert_host_resource_limits(policy: LoadedExecutorPolicy) -> None:
    resource = environment_tool.resource
    if resource is None:
        _schema("native executor resource limits require Linux resource support")
    process = policy.data["process"]
    assert isinstance(process, dict)
    for limit, required, label in (
        (resource.RLIMIT_NOFILE, int(process["rlimitNoFile"]), "open-file"),
        (resource.RLIMIT_FSIZE, int(process["rlimitFileSizeBytes"]), "file-size"),
    ):
        _soft, hard = resource.getrlimit(limit)
        if hard != resource.RLIM_INFINITY and hard < required:
            _integrity(f"host hard {label} limit is below the locked executor limit")


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


def _close_partial_pins(
    files: dict[str, PinnedFile],
    directories: dict[str, PinnedDirectory],
    composition_inputs: composition_tool.BoundInputs | None,
) -> None:
    for pinned in reversed(tuple(files.values())):
        if pinned.descriptor >= 0:
            try:
                os.close(pinned.descriptor)
            except OSError:
                pass
            pinned.descriptor = -1
    for pinned in reversed(tuple(directories.values())):
        if pinned.descriptor >= 0:
            try:
                os.close(pinned.descriptor)
            except OSError:
                pass
            pinned.descriptor = -1
    if composition_inputs is not None:
        try:
            composition_inputs.close()
        except BaseException:
            pass


def _pin_probe_inputs(
    policy: LoadedExecutorPolicy,
    preparation: native_build_tool.PreparationInputs,
    preparation_receipt_raw: bytes,
    directory_baseline: dict[str, tuple[int, ...]],
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
) -> PinnedProbeInputs:
    files: dict[str, PinnedFile] = {}
    directories: dict[str, PinnedDirectory] = {}
    composition_inputs: composition_tool.BoundInputs | None = None
    try:
        composition_inputs = composition_tool._load_bound_inputs(  # noqa: SLF001
            toolchain_manifest_path,
            source_manifest_path,
            toolchain_cache,
            apt_root,
            sdk_root,
        )
        canonical_source = _open_pinned_directory(
            source_workspace,
            label="canonical source workspace",
            expected_mode=0o755,
        )
        directories["canonical-source"] = canonical_source
        workspace = _open_pinned_directory(
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
            directories[name] = _open_pinned_directory(
                build_workspace / name,
                label=f"prepared {name} directory",
                expected_mode=mode,
                expected_device=workspace.identity[0],
                parent_descriptor=workspace.descriptor,
                child_name=name,
            )
        _assert_directory_baseline(directory_baseline, directories)

        source_manifest_raw = _stable_bytes(
            source_manifest_path,
            maximum=toolchain_tool.MAX_MANIFEST_BYTES,
            label="source manifest",
            missing_exit=source_tool.EXIT_SCHEMA,
        )
        toolchain_manifest_raw = _stable_bytes(
            toolchain_manifest_path,
            maximum=toolchain_tool.MAX_MANIFEST_BYTES,
            label="toolchain manifest",
            missing_exit=source_tool.EXIT_SCHEMA,
        )
        if (
            hashlib.sha256(source_manifest_raw).hexdigest()
            != policy.profile.project["sourceManifestSha256"]
            or hashlib.sha256(toolchain_manifest_raw).hexdigest()
            != policy.profile.project["toolchainManifestSha256"]
        ):
            _integrity("native executor manifests differ from the locked profile")

        direct_files = (
            ("policy", policy_path, policy.raw, "native executor policy", False),
            ("profile", profile_path, policy.profile.raw, "native build profile", False),
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
            files[key] = _open_pinned_file(
                path,
                raw,
                label=label,
                canonical_receipt=canonical_receipt,
            )

        files["source-receipt"] = _open_pinned_file(
            source_workspace / materialize_sources.RECEIPT_NAME,
            preparation.source_receipt_raw,
            label="source materialization receipt",
            canonical_receipt=True,
            parent_descriptor=canonical_source.descriptor,
            child_name=materialize_sources.RECEIPT_NAME,
        )
        files["preparation-receipt"] = _open_pinned_file(
            build_workspace / native_build_tool.PREPARATION_RECEIPT_NAME,
            preparation_receipt_raw,
            label="native build preparation receipt",
            canonical_receipt=True,
            parent_descriptor=workspace.descriptor,
            child_name=native_build_tool.PREPARATION_RECEIPT_NAME,
        )
        files["apt-receipt"] = _open_pinned_file(
            composition_inputs.apt_root / environment_tool.RECEIPT_NAME,
            composition_inputs.apt_receipt_raw,
            label="APT root receipt",
            canonical_receipt=True,
            parent_descriptor=composition_inputs.apt_fd,
            child_name=environment_tool.RECEIPT_NAME,
        )
        files["sdk-receipt"] = _open_pinned_file(
            composition_inputs.sdk_root / sdk_tool.RECEIPT_NAME,
            composition_inputs.sdk_receipt_raw,
            label="SDK projection receipt",
            canonical_receipt=True,
            parent_descriptor=composition_inputs.sdk_fd,
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
            files[f"overlay:{relative}"] = _open_pinned_file(
                path,
                raw,
                label=f"overlay replacement {relative}",
            )
        for relative, raw in policy.helper_raws.items():
            path = native_build_tool._resolve_repository_file(  # noqa: SLF001
                REPOSITORY_ROOT,
                relative,
                f"executor helper {relative}",
            )
            files[f"helper:{relative}"] = _open_pinned_file(
                path,
                raw,
                label=f"executor helper {relative}",
            )
        pinned = PinnedProbeInputs(
            policy=policy,
            preparation=preparation,
            preparation_receipt_raw=preparation_receipt_raw,
            build_workspace=build_workspace,
            directories=directories,
            files=files,
            composition_inputs=composition_inputs,
        )
        _assert_pinned_probe_inputs(pinned)
        return pinned
    except BaseException:
        _close_partial_pins(files, directories, composition_inputs)
        raise


def _assert_pinned_probe_inputs(inputs: PinnedProbeInputs) -> None:
    for pinned in inputs.directories.values():
        _assert_pinned_directory(pinned)
    for pinned in inputs.files.values():
        _assert_pinned_file(pinned)
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
    device_inputs = {
        "APT root": os.fstat(bound.apt_fd).st_dev,
        "SDK projection": os.fstat(bound.sdk_fd).st_dev,
        **{
            pinned.label: os.fstat(pinned.descriptor).st_dev
            for key, pinned in inputs.directories.items()
            if key != "canonical-source"
        },
    }
    if len(set(device_inputs.values())) != 1:
        details = ", ".join(f"{label}={device}" for label, device in device_inputs.items())
        _integrity(
            "native executor requires one block device for the locked I/O envelope: "
            + details
        )
    passed_descriptors = (
        bound.apt_fd,
        bound.sdk_fd,
        *(inputs.directories[key].descriptor for key in ("workspace", "source", "output", "home", "tmp")),
        *(
            inputs.files[f"helper:{relative}"].descriptor
            for relative in inputs.policy.helper_raws
        ),
    )
    if (
        len(set(passed_descriptors)) != len(passed_descriptors)
        or any(descriptor < 3 or descriptor == 255 for descriptor in passed_descriptors)
    ):
        _integrity("native executor descriptors are duplicated or outside the allowed range")


def _reverify_probe_inputs(
    inputs: PinnedProbeInputs,
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
) -> None:
    _assert_pinned_probe_inputs(inputs)
    reloaded_policy = load_execution_policy(
        policy_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    if (
        reloaded_policy.raw != inputs.policy.raw
        or reloaded_policy.data != inputs.policy.data
        or reloaded_policy.profile.raw != inputs.policy.profile.raw
        or reloaded_policy.helper_raws != inputs.policy.helper_raws
    ):
        _integrity("native executor policy inputs changed while preparing the probe")
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
    current = native_build_tool._snapshot_preparation_inputs(  # noqa: SLF001
        profile,
        source_workspace,
        composition_receipt_path,
    )
    native_build_tool._assert_same_preparation_inputs(  # noqa: SLF001
        inputs.preparation,
        current,
    )
    _receipt, receipt_raw = native_build_tool._verify_prepared_workspace(  # noqa: SLF001
        inputs.build_workspace,
        current,
    )
    if receipt_raw != inputs.preparation_receipt_raw:
        _integrity("native build preparation receipt changed during executor probe")
    composition_tool._reverify_bound_inputs(  # noqa: SLF001
        toolchain_manifest_path,
        source_manifest_path,
        toolchain_cache,
        inputs.composition_inputs,
    )
    _assert_pinned_probe_inputs(inputs)


def _namespace_path(path: Path, label: str) -> str:
    value = str(path)
    allowed = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
    )
    if (
        not value.startswith("/")
        or any(character not in allowed for character in value)
        or "//" in value
        or value.endswith("/")
    ):
        _schema(f"{label} is not a canonical namespace path")
    return value


def _identity_text(identity: tuple[int, int]) -> str:
    return f"{identity[0]}:{identity[1]}"


def _probe_process_arguments(
    inputs: PinnedProbeInputs,
    cgroup_procs_descriptor: int,
) -> tuple[list[str], tuple[int, ...]]:
    helpers = inputs.policy.data["helpers"]
    process_policy = inputs.policy.data["process"]
    assert isinstance(helpers, dict)
    assert isinstance(process_policy, dict)

    def helper_descriptor(path_key: str) -> int:
        relative = helpers[path_key]
        assert isinstance(relative, str)
        return inputs.files[f"helper:{relative}"].descriptor

    launcher_descriptor = helper_descriptor("launcherPath")
    namespace_descriptor = helper_descriptor("namespacePath")
    probe_descriptor = helper_descriptor("probePath")
    seccomp_descriptor = helper_descriptor("seccompPath")
    directories = inputs.directories
    bound = inputs.composition_inputs
    preserved = (
        bound.apt_fd,
        bound.sdk_fd,
        directories["workspace"].descriptor,
        directories["source"].descriptor,
        directories["output"].descriptor,
        directories["home"].descriptor,
        directories["tmp"].descriptor,
        namespace_descriptor,
        probe_descriptor,
        seccomp_descriptor,
    )
    pass_fds = (*preserved, launcher_descriptor, cgroup_procs_descriptor)
    if (
        len(set(pass_fds)) != len(pass_fds)
        or any(descriptor < 3 or descriptor == 255 for descriptor in pass_fds)
    ):
        _integrity("native executor launch descriptors are duplicated or reserved")

    apt_path = _namespace_path(bound.apt_root, "APT root")
    sdk_path = _namespace_path(bound.sdk_root, "SDK projection")
    workspace_path = _namespace_path(inputs.build_workspace, "native build workspace")
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
        apt_path,
        sdk_path,
        workspace_path,
        *(str(descriptor) for descriptor in preserved),
        _identity_text(bound.apt_identity),
        _identity_text(bound.sdk_identity),
        *(
            _identity_text(directories[key].identity)
            for key in ("workspace", "source", "output", "home", "tmp")
        ),
        hashlib.sha256(
            inputs.files[f"helper:{helpers['namespacePath']}"].raw
        ).hexdigest(),
        hashlib.sha256(
            inputs.files[f"helper:{helpers['probePath']}"].raw
        ).hexdigest(),
        hashlib.sha256(
            inputs.files[f"helper:{helpers['seccompPath']}"].raw
        ).hexdigest(),
        *(composition_tool._namespace_id(name) for name in ("mnt", "net", "pid", "uts", "ipc")),  # noqa: SLF001
        NAMESPACE_PROFILE,
    ]
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
        str(process_policy["rlimitNoFile"]),
        str(process_policy["rlimitFileSizeBytes"]),
        str(process_policy["rlimitCoreBytes"]),
        ",".join(str(descriptor) for descriptor in preserved),
        "--",
        *namespace_argv,
    ]
    return argv, pass_fds


def _append_bounded(
    buffer: bytearray,
    chunk: bytes,
    *,
    maximum: int,
    label: str,
) -> None:
    if len(buffer) + len(chunk) > maximum:
        _integrity(f"native executor {label} exceeds {maximum} bytes")
    buffer.extend(chunk)


def _filesystem_violation(
    workspace_descriptor: int,
    initial_free_inodes: int,
    policy: LoadedExecutorPolicy,
) -> str | None:
    filesystem_policy = policy.data["filesystem"]
    assert isinstance(filesystem_policy, dict)
    current = os.statvfs(f"/proc/self/fd/{workspace_descriptor}")
    free_bytes = current.f_bavail * current.f_frsize
    if free_bytes < int(filesystem_policy["reserveFreeBytes"]):
        return "native executor consumed the reserved filesystem capacity"
    if (
        current.f_favail < int(filesystem_policy["reserveFreeInodes"])
        or initial_free_inodes - current.f_favail
        > int(filesystem_policy["maximumEntryDelta"])
    ):
        return "native executor exceeded the reserved inode capacity"
    return None


def _send_pidfd_signal(pidfd: int, signal_number: signal.Signals) -> None:
    sender = getattr(signal, "pidfd_send_signal", None)
    if sender is None:
        _integrity("native executor requires pidfd_send_signal support")
    try:
        sender(pidfd, signal_number)
    except ProcessLookupError:
        pass


def _terminate_probe_process(
    process: subprocess.Popen[bytes] | None,
    pidfd: int,
    cgroup: environment_tool.CgroupHandle,
) -> None:
    failures: list[str] = []
    leader_was_running = process is not None and process.poll() is None

    if leader_was_running and pidfd >= 0:
        try:
            _send_pidfd_signal(pidfd, signal.SIGTERM)
        except BaseException as error:
            failures.append(f"pidfd SIGTERM failed: {error}")
        try:
            assert process is not None
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        except BaseException as error:
            failures.append(f"leader wait after SIGTERM failed: {error}")

    try:
        environment_tool._kill_installer_cgroup(cgroup)  # noqa: SLF001
    except BaseException as error:
        failures.append(f"cgroup.kill failed: {error}")

    if process is not None and process.poll() is None:
        if pidfd < 0:
            failures.append("running leader is not covered by a pidfd")
        else:
            try:
                _send_pidfd_signal(pidfd, signal.SIGKILL)
            except BaseException as error:
                failures.append(f"pidfd SIGKILL failed: {error}")
        try:
            process.wait(timeout=environment_tool.CGROUP_DRAIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            failures.append("leader survived the cleanup deadline")
        except BaseException as error:
            failures.append(f"leader wait after cleanup failed: {error}")

    cgroup_empty = False
    try:
        cgroup_empty = environment_tool._cgroup_is_empty(cgroup)  # noqa: SLF001
    except BaseException as error:
        failures.append(f"cannot inspect cgroup population: {error}")
    if not cgroup_empty:
        try:
            environment_tool._kill_installer_cgroup(cgroup)  # noqa: SLF001
        except BaseException as error:
            failures.append(f"final cgroup.kill failed: {error}")
        try:
            cgroup_empty = environment_tool._cgroup_is_empty(cgroup)  # noqa: SLF001
        except BaseException as error:
            failures.append(f"cannot confirm final cgroup population: {error}")

    if cgroup_empty:
        try:
            environment_tool._remove_installer_cgroup(cgroup)  # noqa: SLF001
        except BaseException as error:
            failures.append(f"cannot remove empty cgroup: {error}")
    else:
        failures.append("cgroup population could not be proven empty")

    if failures:
        name = getattr(cgroup, "name", "<unknown>")
        raise environment_tool.InstallerIsolationError(
            f"native executor cgroup {name} cleanup failed: " + "; ".join(failures),
            retain_staging=True,
        )


def _run_namespace_probe(inputs: PinnedProbeInputs) -> bytes:
    _assert_pinned_probe_inputs(inputs)
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
        _schema("native executor requires Linux pidfd and signal-mask process control")
    process_policy = inputs.policy.data["process"]
    filesystem_policy = inputs.policy.data["filesystem"]
    assert isinstance(process_policy, dict)
    assert isinstance(filesystem_policy, dict)
    timeout_seconds = int(process_policy["probeTimeoutSeconds"])
    stdout_limit = int(process_policy["stdoutLimitBytes"])
    stderr_limit = int(process_policy["stderrLimitBytes"])
    workspace_descriptor = inputs.directories["workspace"].descriptor
    workspace_source = Path(f"/proc/self/fd/{workspace_descriptor}")
    initial_filesystem = os.statvfs(workspace_source)
    initial_free_bytes = initial_filesystem.f_bavail * initial_filesystem.f_frsize
    if (
        initial_free_bytes < int(filesystem_policy["minimumFreeBytes"])
        or initial_filesystem.f_favail < int(filesystem_policy["minimumFreeInodes"])
    ):
        _integrity("native executor workspace lacks the locked minimum capacity")

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
    try:
        try:
            previous_signal_mask = signal_mask(signal_block, CONTROL_SIGNALS)
        except (OSError, ValueError) as error:
            _integrity(f"cannot block native executor control signals: {error}")
        deadline = time.monotonic() + timeout_seconds
        try:
            pidfd_reserve = os.pidfd_open(os.getpid(), 0)
        except OSError as error:
            _integrity(f"cannot reserve native executor pidfd capacity: {error}")
        cgroup = environment_tool._prepare_installer_cgroup(workspace_source)  # noqa: SLF001
        argv, pass_fds = _probe_process_arguments(inputs, cgroup.procs_fd)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd="/",
                env=dict(PROBE_ENVIRONMENT),
                close_fds=True,
                pass_fds=pass_fds,
                start_new_session=True,
            )
        except OSError as error:
            _integrity(f"cannot launch native executor probe: {error}")
        if process.stdout is None or process.stderr is None:
            _integrity("native executor probe pipes were not created")
        streams = {
            process.stdout: stdout_buffer,
            process.stderr: stderr_buffer,
        }
        try:
            os.close(pidfd_reserve)
            pidfd_reserve = -1
            pidfd = os.pidfd_open(process.pid, 0)
        except OSError as error:
            _integrity(f"cannot open native executor leader pidfd: {error}")
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
                    f"native executor probe exceeded {timeout_seconds} seconds",
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
                    _append_bounded(
                        streams[stream],
                        chunk,
                        maximum=(
                            stdout_limit if stream is process.stdout else stderr_limit
                        ),
                        label="stdout" if stream is process.stdout else "stderr",
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
                    retain_staging=False,
                )
                break
            filesystem_violation = _filesystem_violation(
                workspace_descriptor,
                initial_filesystem.f_favail,
                inputs.policy,
            )
            if filesystem_violation is not None:
                failure = environment_tool.InstallerIsolationError(
                    filesystem_violation,
                    retain_staging=False,
                )
                break
        if failure is None:
            return_code = process.wait(timeout=10)
            violation = environment_tool._cgroup_limit_violation(cgroup)  # noqa: SLF001
            if violation is not None:
                failure = environment_tool.InstallerIsolationError(
                    violation,
                    retain_staging=False,
                )
            elif not environment_tool._cgroup_is_empty(cgroup):  # noqa: SLF001
                failure = environment_tool.InstallerIsolationError(
                    "native executor probe left descendant processes running",
                    retain_staging=False,
                )
            else:
                filesystem_violation = _filesystem_violation(
                    workspace_descriptor,
                    initial_filesystem.f_favail,
                    inputs.policy,
                )
                if filesystem_violation is not None:
                    failure = environment_tool.InstallerIsolationError(
                        filesystem_violation,
                        retain_staging=False,
                    )
    except BaseException as error:
        failure = error
        if process is not None and process.returncode is not None:
            return_code = process.returncode
    finally:
        if cgroup is not None and cgroup.procs_fd >= 0:
            try:
                os.close(cgroup.procs_fd)
            except BaseException as close_error:
                failure = _combine_failures(
                    failure,
                    close_error,
                    "parent cgroup.procs descriptor cleanup also failed",
                )
            finally:
                cgroup.procs_fd = -1
        if cgroup is not None and not cgroup.removed:
            terminate = failure is not None
            if not terminate:
                try:
                    terminate = not environment_tool._cgroup_is_empty(cgroup)  # noqa: SLF001
                except BaseException as inspect_error:
                    failure = _combine_failures(
                        failure,
                        inspect_error,
                        "cgroup population inspection failed before cleanup",
                    )
                    terminate = True
            try:
                if terminate:
                    _terminate_probe_process(process, pidfd, cgroup)
                else:
                    environment_tool._remove_installer_cgroup(cgroup)  # noqa: SLF001
            except BaseException as cleanup_error:
                failure = _combine_failures(
                    failure,
                    cleanup_error,
                    "native executor cgroup cleanup also failed",
                )
        if selector is not None:
            try:
                selector.close()
            except BaseException as close_error:
                failure = _combine_failures(
                    failure,
                    close_error,
                    "selector cleanup also failed",
                )
        for stream in streams:
            if not stream.closed:
                try:
                    stream.close()
                except BaseException as close_error:
                    failure = _combine_failures(
                        failure,
                        close_error,
                        "probe stream cleanup also failed",
                    )
        if pidfd >= 0:
            try:
                os.close(pidfd)
            except BaseException as close_error:
                failure = _combine_failures(
                    failure,
                    close_error,
                    "pidfd cleanup also failed",
                )
        if pidfd_reserve >= 0:
            try:
                os.close(pidfd_reserve)
            except BaseException as close_error:
                failure = _combine_failures(
                    failure,
                    close_error,
                    "reserved pidfd cleanup also failed",
                )
        if previous_signal_mask is not None:
            try:
                signal_mask(signal_setmask, previous_signal_mask)
            except BaseException as mask_error:
                failure = _combine_failures(
                    failure,
                    mask_error,
                    "control-signal mask restoration also failed",
                )
    if process is None or process.stdout is None or process.stderr is None:
        assert failure is not None
        raise failure
    stdout = bytes(stdout_buffer)
    stderr = bytes(stderr_buffer)
    if failure is not None:
        raise failure
    if return_code != 0:
        tail = stderr[-4096:].decode("utf-8", "replace").strip()
        _integrity(f"native executor probe exited {return_code}; stderr tail: {tail}")
    if process_policy["stderrAllowed"] is False and stderr:
        tail = stderr[-4096:].decode("utf-8", "replace").strip()
        _integrity(f"native executor probe emitted unexpected stderr: {tail}")
    return stdout


def _parse_probe_transcript(raw: bytes) -> dict[str, str]:
    if raw != EXPECTED_PROBE_TRANSCRIPT:
        _integrity("native executor probe transcript differs from the locked result")
    return dict(PROBE_RECORDS)


def _combine_failures(
    primary: BaseException | None,
    secondary: BaseException,
    context: str,
) -> BaseException:
    if primary is None:
        return secondary
    if isinstance(primary, source_tool.SourceToolError):
        return source_tool.SourceToolError(
            f"{primary}; {context}: {secondary}",
            primary.exit_code,
        )
    primary.add_note(f"{context}: {secondary}")
    return primary


def probe(
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
) -> bytes:
    native_build_tool._require_linux_root("executor probe")  # noqa: SLF001
    policy = load_execution_policy(
        policy_path,
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
    )
    _assert_runtime_policy(policy)
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
        _integrity("native executor profile changed during probe preflight")
    preparation = native_build_tool._snapshot_preparation_inputs(  # noqa: SLF001
        profile,
        source_workspace,
        composition_receipt_path,
    )
    directory_baseline = _snapshot_probe_directories(
        source_workspace,
        build_workspace,
    )
    _receipt, preparation_receipt_raw = native_build_tool._verify_prepared_workspace(  # noqa: SLF001
        build_workspace,
        preparation,
    )
    inputs = _pin_probe_inputs(
        policy,
        preparation,
        preparation_receipt_raw,
        directory_baseline,
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
    stdout: bytes | None = None
    probe_succeeded = False
    try:
        _reverify_probe_inputs(
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
        )
        try:
            stdout = _run_namespace_probe(inputs)
            _parse_probe_transcript(stdout)
            probe_succeeded = True
        except BaseException as error:
            failure = error
        if probe_succeeded:
            try:
                _reverify_probe_inputs(
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
                )
            except BaseException as error:
                failure = _combine_failures(
                    failure,
                    error,
                    "post-probe input verification also failed",
                )
    except BaseException as error:
        failure = _combine_failures(failure, error, "executor probe setup failed")
    finally:
        try:
            inputs.close()
        except BaseException as error:
            failure = _combine_failures(
                failure,
                error,
                "pinned-input cleanup also failed",
            )
    if failure is not None:
        raise failure
    assert stdout is not None
    print(
        "native executor namespace probe passed: "
        f"policy={policy.sha256}; transcript={hashlib.sha256(stdout).hexdigest()}"
    )
    return stdout


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
    _assert_runtime_policy(policy)
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
    probe_command = commands.add_parser(
        "probe",
        help="run the locked namespace probe without executing a native build",
    )
    probe_command.add_argument(
        "--source-cache",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_CACHE,
    )
    probe_command.add_argument(
        "--toolchain-cache",
        type=Path,
        default=native_build_tool.DEFAULT_TOOLCHAIN_CACHE,
    )
    probe_command.add_argument(
        "--source-workspace",
        type=Path,
        default=native_build_tool.DEFAULT_SOURCE_WORKSPACE,
    )
    probe_command.add_argument(
        "--apt-root",
        type=Path,
        default=native_build_tool.DEFAULT_APT_ROOT,
    )
    probe_command.add_argument(
        "--sdk-root",
        type=Path,
        default=native_build_tool.DEFAULT_SDK_ROOT,
    )
    probe_command.add_argument(
        "--composition-receipt",
        type=Path,
        default=native_build_tool.DEFAULT_COMPOSITION_RECEIPT,
    )
    probe_command.add_argument(
        "--build-workspace",
        type=Path,
        default=native_build_tool.DEFAULT_BUILD_WORKSPACE,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        policy_path = arguments.policy.resolve()
        profile_path = arguments.profile.resolve()
        source_manifest_path = arguments.source_manifest.resolve()
        toolchain_manifest_path = arguments.toolchain_manifest.resolve()
        if arguments.command == "validate":
            validate(
                policy_path,
                profile_path,
                source_manifest_path,
                toolchain_manifest_path,
            )
        elif arguments.command == "probe":
            probe(
                policy_path,
                profile_path,
                source_manifest_path,
                toolchain_manifest_path,
                arguments.source_cache.resolve(),
                arguments.toolchain_cache.resolve(),
                Path(os.path.abspath(arguments.source_workspace)),
                Path(os.path.abspath(arguments.apt_root)),
                Path(os.path.abspath(arguments.sdk_root)),
                Path(os.path.abspath(arguments.composition_receipt)),
                Path(os.path.abspath(arguments.build_workspace)),
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
