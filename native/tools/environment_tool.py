# SPDX-License-Identifier: GPL-3.0-or-later
"""Materialize and verify ZivPlayer's locked offline APT build root."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn

try:
    import resource
except ImportError:  # pragma: no cover - imported on Windows for pure validation tests
    resource = None  # type: ignore[assignment]

if sys.version_info < (3, 11):
    raise SystemExit("environment_tool.py requires Python 3.11 or newer")

import materialize_sources
import rootfs_tool
import source_tool
import toolchain_tool


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NATIVE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_CACHE = NATIVE_DIR / "cache" / "toolchain"
DEFAULT_ROOTFS = Path("/var/tmp/zivplayer-toolchain-apt")

RECEIPT_NAME = "ziv-toolchain-apt.json"
BASE_RECEIPT_NAME = rootfs_tool.RECEIPT_NAME
INPUT_NAME = "ziv-apt-input"
EVIDENCE_RELATIVE = PurePosixPath(
    "usr/share/zivplayer/toolchain-evidence/apt-stage"
)
TREE_FORMAT = "ziv-installed-tree-v2"
PROJECTION_FORMAT = "ziv-dpkg-projection-v1"
NORMALIZED_MTIME_NS = rootfs_tool.NORMALIZED_MTIME_NS
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_STATUS_BYTES = 32 * 1024 * 1024
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 128 * 1024 * 1024
MAX_JKS_BYTES = 16 * 1024 * 1024
MAX_XATTR_COUNT = 1024
MAX_XATTR_BYTES = 1024 * 1024
MAX_TREE_XATTR_BYTES = 16 * 1024 * 1024
MAX_TREE_PATH_BYTES = 16 * 1024 * 1024
MAX_TREE_ENTRIES = 100_000
MAX_TREE_FILE_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_INSTALL_TIMEOUT_SECONDS = 1800
CGROUP_ROOT = Path("/sys/fs/cgroup")
CGROUP_NAME_PATTERN = re.compile(r"^\.zivplayer-apt-[0-9a-f]{32}$")
CGROUP_REQUIRED_CONTROLLERS = frozenset({"cpu", "io", "memory", "pids"})
CGROUP_PIDS_MAX = 512
CGROUP_MEMORY_MAX = 2 * 1024 * 1024 * 1024
CGROUP_MEMORY_SWAP_MAX = 0
CGROUP_CPU_QUOTA_US = 200_000
CGROUP_CPU_PERIOD_US = 100_000
CGROUP_IO_BYTES_PER_SECOND = 8 * 1024 * 1024
CGROUP_IO_OPERATIONS_PER_SECOND = 2048
INSTALLER_NOFILE_LIMIT = 4096
INSTALLER_FILE_SIZE_LIMIT = 2 * 1024 * 1024 * 1024
MIN_INSTALL_FREE_BYTES = 8 * 1024 * 1024 * 1024
INSTALL_FREE_RESERVE_BYTES = 4 * 1024 * 1024 * 1024
MIN_INSTALL_FREE_INODES = 250_000
INSTALL_FREE_RESERVE_INODES = 100_000
MAX_INSTALL_INODE_DELTA = MAX_TREE_ENTRIES + 4096
CGROUP_DRAIN_TIMEOUT_SECONDS = 10

SHELL_SNAPSHOT_PATHS = (
    "native/toolchain/install-apt-rootfs.sh",
    "native/toolchain/install-apt-rootfs.bash",
    "native/toolchain/install-apt-namespace.bash",
    "native/toolchain/install-apt-chroot.bash",
)
SECCOMP_SNAPSHOT_PATHS = (
    "native/toolchain/install-seccomp.pl",
)
PYTHON_SNAPSHOT_PATHS = (
    "native/tools/environment_tool.py",
    "native/tools/exec_clean.py",
    "native/tools/rootfs_tool.py",
    "native/tools/toolchain_tool.py",
    "native/tools/source_tool.py",
    "native/tools/materialize_sources.py",
)
LOCK_EVIDENCE_KEYS = {
    "rootsFile": "locks/apt-roots.txt",
    "sourcesTemplate": "locks/ubuntu.sources.in",
    "baseDpkgLock": "locks/base-dpkg.tsv",
    "indexLock": "locks/apt-indices.tsv",
    "packageLock": "locks/apt-packages.tsv",
    "installOrder": "locks/apt-install-order.tsv",
}
DYNAMIC_EVIDENCE_NAMES = {
    "base/ziv-toolchain-base.json",
    "result/dpkg-projection.tsv",
    "result/generated-files.json",
    "result/sandbox-policy.json",
    "result/installer.stdout",
    "result/installer.stderr",
}
POLICY_RC_BYTES = b"#!/bin/sh\nexit 101\n"
POLICY_RC_SHA256 = "c2bcd9decf63ff2c0d9f473f38bc3607900530aad80f99139855d56678456230"
JKS_MAGIC = 0xFEEDFEED
JKS_VERSION = 2
JKS_TRUSTED_CERTIFICATE = 2
JKS_INTEGRITY_BYTES = 20
JKS_INTEGRITY_PHRASE = b"Mighty Aphrodite"
JKS_PASSWORD = "changeit"
NORMALIZED_TIMESTAMP_MILLIS = NORMALIZED_MTIME_NS // 1_000_000
CACERTS_RELATIVE = PurePosixPath("etc/ssl/certs/java/cacerts")
LDCONFIG_AUX_CACHE_RELATIVE = PurePosixPath("var/cache/ldconfig/aux-cache")


@dataclass(frozen=True)
class LockedState:
    data: dict[str, object]
    artifacts: list[dict[str, object]]
    oci_objects: list[dict[str, object]]
    manifest_sha256: str
    apt: dict[str, object]
    base_rows: list[tuple[str, str, str]]
    package_rows: list[dict[str, object]]
    install_order: list[dict[str, object]]
    snapshots: dict[str, bytes]


@dataclass(frozen=True)
class ScannedEntry:
    path: str
    kind: str
    mode: int
    uid: int
    gid: int
    mtime_ns: int
    size: int
    sha256: str | None
    link: str | None
    xattrs_sha256: str | None


@dataclass
class CgroupHandle:
    root_fd: int
    child_fd: int
    name: str
    child_identity: tuple[int, int]
    procs_fd: int
    baseline_pids_events: dict[str, int]
    baseline_memory_events: dict[str, int]
    baseline_swap_events: dict[str, int]
    device: str
    removed: bool = False


class InstallerIsolationError(source_tool.SourceToolError):
    def __init__(self, message: str, *, retain_staging: bool) -> None:
        super().__init__(message, source_tool.EXIT_INTEGRITY)
        self.retain_staging = retain_staging


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _safe_evidence_name(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    raw_parts = value.split("/")
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in raw_parts)
        or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        _integrity(f"unsafe evidence path: {value!r}")
    return path


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            _integrity("short write while preparing the installed environment")
        view = view[written:]


def _write_exclusive(path: Path, payload: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, mode)
        _write_all(descriptor, payload)
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, 0, 0)
        os.fsync(descriptor)
    except OSError as error:
        _integrity(f"cannot write installed-environment file {path}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _copy_verified_stream(stream: BinaryIO, destination: Path, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(destination, flags, mode)
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            _write_all(descriptor, chunk)
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, 0, 0)
        os.fsync(descriptor)
    except OSError as error:
        _integrity(f"cannot snapshot locked archive {destination}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _mkdir(path: Path, mode: int) -> None:
    try:
        path.mkdir()
        path.chmod(mode)
        os.chown(path, 0, 0)
    except OSError as error:
        _integrity(f"cannot create installed-environment directory {path}: {error}")


def _read_stable(
    path: Path,
    *,
    maximum: int,
    label: str,
    missing_exit: int = source_tool.EXIT_MISSING,
) -> bytes:
    return toolchain_tool._read_stable_bytes(
        path,
        maximum=maximum,
        label=label,
        missing_exit=missing_exit,
    )


def _locked_repository_snapshots(
    data: dict[str, object],
    manifest: Path,
    source_manifest: Path,
    cache: Path,
) -> dict[str, bytes]:
    apt = toolchain_tool._expect_table(data["apt"], "apt")
    snapshots = {
        "manifests/toolchain-manifest.toml": _read_stable(
            manifest,
            maximum=toolchain_tool.MAX_MANIFEST_BYTES,
            label="toolchain manifest evidence",
            missing_exit=source_tool.EXIT_SCHEMA,
        ),
        "manifests/source-manifest.toml": _read_stable(
            source_manifest,
            maximum=toolchain_tool.MAX_MANIFEST_BYTES,
            label="source manifest evidence",
        ),
    }
    for key, evidence_name in LOCK_EVIDENCE_KEYS.items():
        snapshots[evidence_name] = toolchain_tool._read_locked_repository_file(
            apt,
            key,
            repository_root=REPOSITORY_ROOT,
        )
    snapshots["cache/SHA256SUMS"] = _read_stable(
        cache / "apt" / "SHA256SUMS",
        maximum=toolchain_tool.MAX_APT_LOCK_BYTES,
        label="APT cache SHA256SUMS evidence",
    )
    simulation = _read_stable(
        cache / "apt" / "simulation.txt",
        maximum=toolchain_tool.MAX_APT_LOCK_BYTES,
        label="APT simulation evidence",
    )
    if (
        len(simulation) != int(apt["simulationSize"])
        or _sha256(simulation) != apt["simulationSha256"]
    ):
        _integrity("APT simulation changed after cache verification")
    snapshots["cache/simulation.txt"] = simulation
    for relative in (
        *SHELL_SNAPSHOT_PATHS,
        *SECCOMP_SNAPSHOT_PATHS,
        *PYTHON_SNAPSHOT_PATHS,
    ):
        snapshots[f"helpers/{relative}"] = _read_stable(
            REPOSITORY_ROOT / relative,
            maximum=toolchain_tool.MAX_MANIFEST_BYTES,
            label=f"helper snapshot {relative}",
        )
    return snapshots


def _load_locked_state(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
) -> LockedState:
    (
        data,
        artifacts,
        oci_objects,
        _source_data,
        manifest_sha256,
    ) = toolchain_tool.load_manifest_snapshot(
        manifest,
        source_manifest_path=source_manifest,
    )
    toolchain_tool.verify_cache(data, artifacts, oci_objects, cache)
    apt = toolchain_tool._expect_table(data["apt"], "apt")
    base_raw = toolchain_tool._read_locked_repository_file(
        apt,
        "baseDpkgLock",
        repository_root=REPOSITORY_ROOT,
    )
    base_rows = toolchain_tool._parse_tsv(
        base_raw,
        toolchain_tool.APT_BASE_HEADER,
        "base dpkg lock",
    )
    package_rows = toolchain_tool._load_apt_package_rows(
        apt,
        repository_root=REPOSITORY_ROOT,
    )
    install_order = toolchain_tool._load_apt_install_order(
        apt,
        package_rows,
        repository_root=REPOSITORY_ROOT,
    )
    snapshots = _locked_repository_snapshots(
        data,
        manifest,
        source_manifest,
        cache,
    )
    if _sha256(snapshots["manifests/toolchain-manifest.toml"]) != manifest_sha256:
        _integrity("toolchain manifest changed across the locked-state snapshot")
    project = toolchain_tool._expect_table(data["project"], "project")
    if (
        _sha256(snapshots["manifests/source-manifest.toml"])
        != project["sourceManifestSha256"]
    ):
        _integrity("source manifest changed across the locked-state snapshot")
    return LockedState(
        data=data,
        artifacts=artifacts,
        oci_objects=oci_objects,
        manifest_sha256=manifest_sha256,
        apt=apt,
        base_rows=base_rows,
        package_rows=package_rows,
        install_order=install_order,
        snapshots=snapshots,
    )


def _expected_projection(
    base_rows: list[tuple[str, str, str]],
    package_rows: list[dict[str, object]],
) -> dict[tuple[str, str], str]:
    expected: dict[tuple[str, str], str] = {}
    for package, architecture, version in base_rows:
        identity = (package, architecture)
        if identity in expected:
            _integrity("base dpkg lock repeats a package/architecture identity")
        expected[identity] = version
    for row in package_rows:
        identity = (str(row["package"]), str(row["architecture"]))
        if identity in expected:
            _integrity(
                "APT package lock overlaps the base package projection: "
                f"{identity[0]}:{identity[1]}"
            )
        expected[identity] = str(row["version"])
    return expected


def _installed_projection(
    status_raw: bytes,
    base_rows: list[tuple[str, str, str]],
    package_rows: list[dict[str, object]],
) -> bytes:
    expected = _expected_projection(base_rows, package_rows)
    records = toolchain_tool._parse_debian_control(status_raw, "installed dpkg status")
    actual: dict[tuple[str, str], str] = {}
    for index, record in enumerate(records):
        required = ("Package", "Architecture", "Version", "Status")
        if any(key not in record for key in required):
            _integrity(f"installed dpkg record {index} omits identity metadata")
        if record["Status"] != "install ok installed":
            _integrity(
                f"installed dpkg record is not fully installed: {record['Package']}"
            )
        if "Triggers-Pending" in record or "Triggers-Awaited" in record:
            _integrity(
                f"installed dpkg record has an undrained trigger: {record['Package']}"
            )
        toolchain_tool._validate_debian_identity(
            record["Package"],
            record["Architecture"],
            record["Version"],
            f"installed dpkg record {index}",
        )
        identity = (record["Package"], record["Architecture"])
        if identity in actual:
            _integrity(
                "installed dpkg status repeats a package/architecture identity: "
                f"{identity[0]}:{identity[1]}"
            )
        actual[identity] = record["Version"]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        drift = sorted(
            identity
            for identity in set(actual) & set(expected)
            if actual[identity] != expected[identity]
        )
        _integrity(
            "installed dpkg projection differs from the exact locked union; "
            f"missing={missing}, extra={extra}, versionDrift={drift}"
        )
    lines = ["package\tarchitecture\tversion"]
    for package, architecture in sorted(actual):
        lines.append(f"{package}\t{architecture}\t{actual[(package, architecture)]}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _path_text(path: Path, rootfs: Path) -> str:
    relative = path.relative_to(rootfs)
    parts = relative.parts
    value = PurePosixPath(*relative.parts).as_posix()
    try:
        encoded_parts = [part.encode("utf-8") for part in parts]
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        _integrity(f"installed tree path is not UTF-8 encodable: {error}")
    if (
        not encoded
        or len(parts) > rootfs_tool.MAX_PATH_DEPTH
        or len(encoded) > rootfs_tool.MAX_PATH_BYTES
        or any(len(part) > rootfs_tool.MAX_COMPONENT_BYTES for part in encoded_parts)
        or any(unicodedata.normalize("NFKC", part) != part for part in parts)
        or "\\" in value
        or b"\t" in encoded
        or b"\n" in encoded
        or b"\r" in encoded
        or b"\x00" in encoded
    ):
        _integrity(f"installed tree path has unsafe control bytes: {value!r}")
    return value


def _utf8_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        _integrity(f"filesystem name is not UTF-8 encodable: {error}")


_STABLE_STAT_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_gid",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
    "st_nlink",
)


def _assert_stable_stat(before: os.stat_result, after: os.stat_result, label: str) -> None:
    if any(
        getattr(before, field) != getattr(after, field)
        for field in _STABLE_STAT_FIELDS
    ):
        _integrity(f"{label} changed while scanning")


def _xattr_digest(path: Path) -> tuple[str | None, int]:
    try:
        def snapshot() -> list[tuple[bytes, bytes]]:
            names = sorted(
                os.listxattr(path, follow_symlinks=False),
                key=lambda value: value.encode("utf-8"),
            )
            if len(names) > MAX_XATTR_COUNT:
                _integrity(f"too many extended attributes on {path}")
            values: list[tuple[bytes, bytes]] = []
            total = 0
            for name in names:
                name_bytes = name.encode("utf-8")
                if not name.startswith("user."):
                    _integrity(
                        f"installed path carries a forbidden xattr namespace on {path}: "
                        f"{name}"
                    )
                value = os.getxattr(path, name, follow_symlinks=False)
                total += len(name_bytes) + len(value)
                if total > MAX_XATTR_BYTES:
                    _integrity(f"extended attributes are too large on {path}")
                values.append((name_bytes, value))
            return values

        values = snapshot()
        if snapshot() != values:
            _integrity(f"extended attributes changed while scanning {path}")
        if not values:
            return None, 0
        digest = hashlib.sha256()
        rootfs_tool._digest_record(digest, b"F", b"ziv-xattrs-v1")
        for name, value in values:
            rootfs_tool._digest_record(digest, b"N", name)
            rootfs_tool._digest_record(digest, b"V", value)
        return digest.hexdigest(), sum(len(name) + len(value) for name, value in values)
    except (AttributeError, UnicodeEncodeError, OSError) as error:
        _integrity(f"cannot scan extended attributes for {path}: {error}")


def _stable_file_record(
    path: Path,
    rootfs: Path,
) -> tuple[ScannedEntry, tuple[int, int], int, int]:
    before = path.lstat()
    descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            _integrity(f"installed tree path is not a regular file: {path}")
        _assert_stable_stat(before, opened, f"installed tree file {path}")
        if opened.st_size < 0 or opened.st_size > MAX_TREE_FILE_BYTES:
            _integrity(f"installed tree file exceeds the bounded size: {path}")
        digest_state = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                _integrity(f"installed tree file ended while hashing: {path}")
            digest_state.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _integrity(f"installed tree file grew while hashing: {path}")
        hashed = os.fstat(descriptor)
        _assert_stable_stat(opened, hashed, f"installed tree file {path}")
        identity = (opened.st_dev, opened.st_ino)
        size = opened.st_size
        digest = digest_state.hexdigest()
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot hash installed tree file {path}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    xattrs, xattr_bytes = _xattr_digest(path)
    after = path.lstat()
    _assert_stable_stat(before, after, f"installed tree file {path}")
    if (before.st_dev, before.st_ino) != identity or before.st_size != size:
        _integrity(f"installed tree file identity changed while hashing: {path}")
    entry = ScannedEntry(
        path=_path_text(path, rootfs),
        kind="file",
        mode=stat.S_IMODE(before.st_mode),
        uid=before.st_uid,
        gid=before.st_gid,
        mtime_ns=before.st_mtime_ns,
        size=size,
        sha256=digest,
        link=None,
        xattrs_sha256=xattrs,
    )
    return entry, identity, before.st_nlink, xattr_bytes


def _entry_bytes(entry: ScannedEntry) -> bytes:
    values = (
        entry.path,
        entry.kind,
        format(entry.mode, "04o"),
        str(entry.uid),
        str(entry.gid),
        str(entry.mtime_ns),
        str(entry.size),
        entry.sha256 or "-",
        entry.link or "-",
        entry.xattrs_sha256 or "-",
    )
    return "\t".join(values).encode("utf-8")


def _scan_tree(rootfs: Path) -> dict[str, object]:
    root_info = rootfs.lstat()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or rootfs.is_symlink()
        or stat.S_IMODE(root_info.st_mode) != 0o700
        or (root_info.st_uid, root_info.st_gid) != (0, 0)
        or root_info.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("installed rootfs root metadata is not canonical")
    root_xattrs, xattr_budget = _xattr_digest(rootfs)
    receipt_key = unicodedata.normalize("NFKC", RECEIPT_NAME).casefold()
    plain: list[ScannedEntry] = []
    files: list[tuple[ScannedEntry, tuple[int, int], int, int]] = []
    seen_paths: set[str] = set()
    seen_file_sizes: dict[tuple[int, int], int] = {}
    seen_file_ctimes: dict[tuple[int, int], int] = {}
    file_record_cache: dict[
        tuple[int, int],
        tuple[ScannedEntry, int],
    ] = {}
    physical_budget = 0
    path_budget = 0
    scanned_entries = 0
    for current_text, names, leaf_names in os.walk(
        rootfs,
        topdown=True,
        followlinks=False,
        onerror=lambda error: _integrity(f"cannot walk installed tree: {error}"),
    ):
        current = Path(current_text)
        if len(names) + len(leaf_names) > MAX_TREE_ENTRIES - scanned_entries + 1:
            _integrity("installed tree directory exceeds the remaining entry budget")
        names.sort(key=_utf8_sort_key)
        leaf_names.sort(key=_utf8_sort_key)
        combined = sorted(names + leaf_names, key=_utf8_sort_key)
        descend_names: list[str] = []
        for name in combined:
            path = current / name
            relative = _path_text(path, rootfs)
            path_budget += len(relative.encode("utf-8"))
            if path_budget > MAX_TREE_PATH_BYTES:
                _integrity("installed tree exceeds the aggregate path-byte limit")
            if relative in seen_paths:
                _integrity(f"installed tree repeats path: {relative}")
            seen_paths.add(relative)
            receipt_collision = path.parent == rootfs and (
                unicodedata.normalize("NFKC", name).casefold() == receipt_key
            )
            if receipt_collision:
                if name != RECEIPT_NAME:
                    _integrity("installed tree contains a receipt-name collision")
                receipt_info = path.lstat()
                if not stat.S_ISREG(receipt_info.st_mode):
                    _integrity("installed tree receipt path is not a regular file")
                continue
            scanned_entries += 1
            if scanned_entries > MAX_TREE_ENTRIES:
                _integrity("installed tree exceeds the entry-count limit")
            info = path.lstat()
            if info.st_dev != root_info.st_dev:
                _integrity(f"installed tree crosses a filesystem boundary: {relative}")
            if info.st_mtime_ns != NORMALIZED_MTIME_NS:
                _integrity(f"installed tree timestamp is not normalized: {relative}")
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISREG(info.st_mode):
                identity = (info.st_dev, info.st_ino)
                previous_size = seen_file_sizes.get(identity)
                if previous_size is None:
                    if info.st_size < 0 or info.st_size > MAX_TREE_FILE_BYTES:
                        _integrity("installed tree contains an oversized regular file")
                    physical_budget += info.st_size
                    if physical_budget > MAX_TREE_FILE_BYTES:
                        _integrity("installed tree exceeds the physical-file byte limit")
                    seen_file_sizes[identity] = info.st_size
                    seen_file_ctimes[identity] = info.st_ctime_ns
                elif (
                    previous_size != info.st_size
                    or seen_file_ctimes[identity] != info.st_ctime_ns
                ):
                    _integrity("installed tree hard-link changed while scanning")
                cached = file_record_cache.get(identity)
                if cached is None:
                    record = _stable_file_record(path, rootfs)
                    file_record_cache[identity] = (record[0], record[2])
                    xattr_budget += record[3]
                    if xattr_budget > MAX_TREE_XATTR_BYTES:
                        _integrity("installed tree exceeds the aggregate xattr-byte limit")
                    files.append(record)
                else:
                    canonical, links = cached
                    if (
                        canonical.mode != mode
                        or canonical.uid != info.st_uid
                        or canonical.gid != info.st_gid
                        or canonical.mtime_ns != info.st_mtime_ns
                        or canonical.size != info.st_size
                        or links != info.st_nlink
                    ):
                        _integrity("installed tree hard-link metadata changed while scanning")
                    files.append(
                        (
                            ScannedEntry(
                                relative,
                                "file",
                                mode,
                                info.st_uid,
                                info.st_gid,
                                info.st_mtime_ns,
                                info.st_size,
                                canonical.sha256,
                                None,
                                canonical.xattrs_sha256,
                            ),
                            identity,
                            links,
                            0,
                        )
                    )
            elif stat.S_ISDIR(info.st_mode):
                before = info
                xattrs, xattr_bytes = _xattr_digest(path)
                after = path.lstat()
                _assert_stable_stat(before, after, f"installed tree directory {path}")
                xattr_budget += xattr_bytes
                if xattr_budget > MAX_TREE_XATTR_BYTES:
                    _integrity("installed tree exceeds the aggregate xattr-byte limit")
                descend_names.append(name)
                plain.append(
                    ScannedEntry(
                        relative,
                        "directory",
                        mode,
                        info.st_uid,
                        info.st_gid,
                        info.st_mtime_ns,
                        0,
                        None,
                        None,
                        xattrs,
                    )
                )
            elif stat.S_ISLNK(info.st_mode):
                before = info
                link = os.readlink(path)
                link_bytes = _utf8_sort_key(link)
                if any(character in link for character in ("\x00", "\t", "\n", "\r")):
                    _integrity(f"installed tree symlink target is not canonical: {relative}")
                rootfs_tool._resolve_symlink_target(
                    PurePosixPath(relative),
                    link,
                    f"installed tree symlink {relative}",
                )
                xattrs, xattr_bytes = _xattr_digest(path)
                after = path.lstat()
                _assert_stable_stat(before, after, f"installed tree symlink {path}")
                xattr_budget += xattr_bytes
                if xattr_budget > MAX_TREE_XATTR_BYTES:
                    _integrity("installed tree exceeds the aggregate xattr-byte limit")
                plain.append(
                    ScannedEntry(
                        relative,
                        "symlink",
                        mode,
                        info.st_uid,
                        info.st_gid,
                        info.st_mtime_ns,
                        len(link_bytes),
                        None,
                        link,
                        xattrs,
                    )
                )
            else:
                _integrity(f"installed tree contains a special entry: {relative}")
        names[:] = descend_names
    groups: dict[tuple[int, int], list[tuple[ScannedEntry, int]]] = {}
    for entry, identity, links, _xattr_bytes in files:
        groups.setdefault(identity, []).append((entry, links))
    materialized_files: list[ScannedEntry] = []
    physical_bytes = 0
    for group in groups.values():
        group.sort(key=lambda item: _utf8_sort_key(item[0].path))
        expected_links = group[0][1]
        if expected_links != len(group) or any(links != expected_links for _, links in group):
            _integrity(
                "installed tree has a regular-file hard link outside the scanned root: "
                f"{group[0][0].path}"
            )
        canonical = group[0][0]
        materialized_files.append(canonical)
        physical_bytes += canonical.size
        for entry, _links in group[1:]:
            materialized_files.append(
                ScannedEntry(
                    entry.path,
                    "hardlink",
                    entry.mode,
                    entry.uid,
                    entry.gid,
                    entry.mtime_ns,
                    entry.size,
                    None,
                    canonical.path,
                    entry.xattrs_sha256,
                )
            )
    entries = sorted(
        [*plain, *materialized_files],
        key=lambda entry: entry.path.encode("utf-8"),
    )
    digest = hashlib.sha256()
    rootfs_tool._digest_record(digest, b"F", TREE_FORMAT.encode("ascii"))
    rootfs_tool._digest_record(
        digest,
        b"R",
        (
            f"0700\t0\t0\t{NORMALIZED_MTIME_NS}\t{root_xattrs or '-'}"
        ).encode("ascii"),
    )
    counts = {"directories": 0, "files": 0, "hardlinks": 0, "symlinks": 0}
    count_key = {
        "directory": "directories",
        "file": "files",
        "hardlink": "hardlinks",
        "symlink": "symlinks",
    }
    for entry in entries:
        rootfs_tool._digest_record(digest, b"E", _entry_bytes(entry))
        counts[count_key[entry.kind]] += 1
    if physical_bytes != physical_budget:
        _integrity("installed tree physical-file accounting changed while scanning")
    return {
        "format": TREE_FORMAT,
        "sha256": digest.hexdigest(),
        "entries": len(entries),
        "fileBytes": physical_bytes,
        "rootXattrsSha256": root_xattrs,
        **counts,
    }


def _require_real_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        _integrity(f"cannot inspect {label} {path}: {error}")
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or resolved != path:
        _integrity(f"{label} must be a canonical real directory: {path}")
    return info


def _open_real_parent_beneath(
    rootfs: Path,
    relative: PurePosixPath,
    label: str,
) -> tuple[int, str]:
    if relative.is_absolute() or not relative.name or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        _integrity(f"{label} has an unsafe rootfs-relative path")
    root_info = _require_real_directory(rootfs, "installed rootfs")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(rootfs, directory_flags)
        opened_root = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or (opened_root.st_dev, opened_root.st_ino)
            != (root_info.st_dev, root_info.st_ino)
        ):
            _integrity("installed rootfs changed while resolving an internal path")
        root_device = opened_root.st_dev
        for part in relative.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_dev != root_device:
                _integrity(f"{label} traverses a non-directory or nested mount")
        return descriptor, relative.name
    except source_tool.SourceToolError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        _integrity(f"cannot resolve {label} beneath the installed rootfs: {error}")


def _read_stable_beneath(
    rootfs: Path,
    relative: PurePosixPath,
    *,
    maximum: int,
    label: str,
) -> tuple[bytes, os.stat_result]:
    parent_descriptor, leaf = _open_real_parent_beneath(rootfs, relative, label)
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(leaf, flags, dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 0 or before.st_size > maximum:
            _integrity(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                _integrity(f"{label} changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        current = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        metadata = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, key) != getattr(after, key) for key in metadata) or any(
            getattr(after, key) != getattr(current, key) for key in metadata
        ):
            _integrity(f"{label} changed while reading")
        return b"".join(chunks), after
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot read {label} beneath the installed rootfs: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _assert_absent_beneath(
    rootfs: Path,
    relative: PurePosixPath,
    label: str,
) -> None:
    parent_descriptor, leaf = _open_real_parent_beneath(rootfs, relative, label)
    try:
        try:
            os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            _integrity(f"cannot inspect absent {label}: {error}")
        _integrity(f"{label} must be absent")
    finally:
        os.close(parent_descriptor)


def _ensure_directory_chain(root: Path, relative: PurePosixPath) -> Path:
    current = root
    root_device = root.lstat().st_dev
    for part in relative.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            _mkdir(current, 0o755)
            info = current.lstat()
        except OSError as error:
            _integrity(f"cannot inspect directory chain {current}: {error}")
        if (
            not stat.S_ISDIR(info.st_mode)
            or current.is_symlink()
            or info.st_dev != root_device
        ):
            _integrity(f"installed-environment path traverses a non-directory: {current}")
    return current


def _write_relative(
    root: Path,
    relative_text: str,
    payload: bytes,
    *,
    mode: int,
) -> Path:
    relative = _safe_evidence_name(relative_text)
    parent = _ensure_directory_chain(root, relative.parent)
    destination = parent / relative.name
    if destination.exists() or destination.is_symlink():
        _integrity(f"refusing to replace installed-environment path: {destination}")
    _write_exclusive(destination, payload, mode)
    return destination


def _prepare_control(outer: Path, snapshots: dict[str, bytes]) -> tuple[Path, tuple[int, int]]:
    control = outer / "control"
    _mkdir(control, 0o700)
    identity = (control.lstat().st_dev, control.lstat().st_ino)
    for relative in (*SHELL_SNAPSHOT_PATHS, "native/tools/exec_clean.py"):
        _write_relative(
            control,
            relative,
            snapshots[f"helpers/{relative}"],
            mode=0o400,
        )
    materialize_sources._fsync_workspace_directories(control)
    return control, identity


def _prepare_apt_input(
    rootfs: Path,
    state: LockedState,
    cache: Path,
) -> tuple[Path, tuple[int, int]]:
    input_root = rootfs / INPUT_NAME
    if input_root.exists() or input_root.is_symlink():
        _integrity("prepared base rootfs already contains APT installer input")
    _mkdir(input_root, 0o700)
    identity = (input_root.lstat().st_dev, input_root.lstat().st_ino)
    debs = input_root / "debs"
    _mkdir(debs, 0o700)
    order_bytes = state.snapshots["locks/apt-install-order.tsv"]
    _write_exclusive(input_root / "install-order.tsv", order_bytes, 0o400)
    _write_exclusive(
        input_root / "install-apt-chroot.bash",
        state.snapshots["helpers/native/toolchain/install-apt-chroot.bash"],
        0o400,
    )
    _write_exclusive(
        input_root / "install-seccomp.pl",
        state.snapshots["helpers/native/toolchain/install-seccomp.pl"],
        0o400,
    )
    for row in state.package_rows:
        archive = str(row["archive"])
        destination = debs / archive
        with source_tool.open_verified_archive(
            {
                "id": row["package"],
                "size": row["size"],
                "sha256": row["sha256"],
            },
            cache / "apt" / "debs" / archive,
        ) as stream:
            _copy_verified_stream(stream, destination, 0o400)
    actual_names = {
        entry.name
        for entry in os.scandir(debs)
        if entry.is_file(follow_symlinks=False)
    }
    expected_names = {str(row["archive"]) for row in state.package_rows}
    if actual_names != expected_names:
        _integrity("prepared APT input does not contain the exact locked deb set")
    materialize_sources._fsync_workspace_directories(input_root)
    return input_root, identity


def _disable_apt_sources(rootfs: Path) -> None:
    sources = rootfs / "etc" / "apt" / "sources.list"
    source_directory = rootfs / "etc" / "apt" / "sources.list.d"
    ubuntu_sources = source_directory / "ubuntu.sources"
    _require_real_directory(source_directory, "APT sources directory")
    try:
        source_info = sources.lstat()
        ubuntu_info = ubuntu_sources.lstat()
    except OSError as error:
        _integrity(f"locked base APT source files are missing: {error}")
    if not stat.S_ISREG(source_info.st_mode) or sources.is_symlink():
        _integrity("locked base sources.list is not a regular file")
    if not stat.S_ISREG(ubuntu_info.st_mode) or ubuntu_sources.is_symlink():
        _integrity("locked base ubuntu.sources is not a regular file")
    try:
        sources.unlink()
        ubuntu_sources.unlink()
    except OSError as error:
        _integrity(f"cannot disable locked APT repositories: {error}")
    _write_exclusive(sources, b"", 0o644)
    if any(os.scandir(source_directory)):
        _integrity("APT sources directory contains an unexpected source definition")


def _assert_apt_sources_disabled(rootfs: Path) -> None:
    source_directory = rootfs / "etc" / "apt" / "sources.list.d"
    _require_real_directory(source_directory, "APT sources directory")
    raw, _info = _read_stable_beneath(
        rootfs,
        PurePosixPath("etc/apt/sources.list"),
        maximum=1024,
        label="disabled APT sources.list",
    )
    if raw:
        _integrity("installed rootfs APT sources.list is not empty")
    try:
        entries = list(os.scandir(source_directory))
    except OSError as error:
        _integrity(f"cannot enumerate disabled APT sources: {error}")
    if entries:
        _integrity("installed rootfs contains an enabled APT source definition")


def _assert_policy_rc(rootfs: Path) -> None:
    raw, info = _read_stable_beneath(
        rootfs,
        PurePosixPath("usr/sbin/policy-rc.d"),
        maximum=1024,
        label="locked policy-rc.d",
    )
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o755
        or (info.st_uid, info.st_gid) != (0, 0)
    ):
        _integrity("locked policy-rc.d metadata changed")
    if raw != POLICY_RC_BYTES or _sha256(raw) != POLICY_RC_SHA256:
        _integrity("locked policy-rc.d content changed")


def _mounts_below(path: Path) -> bytes:
    try:
        result = subprocess.run(
            [
                "/usr/bin/findmnt",
                "--json",
                "--output",
                "TARGET,FSTYPE,OPTIONS",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
            close_fds=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _integrity(f"cannot inspect rootfs mount state: {error}")
    if result.returncode != 0:
        _integrity(
            "findmnt failed while checking rootfs mount state: "
            + result.stderr.decode("utf-8", "replace").strip()
        )
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _integrity(f"cannot decode findmnt mount inventory: {error}")
    records: list[str] = []
    path_text = path.as_posix()
    prefix = path_text.rstrip("/") + "/"

    def visit(items: object) -> None:
        if not isinstance(items, list):
            _integrity("findmnt mount inventory has an invalid filesystems list")
        for item in items:
            if not isinstance(item, dict):
                _integrity("findmnt mount inventory has an invalid entry")
            target = item.get("target")
            if isinstance(target, str) and (target == path_text or target.startswith(prefix)):
                records.append(
                    f"{target}\t{item.get('fstype', '-')}\t{item.get('options', '-')}"
                )
            if "children" in item:
                visit(item["children"])

    if not isinstance(value, dict) or "filesystems" not in value:
        _integrity("findmnt mount inventory is not a JSON object with filesystems")
    visit(value["filesystems"])
    records.sort(key=lambda item: item.encode("utf-8"))
    return "\n".join(records).encode("utf-8")


def _assert_no_nested_mounts(path: Path) -> None:
    mounts = _mounts_below(path)
    if mounts:
        _integrity(
            "rootfs has a host-visible mount and cannot be safely consumed or removed: "
            + mounts.decode("utf-8", "replace")
        )


def _read_pseudo_file(path: Path, *, maximum: int, label: str) -> bytes:
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        before = path.lstat()
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_uid, opened.st_gid) != (0, 0)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            _integrity(f"{label} is not a canonical root-owned pseudo file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(4096, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                _integrity(f"{label} exceeds its bounded size")
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _integrity(f"{label} changed while reading")
        return b"".join(chunks)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot read {label}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_cgroup_file(
    directory_descriptor: int,
    name: str,
    *,
    maximum: int = 64 * 1024,
) -> bytes:
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid) != (0, 0):
            _integrity(f"cgroup control {name} is not a root-owned regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(4096, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                _integrity(f"cgroup control {name} exceeds its bounded size")
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
        ):
            _integrity(f"cgroup control {name} changed while reading")
        return b"".join(chunks)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot read cgroup control {name}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_cgroup_file(directory_descriptor: int, name: str, payload: bytes) -> None:
    descriptor = -1
    try:
        flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0):
            _integrity(f"cgroup control {name} is not a root-owned regular file")
        written = os.write(descriptor, payload)
        if written != len(payload):
            _integrity(f"short write to cgroup control {name}")
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot write cgroup control {name}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_counter_file(raw: bytes, label: str) -> dict[str, int]:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        _integrity(f"{label} is not ASCII: {error}")
    result: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[0] or not fields[1].isdigit():
            _integrity(f"{label} contains a malformed counter")
        if fields[0] in result:
            _integrity(f"{label} repeats counter {fields[0]}")
        result[fields[0]] = int(fields[1])
    if not result:
        _integrity(f"{label} is empty")
    return result


def _cgroup_is_empty(handle: CgroupHandle) -> bool:
    events = _parse_counter_file(
        _read_cgroup_file(handle.child_fd, "cgroup.events"),
        "cgroup.events",
    )
    state = _parse_counter_file(
        _read_cgroup_file(handle.child_fd, "cgroup.stat"),
        "cgroup.stat",
    )
    if "populated" not in events or "nr_descendants" not in state:
        _integrity("cgroup state omits population counters")
    return events["populated"] == 0 and state["nr_descendants"] == 0


def _expected_cgroup_controls(device: str) -> dict[str, bytes]:
    return {
        "pids.max": f"{CGROUP_PIDS_MAX}\n".encode("ascii"),
        "memory.max": f"{CGROUP_MEMORY_MAX}\n".encode("ascii"),
        "memory.swap.max": f"{CGROUP_MEMORY_SWAP_MAX}\n".encode("ascii"),
        "cpu.max": f"{CGROUP_CPU_QUOTA_US} {CGROUP_CPU_PERIOD_US}\n".encode("ascii"),
        "io.max": (
            f"{device} rbps={CGROUP_IO_BYTES_PER_SECOND} "
            f"wbps={CGROUP_IO_BYTES_PER_SECOND} "
            f"riops={CGROUP_IO_OPERATIONS_PER_SECOND} "
            f"wiops={CGROUP_IO_OPERATIONS_PER_SECOND}\n"
        ).encode("ascii"),
    }


def _verify_cgroup_limits(handle: CgroupHandle) -> None:
    for control, expected in _expected_cgroup_controls(handle.device).items():
        if _read_cgroup_file(handle.child_fd, control).strip() != expected.strip():
            _integrity(f"cgroup control {control} drifted from the locked limit")


def _cgroup_limit_violation(handle: CgroupHandle) -> str | None:
    _verify_cgroup_limits(handle)
    current_sets = (
        (
            "pids.events",
            handle.baseline_pids_events,
            {"max"},
        ),
        (
            "memory.events",
            handle.baseline_memory_events,
            {"max", "oom", "oom_kill", "oom_group_kill"},
        ),
        (
            "memory.swap.events",
            handle.baseline_swap_events,
            {"max", "fail"},
        ),
    )
    for name, baseline, fatal_keys in current_sets:
        current = _parse_counter_file(_read_cgroup_file(handle.child_fd, name), name)
        for key in fatal_keys:
            if key not in baseline or key not in current:
                _integrity(f"{name} omits required counter {key}")
            if current[key] > baseline[key]:
                return f"offline installer hit cgroup limit {name}:{key}"
            if current[key] < baseline[key]:
                _integrity(f"{name} counter {key} moved backwards")
    return None


def _sandbox_policy_record() -> bytes:
    return _canonical_json(
        {
            "cgroup": {
                "cpuMax": {
                    "periodUs": CGROUP_CPU_PERIOD_US,
                    "quotaUs": CGROUP_CPU_QUOTA_US,
                },
                "failClosed": True,
                "ioMax": {
                    "rbps": CGROUP_IO_BYTES_PER_SECOND,
                    "riops": CGROUP_IO_OPERATIONS_PER_SECOND,
                    "scope": "rootfs-block-device",
                    "wbps": CGROUP_IO_BYTES_PER_SECOND,
                    "wiops": CGROUP_IO_OPERATIONS_PER_SECOND,
                },
                "memoryMaxBytes": CGROUP_MEMORY_MAX,
                "memorySwapMaxBytes": CGROUP_MEMORY_SWAP_MAX,
                "pidsMax": CGROUP_PIDS_MAX,
                "version": 2,
            },
            "format": "ziv-installer-sandbox-v1",
            "filesystem": {
                "allowedPublishedXattrNamespaces": ["user"],
                "minimumFreeBytes": MIN_INSTALL_FREE_BYTES,
                "minimumFreeInodes": MIN_INSTALL_FREE_INODES,
                "maximumInstallerInodeDelta": MAX_INSTALL_INODE_DELTA,
                "reserveBytes": INSTALL_FREE_RESERVE_BYTES,
                "reserveInodes": INSTALL_FREE_RESERVE_INODES,
            },
            "rlimits": {
                "coreBytes": 0,
                "fileBytes": INSTALLER_FILE_SIZE_LIMIT,
                "openFiles": INSTALLER_NOFILE_LIMIT,
            },
            "seccomp": {
                "architecture": "AUDIT_ARCH_X86_64",
                "default": "allow",
                "deny": [
                    "add_key",
                    "bpf",
                    "clone-namespace-flags",
                    "clone3",
                    "io_uring_enter",
                    "io_uring_register",
                    "io_uring_setup",
                    "keyctl",
                    "non-AF_UNIX-socket",
                    "perf_event_open",
                    "request_key",
                    "setns",
                    "unshare",
                    "userfaultfd",
                    "x32-syscalls",
                ],
                "mode": "filter",
            },
        }
    )


def _validate_cgroup_mount() -> tuple[os.stat_result, int]:
    mountinfo = _read_pseudo_file(
        Path("/proc/self/mountinfo"),
        maximum=4 * 1024 * 1024,
        label="process mount inventory",
    )
    matches = []
    for raw_line in mountinfo.splitlines():
        fields = raw_line.decode("utf-8", "strict").split()
        if "-" not in fields:
            _integrity("process mount inventory contains a malformed entry")
        separator = fields.index("-")
        if len(fields) <= separator + 1 or len(fields) < 6:
            _integrity("process mount inventory contains a short entry")
        if fields[4] == CGROUP_ROOT.as_posix():
            matches.append((fields[5], fields[separator + 1]))
    if len(matches) != 1 or matches[0][1] != "cgroup2":
        _integrity("/sys/fs/cgroup must be one unambiguous cgroup2 mount")
    options, filesystem = matches[0]
    if filesystem != "cgroup2" or "rw" not in options.split(","):
        _integrity("/sys/fs/cgroup must be a writable cgroup2 mount")
    root_info = _require_real_directory(CGROUP_ROOT, "cgroup v2 root")
    if root_info.st_uid != 0 or stat.S_IMODE(root_info.st_mode) & 0o022:
        _integrity("cgroup v2 root must not be writable by group or other")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        root_fd = os.open(CGROUP_ROOT, flags)
    except OSError as error:
        _integrity(f"cannot open cgroup v2 root: {error}")
    try:
        opened = os.fstat(root_fd)
        if (opened.st_dev, opened.st_ino) != (root_info.st_dev, root_info.st_ino):
            _integrity("cgroup v2 root changed while opening")
        for name in ("cgroup.controllers", "cgroup.subtree_control"):
            values = set(_read_cgroup_file(root_fd, name).decode("ascii").split())
            if not CGROUP_REQUIRED_CONTROLLERS <= values:
                _integrity(f"cgroup v2 {name} omits a required controller")
    except BaseException:
        os.close(root_fd)
        raise
    return root_info, root_fd


def _prepare_installer_cgroup(rootfs: Path) -> CgroupHandle:
    filesystem = os.statvfs(rootfs)
    free_bytes = filesystem.f_bavail * filesystem.f_frsize
    if free_bytes < MIN_INSTALL_FREE_BYTES:
        _integrity(
            f"installer filesystem has only {free_bytes} free bytes; "
            f"{MIN_INSTALL_FREE_BYTES} required"
        )
    if filesystem.f_favail < MIN_INSTALL_FREE_INODES:
        _integrity(
            f"installer filesystem has only {filesystem.f_favail} free inodes; "
            f"{MIN_INSTALL_FREE_INODES} required"
        )
    root_info, root_fd = _validate_cgroup_mount()
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    name = f".zivplayer-apt-{secrets.token_hex(16)}"
    child_fd = -1
    procs_fd = -1
    try:
        os.mkdir(name, mode=0o700, dir_fd=root_fd)
        child_fd = os.open(name, directory_flags, dir_fd=root_fd)
        os.fchmod(child_fd, 0o700)
        os.fchown(child_fd, 0, 0)
        child_info = os.fstat(child_fd)
        current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(child_info.st_mode)
            or stat.S_IMODE(child_info.st_mode) != 0o700
            or (child_info.st_uid, child_info.st_gid) != (0, 0)
            or (current.st_dev, current.st_ino)
            != (child_info.st_dev, child_info.st_ino)
            or (root_info.st_dev, root_info.st_ino)
            != (os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino)
        ):
            _integrity("installer cgroup metadata is not canonical")
        if _read_cgroup_file(child_fd, "cgroup.type").strip() != b"domain":
            _integrity("installer cgroup is not a domain cgroup")
        device = f"{os.major(os.stat(rootfs).st_dev)}:{os.minor(os.stat(rootfs).st_dev)}"
        if not (Path("/sys/dev/block") / device).exists():
            _integrity("rootfs block device cannot be mapped for cgroup I/O limits")
        controls = _expected_cgroup_controls(device)
        for control, payload in controls.items():
            _write_cgroup_file(child_fd, control, payload)
            if _read_cgroup_file(child_fd, control).strip() != payload.strip():
                _integrity(f"cgroup control {control} did not retain the locked limit")
        kill_descriptor = -1
        try:
            kill_flags = (
                os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            kill_descriptor = os.open("cgroup.kill", kill_flags, dir_fd=child_fd)
            kill_info = os.fstat(kill_descriptor)
            if (
                not stat.S_ISREG(kill_info.st_mode)
                or (kill_info.st_uid, kill_info.st_gid) != (0, 0)
            ):
                _integrity("cgroup.kill is not a root-owned writable control")
        finally:
            if kill_descriptor >= 0:
                os.close(kill_descriptor)
        if not _cgroup_is_empty(
            CgroupHandle(
                root_fd,
                child_fd,
                name,
                (child_info.st_dev, child_info.st_ino),
                -1,
                {},
                {},
                {},
                device,
            )
        ):
            _integrity("new installer cgroup is unexpectedly populated")
        procs_flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        procs_fd = os.open("cgroup.procs", procs_flags, dir_fd=child_fd)
        handle = CgroupHandle(
            root_fd=root_fd,
            child_fd=child_fd,
            name=name,
            child_identity=(child_info.st_dev, child_info.st_ino),
            procs_fd=procs_fd,
            baseline_pids_events=_parse_counter_file(
                _read_cgroup_file(child_fd, "pids.events"),
                "pids.events",
            ),
            baseline_memory_events=_parse_counter_file(
                _read_cgroup_file(child_fd, "memory.events"),
                "memory.events",
            ),
            baseline_swap_events=_parse_counter_file(
                _read_cgroup_file(child_fd, "memory.swap.events"),
                "memory.swap.events",
            ),
            device=device,
        )
        return handle
    except BaseException:
        if procs_fd >= 0:
            os.close(procs_fd)
        if child_fd >= 0:
            os.close(child_fd)
        try:
            os.rmdir(name, dir_fd=root_fd)
        except OSError:
            pass
        os.close(root_fd)
        raise


def _wait_cgroup_empty(handle: CgroupHandle, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _cgroup_is_empty(handle):
            return True
        time.sleep(0.05)
    return _cgroup_is_empty(handle)


def _remove_installer_cgroup(handle: CgroupHandle) -> None:
    if handle.removed:
        return
    if handle.procs_fd >= 0:
        os.close(handle.procs_fd)
        handle.procs_fd = -1
    if not _cgroup_is_empty(handle):
        raise InstallerIsolationError(
            "installer cgroup is still populated during cleanup",
            retain_staging=True,
        )
    current = os.stat(handle.name, dir_fd=handle.root_fd, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != handle.child_identity:
        raise InstallerIsolationError(
            "installer cgroup identity changed before cleanup",
            retain_staging=False,
        )
    os.close(handle.child_fd)
    handle.child_fd = -1
    try:
        os.rmdir(handle.name, dir_fd=handle.root_fd)
    except OSError as error:
        raise InstallerIsolationError(
            f"cannot remove empty installer cgroup: {error}",
            retain_staging=False,
        ) from error
    finally:
        os.close(handle.root_fd)
        handle.root_fd = -1
    handle.removed = True


def _kill_installer_cgroup(handle: CgroupHandle) -> None:
    try:
        _write_cgroup_file(handle.child_fd, "cgroup.kill", b"1\n")
    except source_tool.SourceToolError as error:
        raise InstallerIsolationError(
            f"cannot kill installer cgroup: {error}",
            retain_staging=True,
        ) from error
    if not _wait_cgroup_empty(handle, CGROUP_DRAIN_TIMEOUT_SECONDS):
        raise InstallerIsolationError(
            "installer cgroup remained populated after cgroup.kill",
            retain_staging=True,
        )


def _terminate_installer(
    process: subprocess.Popen[bytes] | None,
    cgroup: CgroupHandle,
) -> None:
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    _kill_installer_cgroup(cgroup)
    if process is not None:
        try:
            process.wait(timeout=CGROUP_DRAIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise InstallerIsolationError(
                "offline installer leader survived cgroup.kill",
                retain_staging=True,
            ) from error
    _remove_installer_cgroup(cgroup)


def _set_parent_death_signal(
    expected_parent: int,
    inherited_signal_mask: set[signal.Signals],
    cgroup_procs_fd: int,
) -> None:
    """Make the isolated process group die if the authoritative Python parent dies."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    if os.getppid() != expected_parent:
        os.kill(os.getpid(), signal.SIGKILL)
    try:
        payload = f"{os.getpid()}\n".encode("ascii")
        if os.write(cgroup_procs_fd, payload) != len(payload):
            raise OSError("short cgroup.procs write")
    finally:
        os.close(cgroup_procs_fd)
    if resource is None:
        raise OSError("Python resource limits are unavailable")
    resource.setrlimit(resource.RLIMIT_NOFILE, (INSTALLER_NOFILE_LIMIT,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (INSTALLER_FILE_SIZE_LIMIT,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.pthread_sigmask(signal.SIG_SETMASK, inherited_signal_mask)


_TZDATA_LOCAL_TIME = re.compile(
    rb"^Local time is now:[ ]+[A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] "
    rb"[0-9]{2}:[0-9]{2}:[0-9]{2} UTC [0-9]{4}\.$",
    re.MULTILINE,
)
_TZDATA_UNIVERSAL_TIME = re.compile(
    rb"^Universal Time is now:[ ]+[A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] "
    rb"[0-9]{2}:[0-9]{2}:[0-9]{2} UTC [0-9]{4}\.$",
    re.MULTILINE,
)


def _canonicalize_installer_stderr(raw: bytes) -> bytes:
    local_count = len(_TZDATA_LOCAL_TIME.findall(raw))
    universal_count = len(_TZDATA_UNIVERSAL_TIME.findall(raw))
    if (local_count, universal_count) not in {(0, 0), (1, 1)}:
        _integrity("offline installer emitted an ambiguous tzdata clock transcript")
    if local_count == 0:
        return raw
    normalized = _TZDATA_LOCAL_TIME.sub(
        b"Local time is now:      Sat Jan  1 00:00:00 UTC 2000.",
        raw,
    )
    return _TZDATA_UNIVERSAL_TIME.sub(
        b"Universal Time is now:  Sat Jan  1 00:00:00 UTC 2000.",
        normalized,
    )


def _run_installer(
    rootfs: Path,
    wrapper: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
) -> tuple[bytes, bytes, bytes]:
    if resource is None:
        _schema("offline installer resource limits require Linux resource support")
    for limit, required, label in (
        (resource.RLIMIT_NOFILE, INSTALLER_NOFILE_LIMIT, "open-file"),
        (resource.RLIMIT_FSIZE, INSTALLER_FILE_SIZE_LIMIT, "file-size"),
    ):
        _soft, hard = resource.getrlimit(limit)
        if hard != resource.RLIM_INFINITY and hard < required:
            _integrity(f"host hard {label} limit is below the locked installer limit")
    expected_parent = os.getpid()
    blocked_signals = {
        signal.SIGINT,
        signal.SIGTERM,
        getattr(signal, "SIGHUP", signal.SIGTERM),
    }
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
    cgroup: CgroupHandle | None = None
    process: subprocess.Popen[bytes] | None = None
    streams: dict[BinaryIO, bytearray] = {}
    selector: selectors.BaseSelector | None = None
    failure: BaseException | None = None
    return_code = -1
    initial_free_inodes = -1
    try:
        cgroup = _prepare_installer_cgroup(rootfs)
        selector = selectors.DefaultSelector()
        initial_free_inodes = os.statvfs(rootfs).f_favail
        process = subprocess.Popen(
            ["/bin/sh", str(wrapper), "--rootfs", str(rootfs)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C",
                "TZ": "UTC",
                "SOURCE_DATE_EPOCH": "946684800",
            },
            close_fds=True,
            pass_fds=(cgroup.procs_fd,),
            start_new_session=True,
            preexec_fn=lambda: _set_parent_death_signal(
                expected_parent,
                previous_mask,
                cgroup.procs_fd,
            ),
        )
        assert process.stdout is not None and process.stderr is not None
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        os.close(cgroup.procs_fd)
        cgroup.procs_fd = -1
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = source_tool.SourceToolError(
                    f"offline APT installation exceeded {timeout_seconds} seconds",
                    source_tool.EXIT_INTEGRITY,
                )
                break
            if selector.get_map():
                selected = selector.select(timeout=min(0.25, remaining))
            else:
                time.sleep(min(0.25, remaining))
                selected = []
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
                buffer = streams[stream]
                if len(buffer) + len(chunk) > MAX_LOG_BYTES:
                    failure = source_tool.SourceToolError(
                        "offline APT installer exceeded the bounded transcript size",
                        source_tool.EXIT_INTEGRITY,
                    )
                    break
                buffer.extend(chunk)
            if failure is not None:
                break
            violation = _cgroup_limit_violation(cgroup)
            if violation is not None:
                failure = InstallerIsolationError(
                    violation,
                    retain_staging=False,
                )
                break
            filesystem = os.statvfs(rootfs)
            free_bytes = filesystem.f_bavail * filesystem.f_frsize
            if free_bytes < INSTALL_FREE_RESERVE_BYTES:
                failure = InstallerIsolationError(
                    "offline installer consumed the reserved filesystem capacity",
                    retain_staging=False,
                )
                break
            if (
                filesystem.f_favail < INSTALL_FREE_RESERVE_INODES
                or initial_free_inodes - filesystem.f_favail > MAX_INSTALL_INODE_DELTA
            ):
                failure = InstallerIsolationError(
                    "offline installer exceeded the reserved inode capacity",
                    retain_staging=False,
                )
                break
        if failure is None:
            return_code = process.wait(timeout=10)
            violation = _cgroup_limit_violation(cgroup)
            if violation is not None:
                failure = InstallerIsolationError(
                    violation,
                    retain_staging=False,
                )
            elif not _cgroup_is_empty(cgroup):
                failure = InstallerIsolationError(
                    "offline installer left descendant processes running",
                    retain_staging=False,
                )
    except BaseException as error:
        failure = error
        if process is not None and process.returncode is not None:
            return_code = process.returncode
    finally:
        if cgroup is not None and cgroup.procs_fd >= 0:
            os.close(cgroup.procs_fd)
            cgroup.procs_fd = -1
        cleanup_mask: set[signal.Signals] | None = None
        try:
            try:
                cleanup_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
            except BaseException as mask_error:
                failure = mask_error
            if cgroup is not None and not cgroup.removed:
                try:
                    if failure is not None or not _cgroup_is_empty(cgroup):
                        _terminate_installer(process, cgroup)
                    else:
                        _remove_installer_cgroup(cgroup)
                except BaseException as cleanup_error:
                    failure = cleanup_error
        finally:
            if cleanup_mask is not None:
                try:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                except KeyboardInterrupt as signal_error:
                    failure = signal_error
        if selector is not None:
            selector.close()
        for stream in streams:
            if not stream.closed:
                stream.close()
    if process is None or process.stdout is None or process.stderr is None:
        assert failure is not None
        raise failure
    stdout = bytes(streams[process.stdout])
    stderr = bytes(streams[process.stderr])
    if failure is None and return_code == 0:
        stderr = _canonicalize_installer_stderr(stderr)
    _write_exclusive(stdout_path, stdout, 0o600)
    _write_exclusive(stderr_path, stderr, 0o600)
    if failure is not None:
        raise failure
    _assert_no_nested_mounts(rootfs)
    recorded_stdout = _read_stable(
        stdout_path,
        maximum=MAX_LOG_BYTES,
        label="offline installer stdout",
    )
    recorded_stderr = _read_stable(
        stderr_path,
        maximum=MAX_LOG_BYTES,
        label="offline installer stderr",
    )
    if recorded_stdout != stdout or recorded_stderr != stderr:
        _integrity("offline installer transcript changed after bounded capture")
    if return_code != 0:
        tail = stderr[-4096:].decode("utf-8", "replace").strip()
        _integrity(
            f"offline APT installer exited {return_code}; stderr tail: {tail}"
        )
    return stdout, stderr, _sandbox_policy_record()


def _expected_base_receipt(state: LockedState, cache: Path) -> dict[str, object]:
    layer = next(
        entry
        for entry in state.oci_objects
        if entry["id"] == "ubuntu-layer-amd64"
    )
    entries = rootfs_tool._plan_layer(layer, cache)
    return rootfs_tool._receipt_data(
        state.data,
        layer,
        state.manifest_sha256,
        entries,
    )


def _read_json_bytes(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=toolchain_tool._json_without_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        _integrity(f"cannot decode {label}: {error}")
    if not isinstance(value, dict):
        _integrity(f"{label} must be a JSON object")
    if _canonical_json(value) != raw:
        _integrity(f"{label} is not canonical JSON")
    return value


def _jks_integrity(body: bytes) -> bytes:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(JKS_PASSWORD.encode("utf-16-be"))
    digest.update(JKS_INTEGRITY_PHRASE)
    digest.update(body)
    return digest.digest()


def _jks_generated_file_record(
    raw: bytes,
    *,
    normalize: bool,
) -> tuple[bytes, dict[str, object]]:
    if len(raw) < 12 + JKS_INTEGRITY_BYTES or len(raw) > MAX_JKS_BYTES:
        _integrity("Java cacerts JKS has an invalid size")
    body_end = len(raw) - JKS_INTEGRITY_BYTES
    body = bytearray(raw[:body_end])
    stored_integrity = raw[body_end:]
    if _jks_integrity(bytes(body)) != stored_integrity:
        _integrity("Java cacerts JKS integrity check failed for the locked password")

    cursor = 0

    def take(size: int, label: str) -> bytes:
        nonlocal cursor
        if size < 0 or cursor + size > body_end:
            _integrity(f"Java cacerts JKS has a truncated {label}")
        value = bytes(body[cursor : cursor + size])
        cursor += size
        return value

    def take_i32(label: str) -> int:
        return struct.unpack(">i", take(4, label))[0]

    def take_utf(label: str) -> bytes:
        length = struct.unpack(">H", take(2, f"{label} length"))[0]
        value = take(length, label)
        if not value:
            _integrity(f"Java cacerts JKS has an empty {label}")
        return value

    magic = struct.unpack(">I", take(4, "magic"))[0]
    version = take_i32("version")
    entry_count = take_i32("entry count")
    if magic != JKS_MAGIC or version != JKS_VERSION:
        _integrity("Java cacerts must be a version-2 JKS key store")
    if entry_count <= 0 or entry_count > 4096:
        _integrity("Java cacerts JKS has an invalid entry count")

    aliases: set[bytes] = set()
    alias_digest = hashlib.sha256()
    certificate_digest = hashlib.sha256()
    timestamp_offsets: list[int] = []
    timestamps: list[int] = []
    for index in range(entry_count):
        tag = take_i32(f"entry {index} tag")
        if tag != JKS_TRUSTED_CERTIFICATE:
            _integrity("Java cacerts JKS contains a non-certificate entry")
        alias = take_utf(f"entry {index} alias")
        if alias in aliases:
            _integrity("Java cacerts JKS repeats an alias")
        aliases.add(alias)
        alias_digest.update(struct.pack(">I", len(alias)))
        alias_digest.update(alias)
        timestamp_offsets.append(cursor)
        timestamp = struct.unpack(">q", take(8, f"entry {index} timestamp"))[0]
        timestamps.append(timestamp)
        certificate_type = take_utf(f"entry {index} certificate type")
        if certificate_type != b"X.509":
            _integrity("Java cacerts JKS contains a non-X.509 certificate")
        certificate_size = take_i32(f"entry {index} certificate size")
        if certificate_size <= 0:
            _integrity("Java cacerts JKS contains an empty certificate")
        certificate = take(certificate_size, f"entry {index} certificate")
        certificate_digest.update(struct.pack(">I", certificate_size))
        certificate_digest.update(certificate)
    if cursor != body_end:
        _integrity("Java cacerts JKS has trailing data before its integrity digest")

    if normalize:
        for offset in timestamp_offsets:
            struct.pack_into(">q", body, offset, NORMALIZED_TIMESTAMP_MILLIS)
        normalized_body = bytes(body)
        result = normalized_body + _jks_integrity(normalized_body)
    else:
        if any(value != NORMALIZED_TIMESTAMP_MILLIS for value in timestamps):
            _integrity("Java cacerts JKS entry timestamps are not normalized")
        result = raw
    return result, {
        "action": "normalize-jks-entry-timestamps",
        "aliasesSha256": alias_digest.hexdigest(),
        "certificatePayloadSha256": certificate_digest.hexdigest(),
        "entries": entry_count,
        "format": "JKS-v2",
        "path": CACERTS_RELATIVE.as_posix(),
        "sha256": _sha256(result),
        "size": len(result),
        "timestampMillis": NORMALIZED_TIMESTAMP_MILLIS,
    }


def _rewrite_cacerts(rootfs: Path) -> dict[str, object]:
    parent_descriptor, leaf = _open_real_parent_beneath(
        rootfs,
        CACERTS_RELATIVE,
        "Java cacerts JKS",
    )
    descriptor = -1
    normalized = b""
    record: dict[str, object] = {}
    try:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(leaf, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o644
            or (opened.st_uid, opened.st_gid) != (0, 0)
            or opened.st_size > MAX_JKS_BYTES
        ):
            _integrity("Java cacerts is not a canonical root-owned regular file")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                _integrity("Java cacerts changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        after_read = os.fstat(descriptor)
        if (
            after_read.st_size != opened.st_size
            or after_read.st_mtime_ns != opened.st_mtime_ns
            or after_read.st_ctime_ns != opened.st_ctime_ns
        ):
            _integrity("Java cacerts changed while reading")
        normalized, record = _jks_generated_file_record(
            b"".join(chunks),
            normalize=True,
        )
        if len(normalized) != opened.st_size:
            _integrity("Java cacerts normalization changed the file size")
        os.lseek(descriptor, 0, os.SEEK_SET)
        _write_all(descriptor, normalized)
        os.fsync(descriptor)
        after_write = os.fstat(descriptor)
        current = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            after_write.st_size != opened.st_size
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _integrity("Java cacerts size changed during normalization")
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot normalize Java cacerts JKS: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
    verified, _verified_info = _read_stable_beneath(
        rootfs,
        CACERTS_RELATIVE,
        maximum=MAX_JKS_BYTES,
        label="normalized Java cacerts JKS",
    )
    if verified != normalized:
        _integrity("Java cacerts differs after normalization")
    checked, checked_record = _jks_generated_file_record(verified, normalize=False)
    if checked != normalized or checked_record != record:
        _integrity("Java cacerts normalization did not verify")
    return record


def _verify_cacerts(rootfs: Path) -> dict[str, object]:
    raw, info = _read_stable_beneath(
        rootfs,
        CACERTS_RELATIVE,
        maximum=MAX_JKS_BYTES,
        label="normalized Java cacerts JKS",
    )
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o644
        or (info.st_uid, info.st_gid) != (0, 0)
    ):
        _integrity("normalized Java cacerts is not a canonical regular file")
    _same, record = _jks_generated_file_record(raw, normalize=False)
    return record


def _remove_ldconfig_aux_cache(rootfs: Path) -> None:
    parent_descriptor, leaf = _open_real_parent_beneath(
        rootfs,
        LDCONFIG_AUX_CACHE_RELATIVE,
        "ldconfig auxiliary cache",
    )
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(leaf, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_uid, opened.st_gid) != (0, 0)
        ):
            _integrity("ldconfig auxiliary cache is not a canonical regular file")
        current = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            _integrity("ldconfig auxiliary cache changed before removal")
        os.unlink(leaf, dir_fd=parent_descriptor)
        try:
            os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _integrity("ldconfig auxiliary cache remains after normalization")
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot remove ldconfig auxiliary cache: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _generated_files_record(rootfs: Path, *, normalize: bool) -> bytes:
    if normalize:
        cacerts = _rewrite_cacerts(rootfs)
        _remove_ldconfig_aux_cache(rootfs)
    else:
        cacerts = _verify_cacerts(rootfs)
        _assert_absent_beneath(
            rootfs,
            LDCONFIG_AUX_CACHE_RELATIVE,
            "nondeterministic ldconfig auxiliary cache",
        )
    return _canonical_json(
        {
            "files": [
                cacerts,
                {
                    "action": "remove-runtime-cache",
                    "path": LDCONFIG_AUX_CACHE_RELATIVE.as_posix(),
                },
            ],
            "format": "ziv-generated-files-v1",
        }
    )


def _read_status_projection(rootfs: Path, state: LockedState) -> bytes:
    status, _status_info = _read_stable_beneath(
        rootfs,
        PurePosixPath("var/lib/dpkg/status"),
        maximum=MAX_STATUS_BYTES,
        label="installed dpkg status",
    )
    projection = _installed_projection(status, state.base_rows, state.package_rows)
    updates = rootfs / "var" / "lib" / "dpkg" / "updates"
    _require_real_directory(updates, "dpkg updates directory")
    try:
        if any(os.scandir(updates)):
            _integrity("installed dpkg database retains pending updates")
    except OSError as error:
        _integrity(f"cannot enumerate dpkg updates directory: {error}")
    unincorporated_relative = PurePosixPath("var/lib/dpkg/triggers/Unincorp")
    parent_descriptor, leaf = _open_real_parent_beneath(
        rootfs,
        unincorporated_relative,
        "dpkg unincorporated triggers",
    )
    try:
        try:
            os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            has_unincorporated = False
        else:
            has_unincorporated = True
    finally:
        os.close(parent_descriptor)
    if has_unincorporated:
        raw, _info = _read_stable_beneath(
            rootfs,
            unincorporated_relative,
            maximum=1024 * 1024,
            label="dpkg unincorporated triggers",
        )
        if raw:
            _integrity("installed dpkg database retains unincorporated triggers")
    return projection


def _write_evidence(
    rootfs: Path,
    snapshots: dict[str, bytes],
    *,
    base_receipt: bytes,
    projection: bytes,
    generated_files: bytes,
    sandbox_policy: bytes,
    stdout: bytes,
    stderr: bytes,
) -> tuple[Path, dict[str, bytes]]:
    evidence_root = rootfs.joinpath(*EVIDENCE_RELATIVE.parts)
    if evidence_root.exists() or evidence_root.is_symlink():
        _integrity("installed rootfs already contains APT-stage evidence")
    _ensure_directory_chain(rootfs, EVIDENCE_RELATIVE.parent)
    _mkdir(evidence_root, 0o755)
    evidence = dict(snapshots)
    evidence.update(
        {
            "base/ziv-toolchain-base.json": base_receipt,
            "result/dpkg-projection.tsv": projection,
            "result/generated-files.json": generated_files,
            "result/sandbox-policy.json": sandbox_policy,
            "result/installer.stdout": stdout,
            "result/installer.stderr": stderr,
        }
    )
    for name in sorted(evidence, key=lambda value: value.encode("utf-8")):
        _write_relative(evidence_root, name, evidence[name], mode=0o644)
    materialize_sources._fsync_workspace_directories(evidence_root)
    return evidence_root, evidence


def _evidence_records(evidence: dict[str, bytes]) -> list[dict[str, object]]:
    return [
        {
            "path": name,
            "size": len(evidence[name]),
            "sha256": _sha256(evidence[name]),
        }
        for name in sorted(evidence, key=lambda value: value.encode("utf-8"))
    ]


def _read_evidence(
    rootfs: Path,
    snapshots: dict[str, bytes],
) -> dict[str, bytes]:
    evidence_root = rootfs.joinpath(*EVIDENCE_RELATIVE.parts)
    _require_real_directory(evidence_root, "APT-stage evidence root")
    evidence_root_info = evidence_root.lstat()
    if (
        stat.S_IMODE(evidence_root_info.st_mode) != 0o755
        or (evidence_root_info.st_uid, evidence_root_info.st_gid) != (0, 0)
        or evidence_root_info.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("APT-stage evidence root metadata is not canonical")
    expected_names = set(snapshots) | DYNAMIC_EVIDENCE_NAMES
    expected_directories: set[str] = set()
    for name in expected_names:
        parent = PurePosixPath(name).parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    actual_names: set[str] = set()
    actual_directories: set[str] = set()
    entry_limit = len(expected_names) + len(expected_directories)
    scanned_entries = 0
    pending = [evidence_root]
    while pending:
        current = pending.pop()
        try:
            iterator = os.scandir(current)
            with iterator:
                for directory_entry in iterator:
                    scanned_entries += 1
                    if scanned_entries > entry_limit:
                        _integrity("APT-stage evidence exceeds its exact entry budget")
                    _utf8_sort_key(directory_entry.name)
                    path = Path(directory_entry.path)
                    relative = PurePosixPath(
                        *path.relative_to(evidence_root).parts
                    ).as_posix()
                    info = directory_entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if (
                            stat.S_IMODE(info.st_mode) != 0o755
                            or (info.st_uid, info.st_gid) != (0, 0)
                            or info.st_mtime_ns != NORMALIZED_MTIME_NS
                        ):
                            _integrity(
                                f"APT-stage evidence directory is not canonical: {path}"
                            )
                        actual_directories.add(relative)
                        pending.append(path)
                        continue
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or stat.S_IMODE(info.st_mode) != 0o644
                        or (info.st_uid, info.st_gid) != (0, 0)
                        or info.st_mtime_ns != NORMALIZED_MTIME_NS
                    ):
                        _integrity(f"APT-stage evidence is not a regular file: {relative}")
                    actual_names.add(relative)
        except source_tool.SourceToolError:
            raise
        except OSError as error:
            _integrity(f"cannot enumerate APT-stage evidence: {error}")
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        _integrity(f"APT-stage evidence exact-set mismatch; missing={missing}, extra={extra}")
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        extra = sorted(actual_directories - expected_directories)
        _integrity(
            f"APT-stage evidence directory-set mismatch; missing={missing}, extra={extra}"
        )
    result: dict[str, bytes] = {}
    total_bytes = 0
    for name in sorted(expected_names, key=_utf8_sort_key):
        if name in snapshots:
            maximum = len(snapshots[name])
        elif name.startswith("result/installer."):
            maximum = MAX_LOG_BYTES
        elif name == "base/ziv-toolchain-base.json":
            maximum = MAX_RECEIPT_BYTES
        elif name == "result/dpkg-projection.tsv":
            maximum = MAX_STATUS_BYTES
        else:
            maximum = MAX_RECEIPT_BYTES
        result[name] = _read_stable(
            evidence_root.joinpath(*PurePosixPath(name).parts),
            maximum=maximum,
            label=f"APT-stage evidence {name}",
        )
        total_bytes += len(result[name])
        if total_bytes > MAX_EVIDENCE_TOTAL_BYTES:
            _integrity("APT-stage evidence exceeds its aggregate byte budget")
    for name, expected in snapshots.items():
        if result[name] != expected:
            _integrity(f"APT-stage evidence differs from the current lock: {name}")
    return result


def _receipt_data(
    state: LockedState,
    base_receipt: dict[str, object],
    evidence: dict[str, bytes],
    tree: dict[str, object],
) -> dict[str, object]:
    project = toolchain_tool._expect_table(state.data["project"], "project")
    policy = toolchain_tool._expect_table(state.data["policy"], "policy")
    base_image = toolchain_tool._expect_table(state.data["baseImage"], "baseImage")
    compliance = toolchain_tool._expect_table(state.data["compliance"], "compliance")
    projection = evidence["result/dpkg-projection.tsv"]
    generated_files_raw = evidence["result/generated-files.json"]
    generated_files = _read_json_bytes(
        generated_files_raw,
        "generated-files evidence",
    )
    sandbox_policy_raw = evidence["result/sandbox-policy.json"]
    sandbox_policy = _read_json_bytes(
        sandbox_policy_raw,
        "installer sandbox-policy evidence",
    )
    if sandbox_policy_raw != _sandbox_policy_record():
        _integrity("installer sandbox-policy evidence differs from the locked policy")
    package_count = max(0, projection.count(b"\n") - 1)
    helper_records = []
    for relative in (
        *SHELL_SNAPSHOT_PATHS,
        *SECCOMP_SNAPSHOT_PATHS,
        *PYTHON_SNAPSHOT_PATHS,
    ):
        name = f"helpers/{relative}"
        payload = evidence[name]
        helper_records.append(
            {"path": relative, "size": len(payload), "sha256": _sha256(payload)}
        )
    return {
        "schemaVersion": 1,
        "kind": "ziv-toolchain-apt",
        "ready": False,
        "environmentStatus": project["environmentStatus"],
        "manifestSha256": state.manifest_sha256,
        "sourceManifestSha256": project["sourceManifestSha256"],
        "platform": project["hostPlatform"],
        "base": {
            "indexDigest": base_image["indexDigest"],
            "manifestDigest": base_image["manifestDigest"],
            "receiptSha256": _sha256(evidence["base/ziv-toolchain-base.json"]),
            "tree": base_receipt["tree"],
        },
        "apt": {
            "snapshot": state.apt["snapshot"],
            "architecture": state.apt["architecture"],
            "archiveSignerFingerprint": state.apt["archiveSignerFingerprint"],
            "basePackageCount": state.apt["baseDpkgPackageCount"],
            "packageCount": state.apt["packageCount"],
            "installOrderCount": state.apt["installOrderCount"],
            "installOrderSha256": state.apt["installOrderSha256"],
            "packageLockSha256": state.apt["packageLockSha256"],
            "indexLockSha256": state.apt["indexLockSha256"],
            "simulationSha256": state.apt["simulationSha256"],
            "projection": {
                "format": PROJECTION_FORMAT,
                "packages": package_count,
                "sha256": _sha256(projection),
            },
            "transcript": {
                "stdoutSha256": _sha256(evidence["result/installer.stdout"]),
                "stderrSha256": _sha256(evidence["result/installer.stderr"]),
                "exitCode": 0,
            },
        },
        "installer": {
            "argv": [
                "/bin/sh",
                "CONTROL/native/toolchain/install-apt-rootfs.sh",
                "--rootfs",
                "ROOTFS",
            ],
            "helpers": helper_records,
        },
        "policy": {
            "networkAtBuild": policy["networkAtBuild"],
            "namespace": {
                "mount": True,
                "network": True,
                "pid": True,
                "ipc": True,
                "uts": True,
                "user": False,
                "propagation": "private",
                "visibleInterfaces": ["lo"],
            },
            "inputMount": "ro,nosuid,nodev,noexec",
            "rootMount": "rw,nosuid,nodev",
            "capabilitiesDuringPackageScripts": "none",
            "noNewPrivileges": True,
            "aptSources": "disabled",
            "filesystem": "ext4",
            "rootMode": "0700",
            "normalizedMtimeNs": NORMALIZED_MTIME_NS,
            "generatedFiles": {
                **generated_files,
                "recordSha256": _sha256(generated_files_raw),
            },
            "sandbox": {
                **sandbox_policy,
                "recordSha256": _sha256(sandbox_policy_raw),
            },
        },
        "evidence": {
            "path": EVIDENCE_RELATIVE.as_posix(),
            "files": _evidence_records(evidence),
        },
        "compliance": {
            "androidLicenseFilesStatus": compliance["androidLicenseFilesStatus"],
            "systemNoticesStatus": compliance["systemNoticesStatus"],
            "retentionBundleStatus": compliance["retentionBundleStatus"],
        },
        "tree": tree,
    }


def _preflight_tree_limits(rootfs: Path) -> None:
    root_info = rootfs.lstat()
    pending = [rootfs]
    entries = 0
    physical_bytes = 0
    path_bytes = 0
    seen_files: set[tuple[int, int]] = set()
    while pending:
        current = pending.pop()
        try:
            iterator = os.scandir(current)
            with iterator:
                for directory_entry in iterator:
                    entries += 1
                    if entries > MAX_TREE_ENTRIES:
                        _integrity("installed tree exceeds the entry-count limit")
                    _utf8_sort_key(directory_entry.name)
                    info = directory_entry.stat(follow_symlinks=False)
                    path = Path(directory_entry.path)
                    relative = _path_text(path, rootfs)
                    path_bytes += len(relative.encode("utf-8"))
                    if path_bytes > MAX_TREE_PATH_BYTES:
                        _integrity("installed tree exceeds the aggregate path-byte limit")
                    if info.st_dev != root_info.st_dev:
                        _integrity(f"installed tree crosses a filesystem boundary: {path}")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        identity = (info.st_dev, info.st_ino)
                        if identity not in seen_files:
                            if info.st_size < 0 or info.st_size > MAX_TREE_FILE_BYTES:
                                _integrity(f"installed tree contains an oversized file: {path}")
                            seen_files.add(identity)
                            physical_bytes += info.st_size
                            if physical_bytes > MAX_TREE_FILE_BYTES:
                                _integrity("installed tree exceeds the physical-file byte limit")
                    elif not stat.S_ISLNK(info.st_mode):
                        _integrity(f"installed tree contains a special entry: {path}")
        except source_tool.SourceToolError:
            raise
        except OSError as error:
            _integrity(f"cannot preflight installed tree: {error}")


def _normalize_tree(rootfs: Path) -> None:
    normalized_entries = 0
    for current_text, names, leaf_names in os.walk(
        rootfs,
        topdown=False,
        followlinks=False,
        onerror=lambda error: _integrity(f"cannot walk installed tree: {error}"),
    ):
        current = Path(current_text)
        normalized_entries += len(names) + len(leaf_names)
        if normalized_entries > MAX_TREE_ENTRIES:
            _integrity("installed tree changed beyond the entry-count limit")
        for name in sorted(names + leaf_names, key=_utf8_sort_key):
            path = current / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                try:
                    os.utime(
                        path,
                        ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
                        follow_symlinks=False,
                    )
                except OSError as error:
                    _integrity(f"cannot normalize symlink timestamp {path}: {error}")
            elif stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
                mode = stat.S_IMODE(info.st_mode)
                try:
                    os.utime(
                        path,
                        ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
                        follow_symlinks=False,
                    )
                    os.chmod(path, mode, follow_symlinks=False)
                except OSError as error:
                    _integrity(f"cannot normalize installed path {path}: {error}")
            else:
                _integrity(f"cannot publish installed special entry: {path}")
    try:
        rootfs.chmod(0o700)
        os.chown(rootfs, 0, 0)
        os.utime(
            rootfs,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
    except OSError as error:
        _integrity(f"cannot normalize installed rootfs metadata: {error}")


def _sync_filesystem(rootfs: Path) -> None:
    descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(rootfs, flags)
        before = rootfs.lstat()
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _integrity("installed rootfs changed before filesystem synchronization")
        libc = ctypes.CDLL(None, use_errno=True)
        syncfs = libc.syncfs
        syncfs.argtypes = [ctypes.c_int]
        syncfs.restype = ctypes.c_int
        if syncfs(descriptor) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
        after = rootfs.lstat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            _integrity("installed rootfs changed during filesystem synchronization")
    except source_tool.SourceToolError:
        raise
    except (AttributeError, OSError) as error:
        _integrity(f"cannot synchronize installed rootfs filesystem: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_receipt(rootfs: Path, receipt: dict[str, object]) -> None:
    path = rootfs / RECEIPT_NAME
    if path.exists() or path.is_symlink():
        _integrity("installed rootfs receipt already exists")
    _write_exclusive(path, _canonical_json(receipt), 0o644)
    os.utime(
        path,
        ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
        follow_symlinks=False,
    )
    os.utime(
        rootfs,
        ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
        follow_symlinks=False,
    )


def _read_receipt(rootfs: Path) -> tuple[dict[str, object], bytes]:
    path = rootfs / RECEIPT_NAME
    try:
        info = path.lstat()
    except OSError as error:
        raise source_tool.SourceToolError(
            f"installed rootfs receipt is missing: {error}",
            source_tool.EXIT_MISSING,
        ) from error
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o644
        or (info.st_uid, info.st_gid) != (0, 0)
        or info.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("installed rootfs receipt metadata is not canonical")
    receipt_xattrs, _receipt_xattr_bytes = _xattr_digest(path)
    if receipt_xattrs is not None:
        _integrity("installed rootfs receipt must not carry extended attributes")
    raw = _read_stable(
        path,
        maximum=MAX_RECEIPT_BYTES,
        label="installed rootfs receipt",
    )
    return _read_json_bytes(raw, "installed rootfs receipt"), raw


def _trusted_output_parent_identity(parent: Path) -> tuple[int, int]:
    info = parent.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid != 0 or (mode & 0o022 and not mode & stat.S_ISVTX):
        _integrity(
            "installed rootfs output parent must be root-owned and either private or sticky"
        )
    return info.st_dev, info.st_ino


def verify_apt_environment(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    rootfs: Path,
) -> None:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        _schema("installed rootfs verification requires Linux root")
    parent = materialize_sources._require_real_workspace_parent(rootfs, create=False)
    _trusted_output_parent_identity(parent)
    rootfs_path = parent / rootfs.name
    try:
        rootfs_path.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"installed rootfs is missing: {rootfs_path}",
            source_tool.EXIT_MISSING,
        ) from error
    except OSError as error:
        _integrity(f"cannot inspect installed rootfs: {error}")
    _require_real_directory(rootfs_path, "installed rootfs")
    rootfs_tool._require_ext4(rootfs_path)
    _assert_no_nested_mounts(rootfs_path)
    state = _load_locked_state(manifest, source_manifest, cache)
    cache_root = cache.resolve(strict=True)
    if (
        rootfs_path == cache_root
        or cache_root in rootfs_path.parents
        or rootfs_path in cache_root.parents
    ):
        _integrity("installed rootfs must stay outside the toolchain cache")
    _assert_apt_sources_disabled(rootfs_path)
    for forbidden in (
        rootfs_path / INPUT_NAME,
        rootfs_path / BASE_RECEIPT_NAME,
        rootfs_path / ".ziv-old-root",
    ):
        if forbidden.exists() or forbidden.is_symlink():
            _integrity(f"installed rootfs retains transient path: {forbidden}")
    _assert_policy_rc(rootfs_path)
    projection = _read_status_projection(rootfs_path, state)
    generated_files = _generated_files_record(rootfs_path, normalize=False)
    sandbox_policy = _sandbox_policy_record()
    evidence = _read_evidence(rootfs_path, state.snapshots)
    if evidence["result/dpkg-projection.tsv"] != projection:
        _integrity("APT-stage evidence projection differs from installed dpkg status")
    if evidence["result/generated-files.json"] != generated_files:
        _integrity("generated-files evidence differs from the installed rootfs")
    if evidence["result/sandbox-policy.json"] != sandbox_policy:
        _integrity("sandbox-policy evidence differs from the locked installer policy")
    expected_base = _expected_base_receipt(state, cache)
    expected_base_raw = _canonical_json(expected_base)
    if evidence["base/ziv-toolchain-base.json"] != expected_base_raw:
        _integrity("APT-stage base receipt differs from the locked OCI rootfs")
    tree = _scan_tree(rootfs_path)
    actual_receipt, actual_receipt_raw = _read_receipt(rootfs_path)
    expected_receipt = _receipt_data(state, expected_base, evidence, tree)
    if (
        actual_receipt != expected_receipt
        or actual_receipt_raw != _canonical_json(expected_receipt)
    ):
        _integrity("installed rootfs receipt does not match its locks or tree")
    print(
        f"verified offline APT rootfs: {rootfs_path} "
        f"({tree['entries']} entries, {tree['fileBytes']} file bytes)"
    )


def materialize_apt_environment(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    output: Path,
    *,
    timeout_seconds: int = DEFAULT_INSTALL_TIMEOUT_SECONDS,
) -> None:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        _schema("installed rootfs materialization requires Linux root")
    if timeout_seconds < 60 or timeout_seconds > 7200:
        _schema("offline installer timeout must be between 60 and 7200 seconds")
    parent = materialize_sources._require_real_workspace_parent(output, create=False)
    rootfs_tool._require_ext4(parent)
    output_path = parent / output.name
    state = _load_locked_state(manifest, source_manifest, cache)
    cache_root = cache.resolve(strict=True)
    if (
        output_path == cache_root
        or cache_root in output_path.parents
        or output_path in cache_root.parents
    ):
        _integrity("installed rootfs output must stay outside the toolchain cache")
    parent_identity = _trusted_output_parent_identity(parent)
    if output_path.exists() or output_path.is_symlink():
        _integrity(f"refusing to replace existing installed rootfs: {output_path}")
    outer: Path | None = None
    outer_identity: tuple[int, int] | None = None
    published = False
    parent_durable = False
    handled_signals = tuple(
        value
        for value in (signal.SIGTERM, getattr(signal, "SIGHUP", None))
        if value is not None
    )
    previous_handlers = {value: signal.getsignal(value) for value in handled_signals}

    def interrupt_materialization(_number: int, _frame: object) -> NoReturn:
        raise KeyboardInterrupt

    protected_signals = {
        signal.SIGINT,
        *handled_signals,
    }
    try:
        for value in handled_signals:
            signal.signal(value, interrupt_materialization)
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, protected_signals)
        try:
            outer = Path(
                tempfile.mkdtemp(
                    prefix=f".{output.name}.",
                    suffix=".part",
                    dir=parent,
                )
            )
            outer_info = outer.lstat()
            outer_identity = (outer_info.st_dev, outer_info.st_ino)
            outer.chmod(0o700)
            os.chown(outer, 0, 0)
            current_outer = outer.lstat()
            if (current_outer.st_dev, current_outer.st_ino) != outer_identity:
                _integrity("private installer staging changed during initialization")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        assert outer is not None and outer_identity is not None
        current_parent = parent.lstat()
        if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
            _integrity("installed rootfs output parent changed while staging")
        rootfs_tool._require_ext4(outer)
        control, _control_identity = _prepare_control(outer, state.snapshots)
        working = outer / "rootfs"
        rootfs_tool.materialize_base(
            manifest,
            source_manifest,
            cache,
            working,
        )
        rootfs_tool.verify_base(
            manifest,
            source_manifest,
            cache,
            working,
        )
        _assert_no_nested_mounts(working)
        base_receipt, base_receipt_raw = rootfs_tool._read_receipt(
            working / BASE_RECEIPT_NAME
        )
        _disable_apt_sources(working)
        input_root, input_identity = _prepare_apt_input(working, state, cache)
        stdout_path = outer / "installer.stdout"
        stderr_path = outer / "installer.stderr"
        stdout, stderr, sandbox_policy = _run_installer(
            working,
            control / "native" / "toolchain" / "install-apt-rootfs.sh",
            stdout_path,
            stderr_path,
            timeout_seconds,
        )
        _assert_no_nested_mounts(working)
        projection = _read_status_projection(working, state)
        generated_files = _generated_files_record(working, normalize=True)
        state_after = _load_locked_state(manifest, source_manifest, cache)
        if (
            state_after.manifest_sha256 != state.manifest_sha256
            or state_after.data != state.data
            or state_after.package_rows != state.package_rows
            or state_after.install_order != state.install_order
            or state_after.snapshots != state.snapshots
        ):
            _integrity("locked toolchain inputs changed during APT materialization")
        cleanup_error = materialize_sources._remove_tree(input_root, input_identity)
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                f"cannot remove transient APT input: {cleanup_error}",
                source_tool.EXIT_INTERNAL,
            )
        for transient in (
            working / ".ziv-old-root",
        ):
            if transient.exists() or transient.is_symlink():
                _integrity(f"offline installer retained transient path: {transient}")
        _assert_policy_rc(working)
        _assert_apt_sources_disabled(working)
        actual_base, actual_base_raw = rootfs_tool._read_receipt(
            working / BASE_RECEIPT_NAME
        )
        if actual_base != base_receipt or actual_base_raw != base_receipt_raw:
            _integrity("base rootfs receipt changed during offline installation")
        _evidence_root, evidence = _write_evidence(
            working,
            state.snapshots,
            base_receipt=base_receipt_raw,
            projection=projection,
            generated_files=generated_files,
            sandbox_policy=sandbox_policy,
            stdout=stdout,
            stderr=stderr,
        )
        try:
            (working / BASE_RECEIPT_NAME).unlink()
        except OSError as error:
            _integrity(f"cannot retire transient base receipt: {error}")
        _preflight_tree_limits(working)
        _normalize_tree(working)
        tree = _scan_tree(working)
        receipt = _receipt_data(state, base_receipt, evidence, tree)
        _write_receipt(working, receipt)
        verify_apt_environment(
            manifest,
            source_manifest,
            cache,
            working,
        )
        _sync_filesystem(working)
        materialize_sources._fsync_workspace_directories(working)
        current_parent = parent.lstat()
        if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
            _integrity("installed rootfs output parent changed before publication")
        cleanup_error = None
        post_publish_error: BaseException | None = None
        parent_sync_error: BaseException | None = None
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, protected_signals)
        try:
            materialize_sources._rename_no_replace(working, output_path)
            published = True
            try:
                cleanup_error = materialize_sources._remove_tree(outer, outer_identity)
                if cleanup_error is None:
                    outer = None
            except BaseException as error:
                post_publish_error = error
            try:
                materialize_sources._fsync_directory(parent)
                parent_durable = True
            except BaseException as error:
                parent_sync_error = error
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        if parent_sync_error is not None:
            raise source_tool.SourceToolError(
                f"installed rootfs was published at {output_path}, but output-parent "
                "durability could not be confirmed; verify it before any retry: "
                f"{parent_sync_error}",
                source_tool.EXIT_INTEGRITY,
            ) from parent_sync_error
        if post_publish_error is not None:
            raise source_tool.SourceToolError(
                f"installed rootfs was durably published at {output_path}, but private "
                f"control staging at {outer} could not be cleaned: {post_publish_error}",
                source_tool.EXIT_INTEGRITY,
            ) from post_publish_error
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                f"installed rootfs was durably published at {output_path}, but private control "
                f"staging was retained at {outer} because cleanup failed: {cleanup_error}",
                source_tool.EXIT_INTEGRITY,
            )
        print(f"materialized offline APT rootfs: {output_path}")
    except BaseException as error:
        cleanup_error: str | None = None
        cleanup_mask = signal.pthread_sigmask(signal.SIG_BLOCK, protected_signals)
        try:
            if outer is not None and outer_identity is not None:
                if (
                    isinstance(error, InstallerIsolationError)
                    and error.retain_staging
                ):
                    cleanup_error = (
                        "installer isolation could not prove all descendants stopped; "
                        "refusing recursive cleanup"
                    )
                else:
                    try:
                        mounts = _mounts_below(outer)
                    except source_tool.SourceToolError as mount_error:
                        mounts = b"unknown"
                        cleanup_error = str(mount_error)
                    if mounts:
                        cleanup_error = (
                            cleanup_error
                            or "staging has a host-visible mount; refusing recursive cleanup"
                        )
                    elif not published:
                        cleanup_error = materialize_sources._remove_tree(
                            outer,
                            outer_identity,
                        )
        finally:
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_mask)
            except KeyboardInterrupt:
                error = KeyboardInterrupt()
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                "installed rootfs materialization failed and private staging was retained "
                f"at {outer}: {cleanup_error}",
                source_tool.EXIT_INTERNAL,
            ) from error
        if published and isinstance(error, KeyboardInterrupt):
            durability = "" if parent_durable else "; output-parent durability is unconfirmed"
            raise source_tool.SourceToolError(
                f"installed rootfs materialization was interrupted after publication at "
                f"{output_path}{durability}; verify that output before any retry",
                source_tool.EXIT_INTEGRITY,
            ) from error
        if isinstance(error, (source_tool.SourceToolError, KeyboardInterrupt)):
            raise
        if isinstance(error, Exception):
            raise source_tool.SourceToolError(
                f"installed rootfs materialization failed: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
        raise
    finally:
        for value, handler in previous_handlers.items():
            signal.signal(value, handler)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize and verify ZivPlayer's offline APT build root."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=DEFAULT_SOURCE_MANIFEST,
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="verify all locked root and APT inputs")
    materialize = commands.add_parser(
        "materialize-apt",
        help="create a fresh installed APT rootfs",
    )
    materialize.add_argument("--output", type=Path, default=DEFAULT_ROOTFS)
    materialize.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_INSTALL_TIMEOUT_SECONDS,
    )
    verify = commands.add_parser("verify-apt", help="verify an installed APT rootfs")
    verify.add_argument("--rootfs", type=Path, default=DEFAULT_ROOTFS)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "preflight":
            state = _load_locked_state(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
            )
            print(
                "offline APT preflight complete: "
                f"{len(state.base_rows)} base + {len(state.package_rows)} cached package(s)"
            )
        elif arguments.command == "materialize-apt":
            materialize_apt_environment(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.output,
                timeout_seconds=arguments.timeout_seconds,
            )
        elif arguments.command == "verify-apt":
            verify_apt_environment(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.rootfs,
            )
        else:
            _schema(f"unsupported command: {arguments.command}")
        return source_tool.EXIT_OK
    except source_tool.SourceToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return source_tool.EXIT_INTERNAL
    except Exception as error:
        print(f"error: unexpected failure: {error}", file=sys.stderr)
        return source_tool.EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
