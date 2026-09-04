#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Enter the executor cgroup and limits before replacing this process."""

from __future__ import annotations

import ctypes
import errno
import os
import resource
import signal
import sys
from pathlib import Path


MARKER = "ziv-native-executor-child-v1"
EXPECTED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "TZ": "UTC",
    "SOURCE_DATE_EPOCH": "946684800",
}
EXPECTED_COMMAND_PREFIX = (
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
)
EXPECTED_PRESERVED_DESCRIPTORS = 10
EXPECTED_NOFILE_LIMIT = 4096
EXPECTED_FILE_SIZE_LIMIT = 2147483648
EXPECTED_CORE_LIMIT = 0
CONTROL_SIGNALS = {
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGHUP,
}


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 4


def _canonical_integer(value: str, label: str, *, minimum: int = 0) -> int:
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value:
        raise ValueError(f"{label} is not a canonical integer")
    result = int(value)
    if result < minimum:
        raise ValueError(f"{label} is below its minimum")
    return result


def _descriptor_list(value: str) -> tuple[int, ...]:
    fields = value.split(",")
    if len(fields) != EXPECTED_PRESERVED_DESCRIPTORS:
        raise ValueError("preserved descriptor count is not exact")
    descriptors = tuple(
        _canonical_integer(field, "preserved descriptor", minimum=3)
        for field in fields
    )
    if len(set(descriptors)) != len(descriptors) or 255 in descriptors:
        raise ValueError("preserved descriptors are duplicated or reserved")
    return descriptors


def _open_descriptor_set() -> set[int]:
    result: set[int] = set()
    for name in os.listdir("/proc/self/fd"):
        if not name.isascii() or not name.isdecimal():
            raise ValueError("process descriptor inventory is not numeric")
        descriptor = int(name)
        try:
            os.fstat(descriptor)
        except OSError as error:
            if error.errno == errno.EBADF:
                continue
            raise
        result.add(descriptor)
    return result


def main(argv: list[str]) -> int:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
        if len(argv) < 10 or argv[0] != MARKER or "--" not in argv:
            raise ValueError("invalid native executor child invocation")
        separator = argv.index("--")
        if separator != 8 or not argv[separator + 1 :]:
            raise ValueError("native executor child argument boundary is not exact")
        expected_parent = _canonical_integer(argv[1], "expected parent", minimum=2)
        if os.getppid() != expected_parent:
            os.kill(os.getpid(), signal.SIGKILL)
        cgroup_descriptor = _canonical_integer(argv[2], "cgroup descriptor", minimum=3)
        launcher_descriptor = _canonical_integer(argv[3], "launcher descriptor", minimum=3)
        nofile_limit = _canonical_integer(argv[4], "open-file limit", minimum=3)
        file_size_limit = _canonical_integer(argv[5], "file-size limit")
        core_limit = _canonical_integer(argv[6], "core limit")
        preserved = _descriptor_list(argv[7])
        command = argv[separator + 1 :]
        all_passed = (*preserved, cgroup_descriptor, launcher_descriptor)
        if len(set(all_passed)) != len(all_passed) or 255 in all_passed:
            raise ValueError("native executor child descriptors are not distinct")
        if (
            nofile_limit != EXPECTED_NOFILE_LIMIT
            or file_size_limit != EXPECTED_FILE_SIZE_LIMIT
            or core_limit != EXPECTED_CORE_LIMIT
        ):
            raise ValueError("native executor child limits are not locked")
        if (
            len(command) <= len(EXPECTED_COMMAND_PREFIX)
            or tuple(command[: len(EXPECTED_COMMAND_PREFIX)])
            != EXPECTED_COMMAND_PREFIX
        ):
            raise ValueError("native executor child command prefix is not locked")
        namespace_script = command[len(EXPECTED_COMMAND_PREFIX)]
        if namespace_script not in {f"/proc/self/fd/{descriptor}" for descriptor in preserved}:
            raise ValueError("namespace helper is not a preserved descriptor")
        if not Path(command[0]).is_absolute():
            raise ValueError("native executor child command is not absolute")
        if os.geteuid() != 0 or os.environ != EXPECTED_ENVIRONMENT:
            raise ValueError("native executor child identity or environment is not exact")
        expected_open = {0, 1, 2, *all_passed}
        if _open_descriptor_set() != expected_open:
            raise ValueError("native executor child inherited an unexpected descriptor")

        payload = f"{os.getpid()}\n".encode("ascii")
        if os.write(cgroup_descriptor, payload) != len(payload):
            raise OSError("short cgroup.procs write")
        os.close(cgroup_descriptor)
        resource.setrlimit(resource.RLIMIT_NOFILE, (nofile_limit, nofile_limit))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_limit, file_size_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (core_limit, core_limit))
        os.close(launcher_descriptor)
        if _open_descriptor_set() != {0, 1, 2, *preserved}:
            raise ValueError("native executor child descriptor cleanup is incomplete")
        signal.pthread_sigmask(signal.SIG_UNBLOCK, CONTROL_SIGNALS)
        os.execve(command[0], command, EXPECTED_ENVIRONMENT)
    except (OSError, ValueError) as error:
        return _fail(str(error))
    return _fail("native executor child command unexpectedly returned")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
