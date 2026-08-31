# SPDX-License-Identifier: GPL-3.0-or-later
"""Compose and smoke ZivPlayer's verified APT and Android tool projections."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import hashlib
import io
import json
import os
import re
import selectors
import secrets
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn

if sys.version_info < (3, 11):
    raise SystemExit("composition_tool.py requires Python 3.11 or newer")

import environment_tool
import materialize_sources
import rootfs_tool
import sdk_tool
import source_tool
import toolchain_tool


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NATIVE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_CACHE = NATIVE_DIR / "cache" / "toolchain"
DEFAULT_APT_ROOT = environment_tool.DEFAULT_ROOTFS
DEFAULT_SDK_ROOT = sdk_tool.DEFAULT_OUTPUT
DEFAULT_RECEIPT = Path("/var/tmp/zivplayer-toolchain-composition.json")

RECEIPT_KIND = "ziv-toolchain-composition-receipt-v1"
SMOKE_PROFILE = "ziv-native-toolchain-smoke-v1"
MOUNT_PATH = sdk_tool.MOUNT_PATH
NORMALIZED_MTIME_NS = rootfs_tool.NORMALIZED_MTIME_NS
MAX_HELPER_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 180

NAMESPACE_HELPER = NATIVE_DIR / "toolchain" / "smoke-toolchain-namespace.bash"
CHROOT_HELPER = NATIVE_DIR / "toolchain" / "smoke-toolchain-chroot.bash"
SECCOMP_HELPER = NATIVE_DIR / "toolchain" / "install-seccomp.pl"
HELPER_PATHS = (
    "native/tools/composition_tool.py",
    "native/tools/environment_tool.py",
    "native/tools/sdk_tool.py",
    "native/tools/materialize_sources.py",
    "native/tools/rootfs_tool.py",
    "native/tools/toolchain_tool.py",
    "native/tools/source_tool.py",
    "native/toolchain/smoke-toolchain-namespace.bash",
    "native/toolchain/smoke-toolchain-chroot.bash",
    "native/toolchain/install-seccomp.pl",
)

SMOKE_KEYS = (
    "python",
    "meson",
    "meson_import",
    "ninja",
    "pkg_config",
    "java",
    "javac",
    "aapt2",
    "android_packages",
    "clang_arm64",
    "clang_x86_64",
    "elf_arm64",
    "elf_x86_64",
    "isolation",
)


@dataclass
class BoundInputs:
    apt_root: Path
    sdk_root: Path
    apt_fd: int
    sdk_fd: int
    apt_identity: tuple[int, int]
    sdk_identity: tuple[int, int]
    apt_receipt: dict[str, object]
    apt_receipt_raw: bytes
    sdk_receipt: dict[str, object]
    sdk_receipt_raw: bytes

    def close(self) -> None:
        failure: OSError | None = None
        for name in ("apt_fd", "sdk_fd"):
            descriptor = getattr(self, name)
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError as error:
                    failure = failure or error
                finally:
                    setattr(self, name, -1)
        if failure is not None:
            _integrity(f"cannot close a pinned composition input: {failure}")


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


def _require_linux_root(action: str) -> None:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        _schema(f"toolchain composition {action} requires Linux root")


def _resolved_input(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        _schema(f"{label} must be an absolute directory path")
    parent = materialize_sources._require_real_workspace_parent(path, create=False)
    environment_tool._trusted_output_parent_identity(parent)
    result = parent / path.name
    try:
        info = result.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"{label} is missing: {result}",
            source_tool.EXIT_MISSING,
        ) from error
    except OSError as error:
        _integrity(f"cannot inspect {label}: {error}")
    if not stat.S_ISDIR(info.st_mode) or result.is_symlink():
        _integrity(f"{label} must be a real directory")
    return result


def _open_directory(path: Path, label: str) -> tuple[int, tuple[int, int]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _integrity(f"cannot pin {label}: {error}")
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or (opened.st_uid, opened.st_gid) != (0, 0)
        ):
            _integrity(f"{label} changed while it was pinned")
        return descriptor, (opened.st_dev, opened.st_ino)
    except BaseException:
        os.close(descriptor)
        raise


def _assert_identity(path: Path, descriptor: int, expected: tuple[int, int], label: str) -> None:
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
    except OSError as error:
        _integrity(f"cannot recheck {label} identity: {error}")
    if (
        (opened.st_dev, opened.st_ino) != expected
        or (current.st_dev, current.st_ino) != expected
        or not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
    ):
        _integrity(f"{label} identity changed during composition")


def _read_helper(relative: str) -> bytes:
    return _read_stable_file(
        REPOSITORY_ROOT.joinpath(*Path(relative).parts),
        maximum=MAX_HELPER_BYTES,
        label=f"composition helper {relative}",
        missing_exit=source_tool.EXIT_MISSING,
    )


def _read_stable_file(
    path: Path,
    *,
    maximum: int,
    label: str,
    missing_exit: int = source_tool.EXIT_MISSING,
) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or before.st_nlink != 1
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or toolchain_tool._is_reparse(before)
            or toolchain_tool._is_reparse(opened)
            or toolchain_tool._is_reparse(current)
        ):
            _integrity(f"{label} must be a single-link regular file: {path}")
        _assert_stable_file_stat(before, opened, label, cross_view=True)
        _assert_stable_file_stat(before, current, label)
        if opened.st_size < 0 or opened.st_size > maximum:
            _integrity(f"{label} exceeds {maximum} bytes: {path}")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                _integrity(f"{label} ended before its recorded size: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _integrity(f"{label} grew while it was being read: {path}")
        final_fd = os.fstat(descriptor)
        final_path = path.lstat()
        _assert_stable_file_stat(opened, final_fd, label)
        _assert_stable_file_stat(before, final_path, label)
        return b"".join(chunks)
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"{label} not found: {path}",
            missing_exit,
        ) from error
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot read {label} {path}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_stable_file_stat(
    before: os.stat_result,
    after: os.stat_result,
    label: str,
    *,
    cross_view: bool = False,
) -> None:
    fields = environment_tool._STABLE_STAT_FIELDS
    if os.name == "nt" and cross_view:
        fields = tuple(field for field in fields if field != "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        _integrity(f"{label} changed while it was being read")


def _helper_snapshot() -> tuple[dict[str, bytes], list[dict[str, object]]]:
    raw_by_path: dict[str, bytes] = {}
    records: list[dict[str, object]] = []
    for relative in HELPER_PATHS:
        raw = _read_helper(relative)
        raw_by_path[relative] = raw
        records.append(
            {
                "path": relative,
                "size": len(raw),
                "sha256": _sha256(raw),
            }
        )
    return raw_by_path, records


def _assert_helper_snapshot(raw_by_path: dict[str, bytes]) -> None:
    if tuple(raw_by_path) != HELPER_PATHS:
        _integrity("composition helper snapshot has an unexpected path set or order")
    for relative, expected in raw_by_path.items():
        if _read_helper(relative) != expected:
            _integrity(f"composition helper changed during composition: {relative}")


def _open_pinned_helper(path: Path, expected_raw: bytes, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _integrity(f"cannot pin {label}: {error}")
    try:
        info = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino, info.st_size)
            != (current.st_dev, current.st_ino, current.st_size)
            or path.is_symlink()
            or info.st_size != len(expected_raw)
        ):
            _integrity(f"{label} changed while it was pinned")
        digest = hashlib.sha256()
        offset = 0
        while offset < info.st_size:
            chunk = os.pread(descriptor, min(64 * 1024, info.st_size - offset), offset)
            if not chunk:
                _integrity(f"{label} ended before its recorded size")
            digest.update(chunk)
            offset += len(chunk)
        if digest.digest() != hashlib.sha256(expected_raw).digest():
            _integrity(f"{label} differs from its stable snapshot")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_receipt_binding(
    apt_receipt: dict[str, object],
    sdk_receipt: dict[str, object],
) -> None:
    if (
        apt_receipt.get("schemaVersion") != 1
        or apt_receipt.get("kind") != "ziv-toolchain-apt"
        or apt_receipt.get("ready") is not False
    ):
        _integrity("APT root receipt has an unsupported identity")
    if (
        sdk_receipt.get("schemaVersion") != 1
        or sdk_receipt.get("kind") != "ziv-sdk-projection-receipt-v1"
        or sdk_receipt.get("ready") is not False
        or sdk_receipt.get("mountPath") != MOUNT_PATH
    ):
        _integrity("SDK projection receipt has an unsupported identity")
    if (
        apt_receipt.get("manifestSha256")
        != sdk_receipt.get("toolchainManifestSha256")
        or apt_receipt.get("sourceManifestSha256")
        != sdk_receipt.get("sourceManifestSha256")
    ):
        _integrity("APT and SDK receipts are not bound to the same manifests")
    composition = sdk_receipt.get("composition")
    if not isinstance(composition, dict) or composition != {
        "aptEnvironmentBound": False,
        "mountPath": MOUNT_PATH,
        "releaseInput": False,
        "type": "standalone-mountable-projection",
    }:
        _integrity("SDK projection receipt has an unexpected composition boundary")


def _load_bound_inputs(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    apt_root: Path,
    sdk_root: Path,
) -> BoundInputs:
    apt_path = _resolved_input(apt_root, "APT root")
    sdk_path = _resolved_input(sdk_root, "SDK projection")
    if (
        apt_path == sdk_path
        or apt_path in sdk_path.parents
        or sdk_path in apt_path.parents
    ):
        _integrity("APT and SDK inputs must be disjoint directories")
    _quietly_verify_input_trees(
        manifest,
        source_manifest,
        cache,
        apt_path,
        sdk_path,
    )
    environment_tool._assert_no_nested_mounts(apt_path)
    environment_tool._assert_no_nested_mounts(sdk_path)
    apt_fd = -1
    sdk_fd = -1
    try:
        apt_fd, apt_identity = _open_directory(apt_path, "APT root")
        sdk_fd, sdk_identity = _open_directory(sdk_path, "SDK projection")
        apt_receipt, apt_receipt_raw = environment_tool._read_receipt(apt_path)
        sdk_receipt, sdk_receipt_raw = sdk_tool._read_receipt(sdk_path)
        _validate_receipt_binding(apt_receipt, sdk_receipt)
        _assert_identity(apt_path, apt_fd, apt_identity, "APT root")
        _assert_identity(sdk_path, sdk_fd, sdk_identity, "SDK projection")
        return BoundInputs(
            apt_path,
            sdk_path,
            apt_fd,
            sdk_fd,
            apt_identity,
            sdk_identity,
            apt_receipt,
            apt_receipt_raw,
            sdk_receipt,
            sdk_receipt_raw,
        )
    except BaseException:
        if apt_fd >= 0:
            os.close(apt_fd)
        if sdk_fd >= 0:
            os.close(sdk_fd)
        raise


def _quietly_verify_input_trees(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    apt_root: Path,
    sdk_root: Path,
) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        environment_tool.verify_apt_environment(
            manifest,
            source_manifest,
            cache,
            apt_root,
        )
        sdk_tool.verify_sdk_projection(
            manifest,
            source_manifest,
            cache,
            sdk_root,
        )


def _reverify_bound_inputs(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    inputs: BoundInputs,
) -> None:
    _quietly_verify_input_trees(
        manifest,
        source_manifest,
        cache,
        inputs.apt_root,
        inputs.sdk_root,
    )
    apt_receipt, apt_raw = environment_tool._read_receipt(inputs.apt_root)
    sdk_receipt, sdk_raw = sdk_tool._read_receipt(inputs.sdk_root)
    if (
        apt_raw != inputs.apt_receipt_raw
        or sdk_raw != inputs.sdk_receipt_raw
        or apt_receipt != inputs.apt_receipt
        or sdk_receipt != inputs.sdk_receipt
    ):
        _integrity("composition input receipt changed after it was pinned")
    _assert_identity(inputs.apt_root, inputs.apt_fd, inputs.apt_identity, "APT root")
    _assert_identity(inputs.sdk_root, inputs.sdk_fd, inputs.sdk_identity, "SDK projection")
    environment_tool._assert_no_nested_mounts(inputs.apt_root)
    environment_tool._assert_no_nested_mounts(inputs.sdk_root)


def _namespace_id(name: str) -> str:
    try:
        value = os.readlink(f"/proc/self/ns/{name}")
    except OSError as error:
        _integrity(f"cannot read host {name} namespace identity: {error}")
    if re.fullmatch(rf"{re.escape(name)}:\[[0-9]+\]", value) is None:
        _integrity(f"host {name} namespace identity is malformed")
    return value


def _append_bounded(buffer: bytearray, chunk: bytes, label: str) -> None:
    if len(buffer) + len(chunk) > MAX_LOG_BYTES:
        _integrity(f"composition {label} exceeds {MAX_LOG_BYTES} bytes")
    buffer.extend(chunk)


def _run_smoke(
    inputs: BoundInputs,
    timeout_seconds: int,
    helper_raws: dict[str, bytes],
) -> tuple[bytes, bytes]:
    if timeout_seconds < 30 or timeout_seconds > 900:
        _schema("composition smoke timeout must be between 30 and 900 seconds")
    if tuple(helper_raws) != HELPER_PATHS:
        _integrity("composition helper snapshot has an unexpected path set or order")
    namespace_raw = helper_raws["native/toolchain/smoke-toolchain-namespace.bash"]
    chroot_raw = helper_raws["native/toolchain/smoke-toolchain-chroot.bash"]
    seccomp_raw = helper_raws["native/toolchain/install-seccomp.pl"]
    namespace_fd = -1
    chroot_fd = -1
    seccomp_fd = -1
    cgroup: environment_tool.CgroupHandle | None = None
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    streams: dict[BinaryIO, bytearray] = {}
    failure: BaseException | None = None
    return_code = -1
    blocked_signals = {
        signal.SIGINT,
        signal.SIGTERM,
        getattr(signal, "SIGHUP", signal.SIGTERM),
    }
    previous_mask: set[signal.Signals] | None = None
    try:
        namespace_fd = _open_pinned_helper(
            NAMESPACE_HELPER,
            namespace_raw,
            "namespace helper",
        )
        chroot_fd = _open_pinned_helper(
            CHROOT_HELPER,
            chroot_raw,
            "chroot smoke helper",
        )
        seccomp_fd = _open_pinned_helper(
            SECCOMP_HELPER,
            seccomp_raw,
            "seccomp helper",
        )
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
        _assert_identity(inputs.apt_root, inputs.apt_fd, inputs.apt_identity, "APT root")
        cgroup = environment_tool._prepare_installer_cgroup(
            Path(f"/proc/self/fd/{inputs.apt_fd}")
        )
        expected_parent = os.getpid()
        argv = [
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
            f"/proc/self/fd/{namespace_fd}",
            str(inputs.apt_root),
            str(inputs.sdk_root),
            str(inputs.apt_fd),
            str(inputs.sdk_fd),
            str(chroot_fd),
            str(seccomp_fd),
            f"{inputs.apt_identity[0]}:{inputs.apt_identity[1]}",
            f"{inputs.sdk_identity[0]}:{inputs.sdk_identity[1]}",
            _sha256(chroot_raw),
            _sha256(seccomp_raw),
            *(_namespace_id(name) for name in ("mnt", "net", "pid", "uts", "ipc")),
            SMOKE_PROFILE,
        ]
        process = subprocess.Popen(
            argv,
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
            pass_fds=(
                inputs.apt_fd,
                inputs.sdk_fd,
                namespace_fd,
                chroot_fd,
                seccomp_fd,
                cgroup.procs_fd,
            ),
            start_new_session=True,
            preexec_fn=lambda: environment_tool._set_parent_death_signal(
                expected_parent,
                previous_mask,
                cgroup.procs_fd,
            ),
        )
        assert process.stdout is not None and process.stderr is not None
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
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
        deadline = time.monotonic() + timeout_seconds
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = source_tool.SourceToolError(
                    f"composition smoke exceeded {timeout_seconds} seconds",
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
                        "stdout" if stream is process.stdout else "stderr",
                    )
                except source_tool.SourceToolError as error:
                    failure = error
                    break
            if failure is not None:
                break
            violation = environment_tool._cgroup_limit_violation(cgroup)
            if violation is not None:
                failure = environment_tool.InstallerIsolationError(
                    violation,
                    retain_staging=False,
                )
                break
        if failure is None:
            return_code = process.wait(timeout=10)
            violation = environment_tool._cgroup_limit_violation(cgroup)
            if violation is not None:
                failure = environment_tool.InstallerIsolationError(
                    violation,
                    retain_staging=False,
                )
            elif not environment_tool._cgroup_is_empty(cgroup):
                failure = environment_tool.InstallerIsolationError(
                    "composition smoke left descendant processes running",
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
                failure = failure or close_error
            finally:
                cgroup.procs_fd = -1
        try:
            if previous_mask is not None:
                try:
                    signal.pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
                except BaseException as mask_error:
                    failure = mask_error
            if cgroup is not None and not cgroup.removed:
                try:
                    if failure is not None or not environment_tool._cgroup_is_empty(cgroup):
                        environment_tool._terminate_installer(process, cgroup)
                    else:
                        environment_tool._remove_installer_cgroup(cgroup)
                except BaseException as cleanup_error:
                    failure = cleanup_error
        finally:
            if previous_mask is not None:
                try:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                except BaseException as signal_error:
                    failure = signal_error
        if selector is not None:
            try:
                selector.close()
            except BaseException as close_error:
                failure = failure or close_error
        for stream in streams:
            if not stream.closed:
                try:
                    stream.close()
                except BaseException as close_error:
                    failure = failure or close_error
        if namespace_fd >= 0:
            try:
                os.close(namespace_fd)
            except BaseException as close_error:
                failure = failure or close_error
        if chroot_fd >= 0:
            try:
                os.close(chroot_fd)
            except BaseException as close_error:
                failure = failure or close_error
        if seccomp_fd >= 0:
            try:
                os.close(seccomp_fd)
            except BaseException as close_error:
                failure = failure or close_error
    if process is None or process.stdout is None or process.stderr is None:
        assert failure is not None
        raise failure
    stdout = bytes(streams[process.stdout])
    stderr = bytes(streams[process.stderr])
    if failure is not None:
        raise failure
    if return_code != 0:
        tail = stderr[-4096:].decode("utf-8", "replace").strip()
        _integrity(f"composition smoke exited {return_code}; stderr tail: {tail}")
    if stderr:
        tail = stderr[-4096:].decode("utf-8", "replace").strip()
        _integrity(f"composition smoke emitted unexpected stderr: {tail}")
    environment_tool._assert_no_nested_mounts(inputs.apt_root)
    environment_tool._assert_no_nested_mounts(inputs.sdk_root)
    return stdout, stderr


def _parse_smoke(raw: bytes) -> dict[str, str]:
    if not raw or len(raw) > MAX_LOG_BYTES or not raw.endswith(b"\n"):
        _integrity("composition smoke transcript is empty, oversized, or unterminated")
    if b"\r" in raw or b"\0" in raw:
        _integrity("composition smoke transcript contains noncanonical bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        _integrity(f"composition smoke transcript is not UTF-8: {error}")
    records: dict[str, str] = {}
    keys: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.count("\t") != 1:
            _integrity(f"composition smoke line {number} is not canonical TSV")
        key, value = line.split("\t")
        if (
            not key
            or not value
            or len(value.encode("utf-8")) > 2048
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
            or key in records
        ):
            _integrity(f"composition smoke line {number} has an invalid record")
        keys.append(key)
        records[key] = value
    if tuple(keys) != SMOKE_KEYS:
        _integrity("composition smoke record order or set differs from the fixed profile")
    exact = {
        "python": "Python 3.12.3",
        "meson": "1.11.0",
        "meson_import": "/opt/zivplayer/toolchain/python/site-packages/mesonbuild/__init__.py",
        "ninja": "1.11.1",
        "pkg_config": "1.8.1",
        "android_packages": "build-tools=36.0.0;platform=36-r02;ndk=29.0.14206865",
        "elf_arm64": "AArch64;load-align=0x4000",
        "elf_x86_64": "Advanced Micro Devices X86-64;load-align=0x4000",
        "isolation": "private-namespaces;loopback-only;seccomp;no-caps;read-only-inputs",
    }
    for key, expected in exact.items():
        if records[key] != expected:
            _integrity(f"composition smoke {key} differs from the locked expectation")
    patterns = {
        "java": r'openjdk version "17\.0\.[0-9]+"(?: .*)?',
        "javac": r"javac 17\.0\.[0-9]+",
        "aapt2": r"Android Asset Packaging Tool.*",
        "clang_arm64": r".*clang version 21\.0\.0.*",
        "clang_x86_64": r".*clang version 21\.0\.0.*",
    }
    for key, pattern in patterns.items():
        if re.fullmatch(pattern, records[key]) is None:
            _integrity(f"composition smoke {key} has an unexpected version")
    return records


def _input_records(inputs: BoundInputs) -> dict[str, object]:
    apt_tree = inputs.apt_receipt.get("tree")
    sdk_tree = inputs.sdk_receipt.get("tree")
    sdk_projection = inputs.sdk_receipt.get("projection")
    if not all(isinstance(value, dict) for value in (apt_tree, sdk_tree, sdk_projection)):
        _integrity("composition input receipts omit tree or projection evidence")
    return {
        "apt": {
            "kind": inputs.apt_receipt["kind"],
            "receiptSha256": _sha256(inputs.apt_receipt_raw),
            "tree": apt_tree,
        },
        "sdk": {
            "kind": inputs.sdk_receipt["kind"],
            "receiptSha256": _sha256(inputs.sdk_receipt_raw),
            "tree": sdk_tree,
            "projection": sdk_projection,
            "mountPath": MOUNT_PATH,
        },
    }


def _mount_policy() -> dict[str, object]:
    return {
        "root": "ext4:ro,nosuid,nodev,exec",
        "sdk": "ext4:ro,nosuid,nodev,exec",
        "sdkMountPath": MOUNT_PATH,
        "opt": "tmpfs:rw,nosuid,nodev,noexec,size=16m,mode=0755",
        "proc": "proc:ro,nosuid,nodev,noexec,hidepid=2",
        "procKeys": "empty-root-owned-tmpfs-file-bind:ro,nosuid,nodev,noexec,mode=0444",
        "dev": "tmpfs:ro,nosuid,noexec,size=16m,mode=0755",
        "devShm": "tmpfs:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "run": "tmpfs:rw,nosuid,nodev,noexec,size=64m,mode=0755",
        "tmp": "tmpfs:rw,nosuid,nodev,noexec,size=512m,mode=1777",
        "varTmp": "tmpfs:rw,nosuid,nodev,noexec,size=512m,mode=1777",
        "varLog": "tmpfs:rw,nosuid,nodev,noexec,size=64m,mode=0755",
        "propagation": "private",
        "oldRoot": "pivot_root then recursive lazy detach with visibility check",
    }


def _receipt_data(
    inputs: BoundInputs,
    stdout: bytes,
    stderr: bytes,
    helper_records: list[dict[str, object]],
) -> dict[str, object]:
    records = _parse_smoke(stdout)
    if stderr:
        _integrity("composition receipt cannot record nonempty stderr")
    input_records = _input_records(inputs)
    mount_policy = _mount_policy()
    composition_projection = {
        "format": "ziv-toolchain-composition-v1",
        "inputs": input_records,
        "mountPolicy": mount_policy,
        "smokeProfile": SMOKE_PROFILE,
        "smokeTranscriptSha256": _sha256(stdout),
        "helpers": helper_records,
    }
    sandbox_raw = environment_tool._sandbox_policy_record()
    sandbox = json.loads(sandbox_raw)
    return {
        "schemaVersion": 1,
        "kind": RECEIPT_KIND,
        "ready": False,
        "releaseInput": False,
        "project": "ZivPlayer",
        "toolchainManifestSha256": inputs.apt_receipt["manifestSha256"],
        "sourceManifestSha256": inputs.apt_receipt["sourceManifestSha256"],
        "inputs": input_records,
        "composition": {
            "type": "ephemeral-read-only-bind",
            "aptEnvironmentBound": True,
            "compositionVerified": True,
            "inputMutationObserved": False,
            "mountPath": MOUNT_PATH,
            "mountPolicy": mount_policy,
            "namespace": {
                "mount": True,
                "network": True,
                "pid": True,
                "ipc": True,
                "uts": True,
                "user": False,
                "pidOne": True,
                "visibleInterfaces": ["lo"],
                "routes": "none",
            },
            "sha256": _sha256(_canonical_json(composition_projection)),
        },
        "smoke": {
            "profile": SMOKE_PROFILE,
            "passed": True,
            "records": records,
            "transcript": {
                "encoding": "utf-8-canonical-tsv",
                "text": stdout.decode("utf-8"),
                "size": len(stdout),
                "sha256": _sha256(stdout),
            },
            "stderr": {
                "size": 0,
                "sha256": _sha256(b""),
            },
        },
        "isolation": {
            "helpers": helper_records,
            "processSandboxReuse": sandbox,
            "cgroupDrained": True,
            "descendantsRemaining": False,
            "mountLeak": False,
        },
        "postconditions": {
            "aptIdentityStable": True,
            "aptReceiptAndTreeReverified": True,
            "sdkIdentityStable": True,
            "sdkReceiptAndTreeReverified": True,
            "inputsRemainStandalone": True,
        },
        "compliance": inputs.sdk_receipt["compliance"],
        "releaseBlockers": [
            "accepted Android license evidence is not installed",
            "system notices bundle is not complete",
            "retention bundle is not complete",
            "canonical preserve-mode source workspace and native build are pending",
            "dual-ABI ELF, JNI, SBOM, corresponding-source, and device gates are pending",
        ],
    }


def _resolved_receipt_path(path: Path) -> tuple[Path, Path]:
    if not path.is_absolute() or path.name in {"", ".", ".."} or path.suffix != ".json":
        _schema("composition receipt must be an absolute .json file path")
    parent = materialize_sources._require_real_workspace_parent(path, create=False)
    environment_tool._trusted_output_parent_identity(parent)
    rootfs_tool._require_ext4(parent)
    result = parent / path.name
    if len(path.name.encode("utf-8")) > rootfs_tool.MAX_COMPONENT_BYTES:
        _schema("composition receipt filename exceeds the component limit")
    return parent, result


def _assert_receipt_location(
    output: Path,
    cache: Path,
    inputs: BoundInputs,
) -> None:
    cache_root = cache.resolve(strict=False)
    forbidden_roots = (inputs.apt_root, inputs.sdk_root, cache_root)
    if any(output == root or root in output.parents for root in forbidden_roots):
        _integrity(
            "composition receipt must stay outside both immutable inputs and the toolchain cache"
        )


def _read_composition_receipt(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"composition receipt is missing: {path}",
            source_tool.EXIT_MISSING,
        ) from error
    except OSError as error:
        _integrity(f"cannot inspect composition receipt: {error}")
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o644
        or (info.st_uid, info.st_gid) != (0, 0)
        or info.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("composition receipt metadata is not canonical")
    xattrs_before, _size = environment_tool._xattr_digest(path)
    if xattrs_before is not None:
        _integrity("composition receipt must not carry extended attributes")
    raw = _read_stable_file(
        path,
        maximum=MAX_RECEIPT_BYTES,
        label="composition receipt",
    )
    after = path.lstat()
    environment_tool._assert_stable_stat(info, after, "composition receipt")
    xattrs_after, _size = environment_tool._xattr_digest(path)
    if xattrs_after is not None or xattrs_after != xattrs_before:
        _integrity("composition receipt extended attributes changed while reading")
    receipt = environment_tool._read_json_bytes(raw, "composition receipt")
    return receipt, raw


def _rename_no_replace_at(parent_fd: int, source: str, destination: str) -> None:
    if sys.platform != "linux":
        _schema("composition receipt publication requires Linux renameat2")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        _integrity("Linux libc does not expose renameat2(RENAME_NOREPLACE)")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent_fd,
        os.fsencode(source),
        parent_fd,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        _integrity(f"refusing to replace existing composition receipt: {destination}")
    raise OSError(error_number, os.strerror(error_number), destination)


def _same_directory_identity(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink")
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _open_receipt_parent(parent: Path) -> tuple[int, os.stat_result]:
    expected_identity = environment_tool._trusted_output_parent_identity(parent)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(parent, flags)
        opened = os.fstat(descriptor)
        current = parent.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_directory_identity(opened, current)
            or expected_identity != (opened.st_dev, opened.st_ino)
        ):
            _integrity("composition receipt parent changed before publication")
        return descriptor, opened
    except source_tool.SourceToolError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        _integrity(f"cannot pin composition receipt parent: {error}")


def _publish_receipt(path: Path, receipt: dict[str, object]) -> None:
    parent, output = _resolved_receipt_path(path)
    raw = _canonical_json(receipt)
    if len(raw) > MAX_RECEIPT_BYTES:
        _integrity("composition receipt exceeds its byte budget")
    parent_fd, parent_info = _open_receipt_parent(parent)
    descriptor = -1
    temporary_name: str | None = None
    temporary_info: os.stat_result | None = None
    published = False
    try:
        try:
            os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _integrity(f"refusing to replace existing composition receipt: {output}")
        temporary_name = f".ziv-composition-{secrets.token_hex(16)}.part"
        temporary_flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(temporary_name, temporary_flags, 0o600, dir_fd=parent_fd)
        temporary_info = os.fstat(descriptor)
        os.fchmod(descriptor, 0o644)
        os.fchown(descriptor, 0, 0)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _integrity("short write while publishing the composition receipt")
            view = view[written:]
        os.utime(
            descriptor,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
        )
        os.fsync(descriptor)
        current = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        staged = bytearray()
        while len(staged) < len(raw):
            chunk = os.read(descriptor, min(64 * 1024, len(raw) - len(staged)))
            if not chunk:
                break
            staged.extend(chunk)
        extra = os.read(descriptor, 1)
        try:
            xattrs = os.listxattr(descriptor)
        except (AttributeError, OSError) as error:
            _integrity(f"cannot inspect staged composition receipt xattrs: {error}")
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino)
            != (temporary_info.st_dev, temporary_info.st_ino)
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) != 0o644
            or (current.st_uid, current.st_gid) != (0, 0)
            or current.st_mtime_ns != NORMALIZED_MTIME_NS
            or current.st_size != len(raw)
            or bytes(staged) != raw
            or extra
            or xattrs
        ):
            _integrity("staged composition receipt changed before publication")
        _rename_no_replace_at(parent_fd, temporary_name, output.name)
        published = True
        published_info = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(published_info.st_mode)
            or (published_info.st_dev, published_info.st_ino)
            != (current.st_dev, current.st_ino)
            or published_info.st_nlink != 1
            or stat.S_IMODE(published_info.st_mode) != 0o644
            or (published_info.st_uid, published_info.st_gid) != (0, 0)
            or published_info.st_mtime_ns != NORMALIZED_MTIME_NS
            or published_info.st_size != len(raw)
        ):
            _integrity("published composition receipt metadata changed during rename")
        os.fsync(parent_fd)
        current_parent = parent.lstat()
        if not _same_directory_identity(parent_info, current_parent):
            _integrity("composition receipt parent changed during publication")
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot publish composition receipt: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published and temporary_name is not None and temporary_info is not None:
            try:
                staged_info = os.stat(
                    temporary_name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                if (staged_info.st_dev, staged_info.st_ino) == (
                    temporary_info.st_dev,
                    temporary_info.st_ino,
                ):
                    os.unlink(temporary_name, dir_fd=parent_fd)
        os.close(parent_fd)


def preflight(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    apt_root: Path,
    sdk_root: Path,
) -> None:
    _require_linux_root("preflight")
    inputs = _load_bound_inputs(manifest, source_manifest, cache, apt_root, sdk_root)
    try:
        print(
            "composition preflight complete: "
            f"APT {inputs.apt_receipt['tree']['entries']} entries + "
            f"SDK {inputs.sdk_receipt['tree']['entries']} entries"
        )
    finally:
        inputs.close()


def compose_and_smoke(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    apt_root: Path,
    sdk_root: Path,
    receipt_path: Path,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    _require_linux_root("smoke")
    _parent, output = _resolved_receipt_path(receipt_path)
    if output.exists() or output.is_symlink():
        _integrity(f"refusing to replace existing composition receipt: {output}")
    helper_raws, helper_records = _helper_snapshot()
    inputs = _load_bound_inputs(manifest, source_manifest, cache, apt_root, sdk_root)
    try:
        _assert_receipt_location(output, cache, inputs)
        stdout, stderr = _run_smoke(inputs, timeout_seconds, helper_raws)
        _parse_smoke(stdout)
        _assert_identity(inputs.apt_root, inputs.apt_fd, inputs.apt_identity, "APT root")
        _assert_identity(inputs.sdk_root, inputs.sdk_fd, inputs.sdk_identity, "SDK projection")
        _reverify_bound_inputs(
            manifest,
            source_manifest,
            cache,
            inputs,
        )
        _assert_helper_snapshot(helper_raws)
        receipt = _receipt_data(inputs, stdout, stderr, helper_records)
        _publish_receipt(output, receipt)
    finally:
        inputs.close()
    try:
        verify_composition(
            manifest,
            source_manifest,
            cache,
            apt_root,
            sdk_root,
            receipt_path,
            announce=False,
        )
    except source_tool.SourceToolError as error:
        raise source_tool.SourceToolError(
            f"composition receipt was published at {output}, but its "
            f"post-publication verification failed: {error}; retain it for "
            "inspection and deliberately verify or remove that exact file",
            error.exit_code,
        ) from error
    print(f"composed and smoked locked toolchain: {output}")


def verify_composition(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    apt_root: Path,
    sdk_root: Path,
    receipt_path: Path,
    *,
    announce: bool = True,
) -> None:
    _require_linux_root("verification")
    _parent, receipt_path = _resolved_receipt_path(receipt_path)
    helper_raws, helper_records = _helper_snapshot()
    inputs = _load_bound_inputs(manifest, source_manifest, cache, apt_root, sdk_root)
    try:
        _assert_receipt_location(receipt_path, cache, inputs)
        actual, actual_raw = _read_composition_receipt(receipt_path)
        smoke = actual.get("smoke")
        if not isinstance(smoke, dict):
            _integrity("composition receipt omits smoke evidence")
        transcript = smoke.get("transcript")
        if not isinstance(transcript, dict) or not isinstance(transcript.get("text"), str):
            _integrity("composition receipt omits its canonical smoke transcript")
        try:
            stdout = transcript["text"].encode("utf-8")
        except UnicodeEncodeError as error:
            _integrity(f"composition smoke transcript cannot be encoded: {error}")
        _parse_smoke(stdout)
        _reverify_bound_inputs(
            manifest,
            source_manifest,
            cache,
            inputs,
        )
        expected = _receipt_data(inputs, stdout, b"", helper_records)
        _assert_helper_snapshot(helper_raws)
        if actual != expected or actual_raw != _canonical_json(expected):
            _integrity("composition receipt differs from its inputs, helpers, or smoke")
    finally:
        inputs.close()
    if announce:
        print(f"verified locked toolchain composition: {receipt_path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=DEFAULT_SOURCE_MANIFEST,
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("preflight", "verify both standalone inputs without mounting"),
        ("compose-and-smoke", "run the fixed isolated toolchain smoke profile"),
        ("verify", "verify a composition receipt and both current inputs"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--apt-root", type=Path, default=DEFAULT_APT_ROOT)
        command.add_argument("--sdk-root", type=Path, default=DEFAULT_SDK_ROOT)
        if name != "preflight":
            command.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
        if name == "compose-and-smoke":
            command.add_argument(
                "--timeout-seconds",
                type=int,
                default=DEFAULT_TIMEOUT_SECONDS,
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "preflight":
            preflight(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.apt_root,
                arguments.sdk_root,
            )
        elif arguments.command == "compose-and-smoke":
            compose_and_smoke(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.apt_root,
                arguments.sdk_root,
                arguments.receipt,
                timeout_seconds=arguments.timeout_seconds,
            )
        elif arguments.command == "verify":
            verify_composition(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.apt_root,
                arguments.sdk_root,
                arguments.receipt,
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
