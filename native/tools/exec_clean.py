# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute a fixed command after closing every inherited non-stdio descriptor."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "--":
        print("error: expected -- followed by an absolute command", file=sys.stderr)
        return 2
    command = Path(argv[1])
    if not command.is_absolute():
        print("error: command must be absolute", file=sys.stderr)
        return 2
    try:
        info = command.lstat()
    except OSError as error:
        print(f"error: cannot inspect command: {error}", file=sys.stderr)
        return 2
    if not stat.S_ISREG(info.st_mode) or command.is_symlink():
        print("error: command must be a regular non-symlink file", file=sys.stderr)
        return 2

    try:
        descriptors = [
            int(name)
            for name in os.listdir("/proc/self/fd")
            if name.isascii() and name.isdecimal() and int(name) > 2
        ]
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
        os.execve(
            command,
            [str(command), *argv[2:]],
            {
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C",
                "TZ": "UTC",
                "SOURCE_DATE_EPOCH": "946684800",
            },
        )
    except OSError as error:
        print(f"error: cannot execute descriptor-clean command: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
