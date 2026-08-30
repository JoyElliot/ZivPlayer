# SPDX-License-Identifier: GPL-3.0-or-later
"""Materialize ZivPlayer's verified native source cache into a build workspace."""

from __future__ import annotations

import argparse
import bz2
import ctypes
import errno
import gzip
import hashlib
import json
import lzma
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator, NoReturn

if sys.version_info < (3, 11):
    raise SystemExit("materialize_sources.py requires Python 3.11 or newer")

import source_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_CACHE = NATIVE_DIR / "cache" / "sources"
DEFAULT_WORKSPACE = NATIVE_DIR / "out" / "workspace"

RECEIPT_NAME = "ziv-native-materialization.json"
TREE_DIGEST_FORMAT = "ziv-native-tree-v1"
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_TREE_ENTRIES = 500_000
MAX_TREE_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_EXPANSION_RATIO = 100
MAX_TAR_METADATA_BUDGET_BYTES = 16 * 1024 * 1024
MAX_TAR_METADATA_RECORD_BYTES = 1024 * 1024
MAX_TAR_EXTENSION_CHAIN_DEPTH = 128
MAX_TAR_READ_BYTES = 8 * 1024 * 1024
MAX_TAR_STREAM_BYTES = (
    MAX_ARCHIVE_BYTES
    + (2 * tarfile.BLOCKSIZE * MAX_ARCHIVE_MEMBERS)
    + MAX_TAR_METADATA_BUDGET_BYTES
)
MAX_ARCHIVE_PATH_BYTES = 4096
MAX_ARCHIVE_PATH_DEPTH = 128
NORMALIZED_GENERATED_MTIME = 946_684_800
NORMALIZED_GENERATED_MTIME_NS = NORMALIZED_GENERATED_MTIME * 1_000_000_000

ROOT_SOURCE_ID = "mpv-android"
SINGLE_FILE_SOURCE_ID = "gas-preprocessor"
TOP_LEVEL_ALIASES = {
    "freetype": "freetype2",
    "libunibreak": "unibreak",
}
WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class PlannedMember:
    info: tarfile.TarInfo
    path: PurePosixPath
    kind: str
    link_target: PurePosixPath | None = None


@dataclass(frozen=True)
class FileProof:
    size: int
    sha256: str
    mode: int | None


@dataclass(frozen=True)
class ExpectedEntry:
    kind: str
    mode: int | None = None
    file: FileProof | None = None


@dataclass(frozen=True)
class LockedArchiveScan:
    plans: dict[str, list[PlannedMember]]
    regular_files: dict[tuple[str, PurePosixPath], FileProof]


class _BoundedTarInfo(tarfile.TarInfo):
    _EXTENDED_HEADER_TYPES = {
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
        tarfile.XHDTYPE,
        tarfile.XGLTYPE,
        tarfile.SOLARIS_XHDTYPE,
    }
    _LONG_PATH_HEADER_TYPES = {
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
    }

    def _proc_gnusparse_00(
        self,
        next_info: tarfile.TarInfo,
        raw_headers: list[tuple[int, bytes, bytes]],
    ) -> None:
        del next_info, raw_headers
        _integrity("archive uses unsupported GNU sparse-file metadata")

    def _proc_gnusparse_01(
        self,
        next_info: tarfile.TarInfo,
        pax_headers: dict[str, str],
    ) -> None:
        del next_info, pax_headers
        _integrity("archive uses unsupported GNU sparse-file metadata")

    def _proc_gnusparse_10(
        self,
        next_info: tarfile.TarInfo,
        pax_headers: dict[str, str],
        archive: tarfile.TarFile,
    ) -> None:
        del next_info, pax_headers, archive
        _integrity("archive uses unsupported GNU sparse-file metadata")

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type == tarfile.GNUTYPE_SPARSE:
            _integrity("archive uses unsupported GNU sparse-file metadata")
        if self.type not in self._EXTENDED_HEADER_TYPES:
            return super()._proc_member(archive)

        extension_depth = getattr(archive, "_ziv_extension_depth", 0) + 1
        if extension_depth > MAX_TAR_EXTENSION_CHAIN_DEPTH:
            _integrity(
                f"archive TAR extension chain exceeds the "
                f"{MAX_TAR_EXTENSION_CHAIN_DEPTH}-header depth limit"
            )
        setattr(archive, "_ziv_extension_depth", extension_depth)
        try:
            record_limit = (
                MAX_ARCHIVE_PATH_BYTES + 1
                if self.type in self._LONG_PATH_HEADER_TYPES
                else MAX_TAR_METADATA_RECORD_BYTES
            )
            if self.size > record_limit:
                _integrity(
                    f"archive TAR extension exceeds the {record_limit}-byte "
                    "metadata record limit"
                )
            padded_size = (
                (self.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
            ) * tarfile.BLOCKSIZE
            metadata_bytes = getattr(archive, "_ziv_metadata_bytes", 0) + padded_size
            if metadata_bytes > MAX_TAR_METADATA_BUDGET_BYTES:
                _integrity(
                    f"archive TAR extensions exceed the "
                    f"{MAX_TAR_METADATA_BUDGET_BYTES}-byte metadata budget"
                )
            setattr(archive, "_ziv_metadata_bytes", metadata_bytes)
            return super()._proc_member(archive)
        finally:
            setattr(archive, "_ziv_extension_depth", extension_depth - 1)


class _BoundedTarReader:
    def __init__(self, stream: BinaryIO, source_id: str, limit: int) -> None:
        self._stream = stream
        self._source_id = source_id
        self._limit = limit

    def _position(self) -> int:
        position = self._stream.tell()
        if position < 0 or position > self._limit:
            _integrity(
                f"archive for {self._source_id} exceeds its bounded decompressed TAR stream"
            )
        return position

    def tell(self) -> int:
        return self._position()

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            _integrity(f"archive for {self._source_id} attempted an unbounded TAR read")
        if size > MAX_TAR_READ_BYTES:
            _integrity(
                f"archive for {self._source_id} exceeds the {MAX_TAR_READ_BYTES}-byte "
                "TAR metadata/read request limit"
            )
        position = self._position()
        remaining = self._limit - position
        data = self._stream.read(min(size, remaining + 1))
        if len(data) > remaining:
            _integrity(
                f"archive for {self._source_id} exceeds its bounded decompressed TAR stream"
            )
        return data

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self._position() + offset
        else:
            _integrity(
                f"archive for {self._source_id} attempted an unsupported TAR seek"
            )
        if target < 0 or target > self._limit:
            _integrity(
                f"archive for {self._source_id} exceeds its bounded decompressed TAR stream"
            )
        position = self._stream.seek(offset, whence)
        if position != target:
            _integrity(f"archive for {self._source_id} returned an inconsistent TAR position")
        return position


@contextmanager
def _open_bounded_tar(
    archive_stream: BinaryIO,
    source_id: str,
    compressed_size: int,
) -> Iterator[tarfile.TarFile]:
    archive_stream.seek(0)
    magic = archive_stream.read(10)
    archive_stream.seek(0)
    decompressed: BinaryIO
    if magic.startswith(b"\x1f\x8b\x08"):
        decompressed = gzip.GzipFile(fileobj=archive_stream, mode="rb")
    elif (
        magic[:3] == b"BZh"
        and len(magic) >= 10
        and magic[3] in b"123456789"
        and magic[4:10] == b"1AY&SY"
    ):
        decompressed = bz2.BZ2File(archive_stream, mode="rb")
    elif magic.startswith(b"\xfd7zXZ\x00"):
        decompressed = lzma.LZMAFile(archive_stream, mode="rb")
    else:
        decompressed = archive_stream

    stream_limit = min(
        MAX_TAR_STREAM_BYTES,
        compressed_size * MAX_ARCHIVE_EXPANSION_RATIO
        + (2 * tarfile.BLOCKSIZE * MAX_ARCHIVE_MEMBERS)
        + MAX_TAR_METADATA_BUDGET_BYTES,
    )
    bounded = _BoundedTarReader(decompressed, source_id, stream_limit)
    try:
        try:
            with tarfile.open(
                fileobj=bounded,
                mode="r:",
                tarinfo=_BoundedTarInfo,
                encoding="utf-8",
                errors="surrogateescape",
            ) as archive:
                yield archive
        except source_tool.SourceToolError:
            raise
        except (
            EOFError,
            OSError,
            OverflowError,
            RecursionError,
            UnicodeError,
            ValueError,
            lzma.LZMAError,
            tarfile.TarError,
        ) as error:
            raise source_tool.SourceToolError(
                f"cannot read bounded TAR archive for {source_id}: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
    finally:
        if decompressed is not archive_stream:
            decompressed.close()


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _expected_permission_mode(requested_mode: int, kind: str) -> int | None:
    requested_mode &= 0o777
    if os.name == "nt":
        # Windows derives stat execute bits partly from filename suffix and only
        # approximates POSIX chmod. portable-copy is inspection-only, so its
        # receipt records observed modes without claiming archive-mode parity.
        return None
    return requested_mode


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _portable_component(component: str, location: str) -> None:
    try:
        component.encode("utf-8")
    except UnicodeEncodeError:
        _integrity(f"{location} contains a non-UTF-8 path component: {component!r}")
    if (
        not component
        or component in {".", ".."}
        or len(component) > 255
        or component.endswith((".", " "))
        or component.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        or ":" in component
        or any(character in '*?"<>|' for character in component)
        or any(ord(character) < 32 or ord(character) == 127 for character in component)
    ):
        _integrity(f"{location} contains a non-portable path component: {component!r}")


def _member_parts(name: str, location: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        _integrity(f"{location} is not a safe archive path: {name!r}")
    try:
        encoded_name = name.encode("utf-8")
    except UnicodeEncodeError:
        _integrity(f"{location} contains a non-UTF-8 archive path")
    if len(encoded_name) > MAX_ARCHIVE_PATH_BYTES:
        _integrity(
            f"{location} exceeds the {MAX_ARCHIVE_PATH_BYTES}-byte archive path limit"
        )
    raw_parts = name.split("/")
    if raw_parts[-1] == "":
        raw_parts.pop()
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        _integrity(f"{location} is not a canonical archive path: {name!r}")
    if len(raw_parts) > MAX_ARCHIVE_PATH_DEPTH:
        _integrity(
            f"{location} exceeds the {MAX_ARCHIVE_PATH_DEPTH}-component archive path limit"
        )
    for part in raw_parts:
        _portable_component(part, location)
    return tuple(raw_parts)


def _normalized_key(parts: tuple[str, ...]) -> str:
    return "/".join(unicodedata.normalize("NFKC", part).casefold() for part in parts)


def _resolve_symlink_target(path: PurePosixPath, link_name: str, location: str) -> PurePosixPath:
    if not link_name or "\\" in link_name or "\x00" in link_name or link_name.startswith("/"):
        _integrity(f"{location} has an unsafe symbolic-link target: {link_name!r}")
    try:
        encoded_target = link_name.encode("utf-8")
    except UnicodeEncodeError:
        _integrity(f"{location} contains a non-UTF-8 symbolic-link target")
    if len(encoded_target) > MAX_ARCHIVE_PATH_BYTES:
        _integrity(
            f"{location} exceeds the {MAX_ARCHIVE_PATH_BYTES}-byte symbolic-link target limit"
        )
    target_body = link_name[:-1] if link_name.endswith("/") else link_name
    raw_parts = target_body.split("/")
    if not target_body or any(part in {"", "."} for part in raw_parts):
        _integrity(f"{location} has a non-canonical symbolic-link target: {link_name!r}")
    resolved = list(path.parent.parts)
    for part in raw_parts:
        if part == "..":
            if not resolved:
                _integrity(f"{location} symbolic link escapes its archive root")
            resolved.pop()
            continue
        _portable_component(part, location)
        resolved.append(part)
    if not resolved:
        _integrity(f"{location} symbolic link resolves to the archive root")
    if len(resolved) > MAX_ARCHIVE_PATH_DEPTH:
        _integrity(
            f"{location} exceeds the {MAX_ARCHIVE_PATH_DEPTH}-component resolved link limit"
        )
    return PurePosixPath(*resolved)


def _classify_member(info: tarfile.TarInfo, location: str) -> str:
    if info.isdir():
        return "directory"
    if info.isreg():
        return "file"
    if info.issym():
        return "symlink"
    if info.islnk():
        return "hardlink"
    _integrity(f"{location} uses an unsupported archive entry type")


def _plan_tar(archive: tarfile.TarFile, source_id: str) -> tuple[str, list[PlannedMember]]:
    members: list[tarfile.TarInfo] = []
    for info in archive:
        members.append(info)
        if len(members) > MAX_ARCHIVE_MEMBERS:
            _integrity(f"archive for {source_id} exceeds {MAX_ARCHIVE_MEMBERS} entries")
    trailing_bytes = 0
    while True:
        trailing = archive.fileobj.read(1024 * 1024)
        if not trailing:
            break
        trailing_bytes += len(trailing)
        if any(trailing):
            _integrity(f"archive for {source_id} has non-zero data after its TAR end marker")
    if trailing_bytes < tarfile.BLOCKSIZE:
        _integrity(f"archive for {source_id} is missing its second TAR end marker")
    if not members:
        _integrity(f"archive for {source_id} is empty")

    root: str | None = None
    root_entry_seen = False
    planned: list[PlannedMember] = []
    entries: dict[PurePosixPath, PlannedMember] = {}
    normalized_paths: dict[str, PurePosixPath] = {}
    normalized_prefixes: dict[str, PurePosixPath] = {}
    total_size = 0

    for index, info in enumerate(members):
        location = f"archive {source_id} member[{index}]"
        if stat.S_IMODE(info.mode) & 0o7000:
            _integrity(f"{location} uses unsupported special permission bits")
        if info.name.endswith("/") and not info.isdir():
            _integrity(f"{location} uses directory spelling for a non-directory entry")
        parts = _member_parts(info.name, location)
        if root is None:
            root = parts[0]
        elif parts[0] != root:
            _integrity(f"archive for {source_id} must have exactly one top-level directory")

        if len(parts) == 1:
            if not info.isdir():
                _integrity(f"archive root for {source_id} must be a directory")
            if root_entry_seen:
                _integrity(f"archive for {source_id} repeats its top-level directory")
            root_entry_seen = True
            continue

        relative_parts = parts[1:]
        path = PurePosixPath(*relative_parts)
        kind = _classify_member(info, location)
        if info.sparse is not None:
            _integrity(f"{location} uses unsupported sparse-file metadata")
        if kind == "file":
            if info.size < 0 or info.size > MAX_MEMBER_BYTES:
                _integrity(f"{location} has an invalid or excessive file size")
            total_size += info.size
            if total_size > MAX_ARCHIVE_BYTES:
                _integrity(f"archive for {source_id} exceeds the extraction size limit")

        if path in entries:
            _integrity(f"archive for {source_id} repeats path {path.as_posix()}")
        normalized = _normalized_key(relative_parts)
        previous = normalized_paths.get(normalized)
        if previous is not None and previous != path:
            _integrity(
                f"archive for {source_id} has a case/Unicode path collision: "
                f"{previous.as_posix()} and {path.as_posix()}"
            )
        normalized_paths[normalized] = path
        for prefix_length in range(1, len(relative_parts) + 1):
            prefix_parts = relative_parts[:prefix_length]
            prefix = PurePosixPath(*prefix_parts)
            prefix_key = _normalized_key(prefix_parts)
            previous_prefix = normalized_prefixes.get(prefix_key)
            if previous_prefix is not None and previous_prefix != prefix:
                _integrity(
                    f"archive for {source_id} has a case/Unicode prefix collision: "
                    f"{previous_prefix.as_posix()} and {prefix.as_posix()}"
                )
            normalized_prefixes[prefix_key] = prefix

        link_target: PurePosixPath | None = None
        if kind == "symlink":
            link_target = _resolve_symlink_target(path, info.linkname, location)
        elif kind == "hardlink":
            if info.linkname.endswith("/"):
                _integrity(f"{location} hard-link target must name a regular file")
            target_parts = _member_parts(info.linkname, f"{location} hard-link target")
            if target_parts[0] != root or len(target_parts) == 1:
                _integrity(f"{location} hard link escapes its archive root")
            link_target = PurePosixPath(*target_parts[1:])

        member = PlannedMember(info=info, path=path, kind=kind, link_target=link_target)
        entries[path] = member
        planned.append(member)

    if root is None:
        _integrity(f"archive for {source_id} does not contain a top-level directory prefix")

    for member in planned:
        for parent in member.path.parents:
            if parent == PurePosixPath("."):
                break
            ancestor = entries.get(parent)
            if ancestor is not None and ancestor.kind != "directory":
                _integrity(
                    f"archive for {source_id} places {member.path.as_posix()} below "
                    f"non-directory {parent.as_posix()}"
                )
        if member.link_target is not None:
            target_key = _normalized_key(tuple(member.link_target.parts))
            normalized_target = normalized_prefixes.get(target_key)
            if normalized_target is not None and normalized_target != member.link_target:
                _integrity(
                    f"archive for {source_id} link {member.path.as_posix()} has an "
                    f"ambiguous case/Unicode target: {member.link_target.as_posix()} "
                    f"versus {normalized_target.as_posix()}"
                )
            target_member = entries.get(member.link_target)
            if (
                member.kind == "symlink"
                and member.info.linkname.endswith("/")
                and target_member is not None
                and target_member.kind != "directory"
            ):
                _integrity(
                    f"archive for {source_id} symbolic link {member.path.as_posix()} "
                    "uses a directory target spelling for a non-directory entry"
                )
        if member.kind == "hardlink":
            target = entries.get(member.link_target)
            if target is None or target.kind != "file":
                _integrity(
                    f"archive for {source_id} hard link {member.path.as_posix()} "
                    "must reference a regular file in the same archive"
                )

    return root, planned


def _set_mode_and_time(path: Path, info: tarfile.TarInfo) -> None:
    try:
        path.chmod(info.mode & 0o755)
        try:
            os.utime(
                path,
                ns=(NORMALIZED_GENERATED_MTIME_NS, NORMALIZED_GENERATED_MTIME_NS),
                follow_symlinks=False,
            )
        except NotImplementedError:
            os.utime(
                path,
                ns=(NORMALIZED_GENERATED_MTIME_NS, NORMALIZED_GENERATED_MTIME_NS),
            )
    except (OSError, ValueError, OverflowError, NotImplementedError) as error:
        _integrity(f"cannot apply archive metadata to {path}: {error}")


def _set_symlink_time(path: Path) -> None:
    try:
        os.utime(
            path,
            ns=(NORMALIZED_GENERATED_MTIME_NS, NORMALIZED_GENERATED_MTIME_NS),
            follow_symlinks=False,
        )
    except (OSError, ValueError, OverflowError, NotImplementedError) as error:
        _integrity(f"cannot normalize symbolic-link timestamp for {path}: {error}")


def _copy_regular_file_no_replace(source: Path, destination: Path) -> None:
    descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(source, flags)
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            _integrity(f"portable link target is not a regular file: {source}")
        with os.fdopen(descriptor, "rb") as source_stream:
            descriptor = -1
            with destination.open("xb") as destination_stream:
                shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot copy portable link {source} to {destination}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _extract_tar(
    source: dict[str, object],
    archive_stream: BinaryIO,
    destination: Path,
    *,
    link_mode: str,
) -> None:
    source_id = str(source["id"])
    try:
        with _open_bounded_tar(
            archive_stream,
            source_id,
            int(source["size"]),
        ) as archive:
            _, planned = _plan_tar(archive, source_id)
            planned_by_path = {member.path: member for member in planned}

            directories = sorted(
                (member for member in planned if member.kind == "directory"),
                key=lambda member: (len(member.path.parts), member.path.as_posix()),
            )
            # Keep regular files in archive order so compressed readers only
            # rewind once after planning instead of seeking backwards per path.
            files = [member for member in planned if member.kind == "file"]
            hardlinks = sorted(
                (member for member in planned if member.kind == "hardlink"),
                key=lambda member: member.path.as_posix(),
            )
            symlinks = sorted(
                (member for member in planned if member.kind == "symlink"),
                key=lambda member: member.path.as_posix(),
            )

            for member in directories:
                (destination / Path(*member.path.parts)).mkdir(parents=True, exist_ok=True)
            for member in files:
                output = destination / Path(*member.path.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member.info)
                if stream is None:
                    _integrity(
                        f"cannot read regular file {member.path.as_posix()} from {source_id}"
                    )
                written = 0
                with stream, output.open("xb") as target:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > member.info.size:
                            _integrity(
                                f"archive member {member.path.as_posix()} exceeded its stated size"
                            )
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if written != member.info.size:
                    _integrity(
                        f"archive member {member.path.as_posix()} size mismatch: "
                        f"expected {member.info.size}, got {written}"
                    )
                _set_mode_and_time(output, member.info)

            for member in hardlinks:
                output = destination / Path(*member.path.parts)
                target = destination / Path(*member.link_target.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                if link_mode == "preserve":
                    os.link(target, output)
                else:
                    _copy_regular_file_no_replace(target, output)
                    target_member = planned_by_path[member.link_target]
                    _set_mode_and_time(output, target_member.info)

            for member in symlinks:
                output = destination / Path(*member.path.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                target_member = planned_by_path.get(member.link_target)
                target_is_directory = member.info.linkname.endswith("/") or (
                    target_member is not None and target_member.kind == "directory"
                )
                if link_mode == "preserve":
                    os.symlink(
                        member.info.linkname,
                        output,
                        target_is_directory=target_is_directory,
                    )
                    _set_symlink_time(output)
                elif target_member is not None and target_member.kind == "file":
                    target = destination / Path(*member.link_target.parts)
                    _copy_regular_file_no_replace(target, output)
                    _set_mode_and_time(output, target_member.info)
                elif target_is_directory:
                    target = destination / Path(*member.link_target.parts)
                    if _lexists(target) and (
                        target.is_symlink() or not target.is_dir() or any(target.iterdir())
                    ):
                        _integrity(
                            f"portable link mode cannot copy non-empty directory target "
                            f"for {member.path.as_posix()}"
                        )
                    output.mkdir()
                else:
                    _integrity(
                        f"portable link mode cannot copy dangling file target "
                        f"for {member.path.as_posix()}"
                    )

            for member in sorted(
                directories,
                key=lambda item: (-len(item.path.parts), item.path.as_posix()),
            ):
                _set_mode_and_time(destination / Path(*member.path.parts), member.info)
    except source_tool.SourceToolError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise source_tool.SourceToolError(
            f"cannot extract archive for {source_id}: {error}",
            source_tool.EXIT_INTEGRITY,
        ) from error


def _source_depth(source: dict[str, object], by_id: dict[str, dict[str, object]]) -> int:
    parent = source.get("parent")
    if parent is None:
        return 0
    return 1 + _source_depth(by_id[str(parent)], by_id)


def plan_destinations(sources: list[dict[str, object]]) -> dict[str, PurePosixPath]:
    by_id = {str(source["id"]): source for source in sources}
    anchor = by_id.get(ROOT_SOURCE_ID)
    if anchor is None or "parent" in anchor:
        _schema(f"source manifest must contain top-level {ROOT_SOURCE_ID}")

    destinations: dict[str, PurePosixPath] = {ROOT_SOURCE_ID: PurePosixPath(".")}

    def resolve(source_id: str) -> PurePosixPath:
        existing = destinations.get(source_id)
        if existing is not None:
            return existing
        source = by_id[source_id]
        parent = source.get("parent")
        if parent is not None:
            destination = resolve(str(parent)) / str(source["destination"])
        elif source_id == SINGLE_FILE_SOURCE_ID:
            destination = PurePosixPath("buildscripts/sdk/bin/gas-preprocessor.pl")
        else:
            directory = TOP_LEVEL_ALIASES.get(source_id, source_id)
            destination = PurePosixPath("buildscripts/deps") / directory
        if len(destination.parts) > MAX_ARCHIVE_PATH_DEPTH:
            _schema(
                f"materialization destination for {source_id} exceeds the "
                f"{MAX_ARCHIVE_PATH_DEPTH}-component path limit"
            )
        destinations[source_id] = destination
        return destination

    for source_id in by_id:
        resolve(source_id)

    exact_paths: dict[str, str] = {}
    for source_id, destination in destinations.items():
        if source_id == ROOT_SOURCE_ID:
            continue
        key = _normalized_key(tuple(destination.parts))
        previous = exact_paths.get(key)
        if previous is not None:
            _schema(
                f"materialization destinations collide for {previous} and {source_id}: "
                f"{destination.as_posix()}"
            )
        exact_paths[key] = source_id

    for ancestor_id, ancestor in destinations.items():
        for descendant_id, descendant in destinations.items():
            if ancestor_id == descendant_id or ancestor == PurePosixPath("."):
                continue
            ancestor_parts = tuple(
                unicodedata.normalize("NFKC", part).casefold() for part in ancestor.parts
            )
            descendant_parts = tuple(
                unicodedata.normalize("NFKC", part).casefold() for part in descendant.parts
            )
            if (
                len(descendant_parts) <= len(ancestor_parts)
                or descendant_parts[: len(ancestor_parts)] != ancestor_parts
            ):
                continue
            current_id = descendant_id
            declared_chain: set[str] = set()
            while current_id in by_id and "parent" in by_id[current_id]:
                current_id = str(by_id[current_id]["parent"])
                declared_chain.add(current_id)
            if ancestor_id not in declared_chain:
                _schema(
                    f"materialization destination for {descendant_id} is nested below "
                    f"unrelated source {ancestor_id}: {descendant.as_posix()}"
                )
    return destinations


def _hash_exact_stream(stream: BinaryIO, expected_size: int, location: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            _integrity(f"{location} exceeded its stated size")
        digest.update(chunk)
    if size != expected_size:
        _integrity(f"{location} size mismatch: expected {expected_size}, got {size}")
    return size, digest.hexdigest()


def _archive_regular_file_proofs(
    archive: tarfile.TarFile,
    planned: list[PlannedMember],
    source_id: str,
) -> dict[tuple[str, PurePosixPath], FileProof]:
    proofs: dict[tuple[str, PurePosixPath], FileProof] = {}
    for member in planned:
        if member.kind != "file":
            continue
        stream = archive.extractfile(member.info)
        if stream is None:
            _integrity(
                f"cannot read regular file {member.path.as_posix()} from {source_id}"
            )
        with stream:
            size, digest = _hash_exact_stream(
                stream,
                member.info.size,
                f"archive member {source_id}:{member.path.as_posix()}",
            )
        proofs[(source_id, member.path)] = FileProof(
            size=size,
            sha256=digest,
            mode=_expected_permission_mode(member.info.mode & 0o755, "file"),
        )
    return proofs


def _scan_locked_archives(
    sources: list[dict[str, object]],
    cache: Path,
    destinations: dict[str, PurePosixPath],
) -> LockedArchiveScan:
    plans: dict[str, list[PlannedMember]] = {}
    regular_files: dict[tuple[str, PurePosixPath], FileProof] = {}
    output_types: dict[PurePosixPath, str] = {}
    normalized_outputs: dict[str, PurePosixPath] = {}
    total_bytes = 0

    def add_output(path: PurePosixPath, kind: str) -> None:
        if path == PurePosixPath("."):
            return
        normalized = _normalized_key(tuple(path.parts))
        previous_path = normalized_outputs.get(normalized)
        if previous_path is not None and previous_path != path:
            _integrity(
                f"locked sources have a cross-archive case/Unicode collision: "
                f"{previous_path.as_posix()} and {path.as_posix()}"
            )
        normalized_outputs[normalized] = path
        previous_kind = output_types.get(path)
        if previous_kind is not None and not (
            previous_kind == "directory" and kind == "directory"
        ):
            _integrity(
                f"locked sources collide at materialized path {path.as_posix()}"
            )
        output_types[path] = kind

    for source in sources:
        source_id = str(source["id"])
        destination = destinations[source_id]
        if source_id == SINGLE_FILE_SOURCE_ID:
            plans[source_id] = []
            archive_path = cache / str(source["archive"])
            with source_tool.open_verified_archive(source, archive_path) as archive_stream:
                size, digest = _hash_exact_stream(
                    archive_stream,
                    int(source["size"]),
                    f"single-file source {source_id}",
                )
            regular_files[(source_id, PurePosixPath("."))] = FileProof(
                size=size,
                sha256=digest,
                mode=_expected_permission_mode(0o755, "file"),
            )
            total_bytes += size
            add_output(destination, "file")
            for parent in destination.parents:
                if parent == PurePosixPath("."):
                    break
                add_output(parent, "directory")
            continue

        archive_path = cache / str(source["archive"])
        try:
            with source_tool.open_verified_archive(source, archive_path) as archive_stream:
                with _open_bounded_tar(
                    archive_stream,
                    source_id,
                    int(source["size"]),
                ) as archive:
                    _, planned = _plan_tar(archive, source_id)
                    regular_files.update(
                        _archive_regular_file_proofs(archive, planned, source_id)
                    )
        except source_tool.SourceToolError:
            raise
        except (OSError, tarfile.TarError) as error:
            raise source_tool.SourceToolError(
                f"cannot pre-scan archive for {source_id}: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error

        plans[source_id] = planned
        by_path = {member.path: member for member in planned}
        source_bytes = sum(
            member.info.size for member in planned if member.kind == "file"
        )
        for member in planned:
            if member.kind not in {"hardlink", "symlink"}:
                continue
            target_member = by_path.get(member.link_target)
            if target_member is not None and target_member.kind == "file":
                source_bytes += target_member.info.size
        if source_bytes > MAX_ARCHIVE_BYTES:
            _integrity(
                f"archive for {source_id} exceeds the expanded link-copy byte limit"
            )
        if source_bytes > int(source["size"]) * MAX_ARCHIVE_EXPANSION_RATIO:
            _integrity(
                f"archive for {source_id} exceeds the {MAX_ARCHIVE_EXPANSION_RATIO}x "
                "locked expansion-ratio limit"
            )
        total_bytes += source_bytes
        add_output(destination, "directory")
        for member in planned:
            output = destination / member.path
            add_output(output, member.kind)
            for parent in output.parents:
                if parent == PurePosixPath("."):
                    break
                add_output(parent, "directory")

    for path, kind in output_types.items():
        for parent in path.parents:
            if parent == PurePosixPath("."):
                break
            parent_kind = output_types.get(parent)
            if parent_kind is not None and parent_kind != "directory":
                _integrity(
                    f"locked source path {path.as_posix()} is nested below "
                    f"non-directory {parent.as_posix()}"
                )
        if kind == "symlink" and path == PurePosixPath(RECEIPT_NAME):
            _integrity("locked source collides with the materialization receipt")

    receipt_key = _normalized_key((RECEIPT_NAME,))
    if receipt_key in normalized_outputs:
        _integrity("locked source collides with the materialization receipt")
    if len(output_types) > MAX_TREE_ENTRIES:
        _integrity(
            f"locked source closure exceeds {MAX_TREE_ENTRIES} materialized entries"
        )
    if total_bytes > MAX_TREE_BYTES:
        _integrity("locked source closure exceeds the materialized tree byte limit")
    return LockedArchiveScan(plans=plans, regular_files=regular_files)


def _expected_materialized_entries(
    sources: list[dict[str, object]],
    destinations: dict[str, PurePosixPath],
    scan: LockedArchiveScan,
    link_mode: str,
) -> dict[PurePosixPath, ExpectedEntry]:
    expected: dict[PurePosixPath, ExpectedEntry] = {}

    def record(path: PurePosixPath, entry: ExpectedEntry) -> None:
        if path == PurePosixPath("."):
            return
        previous = expected.get(path)
        if previous is not None and previous != entry:
            _integrity(
                f"locked sources have inconsistent expectations at {path.as_posix()}"
            )
        expected[path] = entry

    def add(path: PurePosixPath, entry: ExpectedEntry) -> None:
        for parent in reversed(path.parents):
            if parent != PurePosixPath("."):
                record(
                    parent,
                    ExpectedEntry(
                        kind="directory",
                        mode=_expected_permission_mode(0o755, "directory"),
                    ),
                )
        record(path, entry)

    for source in sources:
        source_id = str(source["id"])
        destination = destinations[source_id]
        if source_id == SINGLE_FILE_SOURCE_ID:
            proof = scan.regular_files[(source_id, PurePosixPath("."))]
            add(destination, ExpectedEntry(kind="file", mode=proof.mode, file=proof))
            continue

        add(
            destination,
            ExpectedEntry(
                kind="directory",
                mode=_expected_permission_mode(0o755, "directory"),
            ),
        )
        planned = scan.plans[source_id]
        by_path = {member.path: member for member in planned}
        for member in planned:
            output = destination / member.path
            if member.kind == "directory":
                entry = ExpectedEntry(
                    kind="directory",
                    mode=_expected_permission_mode(0o755, "directory"),
                )
            elif member.kind == "file":
                proof = scan.regular_files[(source_id, member.path)]
                entry = ExpectedEntry(kind="file", mode=proof.mode, file=proof)
            elif member.kind == "hardlink":
                proof = scan.regular_files[(source_id, member.link_target)]
                entry = ExpectedEntry(kind="file", mode=proof.mode, file=proof)
            elif link_mode == "preserve":
                entry = ExpectedEntry(kind="symlink")
            else:
                target_member = by_path.get(member.link_target)
                if target_member is not None and target_member.kind == "file":
                    proof = scan.regular_files[(source_id, member.link_target)]
                    entry = ExpectedEntry(kind="file", mode=proof.mode, file=proof)
                elif member.info.linkname.endswith("/") or (
                    target_member is not None and target_member.kind == "directory"
                ):
                    entry = ExpectedEntry(
                        kind="directory",
                        mode=_expected_permission_mode(0o755, "directory"),
                    )
                else:
                    _integrity(
                        f"portable materialization cannot represent symbolic link "
                        f"{source_id}:{member.path.as_posix()}"
                    )
            add(output, entry)
    return expected


def _regular_file_fingerprint(path: Path) -> tuple[tuple[int, int], int, str]:
    descriptor = -1
    try:
        path_stat = path.lstat()
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or path.is_symlink()
            or (
                getattr(path_stat, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
        ):
            _integrity(f"expected a regular materialized file: {path}")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or (
                getattr(opened_stat, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            _integrity(f"materialized file changed while opening: {path}")
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        current_stat = path.lstat()
        if (
            current_stat.st_dev,
            current_stat.st_ino,
            current_stat.st_size,
        ) != (opened_stat.st_dev, opened_stat.st_ino, size):
            _integrity(f"materialized file changed while hashing: {path}")
        return (opened_stat.st_dev, opened_stat.st_ino), size, digest.hexdigest()
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot inspect materialized file {path}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_locked_link_topology(
    plans: dict[str, list[PlannedMember]],
    destinations: dict[str, PurePosixPath],
    workspace: Path,
    link_mode: str,
) -> int:
    expected_symlink_count = 0
    for source_id, planned in plans.items():
        destination = workspace / Path(*destinations[source_id].parts)
        by_path = {member.path: member for member in planned}
        for member in planned:
            if member.kind not in {"hardlink", "symlink"}:
                continue
            output = destination / Path(*member.path.parts)
            target = destination / Path(*member.link_target.parts)
            if member.kind == "hardlink":
                output_identity, output_size, output_digest = _regular_file_fingerprint(output)
                target_identity, target_size, target_digest = _regular_file_fingerprint(target)
                if (output_size, output_digest) != (target_size, target_digest):
                    _integrity(
                        f"materialized hard link content differs from its target: {output}"
                    )
                if link_mode == "preserve" and output_identity != target_identity:
                    _integrity(f"canonical materialization lost hard-link topology: {output}")
                if link_mode == "portable-copy" and output_identity == target_identity:
                    _integrity(f"portable materialization retained a hard link: {output}")
                continue

            target_member = by_path.get(member.link_target)
            target_is_directory = member.info.linkname.endswith("/") or (
                target_member is not None and target_member.kind == "directory"
            )
            if link_mode == "preserve":
                try:
                    output_stat = output.lstat()
                except OSError as error:
                    _integrity(f"cannot inspect materialized symbolic link {output}: {error}")
                if not stat.S_ISLNK(output_stat.st_mode):
                    _integrity(f"canonical materialization lost symbolic link: {output}")
                try:
                    actual_target = os.readlink(output)
                except OSError as error:
                    _integrity(f"cannot read materialized symbolic link {output}: {error}")
                if actual_target != member.info.linkname:
                    _integrity(
                        f"materialized symbolic-link target differs at {output}: "
                        f"expected {member.info.linkname!r}, got {actual_target!r}"
                    )
                expected_symlink_count += 1
            elif target_member is not None and target_member.kind == "file":
                _, output_size, output_digest = _regular_file_fingerprint(output)
                _, target_size, target_digest = _regular_file_fingerprint(target)
                if (output_size, output_digest) != (target_size, target_digest):
                    _integrity(
                        f"portable symbolic-link copy differs from its target: {output}"
                    )
            elif target_is_directory:
                try:
                    output_stat = output.lstat()
                    is_empty = not any(output.iterdir())
                except OSError as error:
                    _integrity(f"cannot inspect portable directory-link copy {output}: {error}")
                if (
                    not stat.S_ISDIR(output_stat.st_mode)
                    or output.is_symlink()
                    or (
                        getattr(output_stat, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                    )
                    or not is_empty
                ):
                    _integrity(
                        f"portable directory-link copy must be an empty real directory: {output}"
                    )
            else:
                _integrity(
                    f"portable materialization cannot represent symbolic link {output}"
                )
    return expected_symlink_count


def _ensure_no_symlink_ancestor(workspace: Path, target: Path) -> None:
    try:
        relative = target.relative_to(workspace)
    except ValueError:
        _integrity(f"materialization target escapes workspace: {target}")
    current = workspace
    for index, part in enumerate(relative.parts):
        if not _lexists(current):
            return
        if not current.is_dir():
            _integrity(f"materialization target traverses non-directory: {current}")
        wanted = unicodedata.normalize("NFKC", part).casefold()
        for child in current.iterdir():
            if (
                unicodedata.normalize("NFKC", child.name).casefold() == wanted
                and child.name != part
            ):
                _integrity(
                    f"materialization target has a case/Unicode collision: "
                    f"{child.name} and {part} below {current}"
                )
        current /= part
        if _lexists(current):
            if current.is_symlink():
                _integrity(f"materialization target traverses symbolic link: {current}")
            if index + 1 < len(relative.parts) and not current.is_dir():
                _integrity(f"materialization target traverses non-directory: {current}")


def _prepare_directory(workspace: Path, destination: Path, *, reuse_empty: bool) -> None:
    if destination != workspace:
        _ensure_no_symlink_ancestor(workspace, destination)
    if _lexists(destination):
        if destination.is_symlink() or not destination.is_dir():
            _integrity(f"materialization destination is not a real directory: {destination}")
        if any(destination.iterdir()):
            _integrity(f"materialization destination is not empty: {destination}")
        if not reuse_empty:
            destination.rmdir()
    destination.mkdir(parents=True, exist_ok=reuse_empty)


def _verify_license_paths(
    source: dict[str, object],
    source_destination: Path,
    *,
    single_file: bool,
) -> None:
    for license_path in source["licenseFiles"]:
        if single_file:
            candidate = source_destination if str(license_path) == source_destination.name else None
        else:
            candidate = source_destination / Path(*PurePosixPath(str(license_path)).parts)
        if (
            candidate is None
            or not _lexists(candidate)
            or candidate.is_symlink()
            or not candidate.is_file()
        ):
            _integrity(
                f"materialized source {source['id']} is missing regular license file "
                f"{license_path}"
            )


def _copy_single_file(
    source: dict[str, object], archive_stream: BinaryIO, destination: Path
) -> None:
    if str(source["id"]) != SINGLE_FILE_SOURCE_ID:
        _schema(f"unsupported non-tar source: {source['id']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _lexists(destination):
        _integrity(f"single-file destination already exists: {destination}")
    with destination.open("xb") as output:
        shutil.copyfileobj(archive_stream, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    destination.chmod(0o755)
    os.utime(
        destination,
        ns=(NORMALIZED_GENERATED_MTIME_NS, NORMALIZED_GENERATED_MTIME_NS),
    )


def _normalize_workspace_directories(workspace: Path) -> None:
    directories: list[Path] = []
    for current, names, _ in os.walk(workspace, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.append(current_path)
        names[:] = [name for name in names if not (current_path / name).is_symlink()]
    try:
        for directory in reversed(directories):
            directory.chmod(0o755)
            os.utime(
                directory,
                ns=(NORMALIZED_GENERATED_MTIME_NS, NORMALIZED_GENERATED_MTIME_NS),
            )
    except OSError as error:
        _integrity(f"cannot normalize generated workspace metadata: {error}")


def _normalize_receipt_metadata(receipt: Path) -> None:
    try:
        receipt.chmod(0o644)
        os.utime(
            receipt,
            ns=(NORMALIZED_GENERATED_MTIME_NS, NORMALIZED_GENERATED_MTIME_NS),
        )
        os.utime(
            receipt.parent,
            ns=(NORMALIZED_GENERATED_MTIME_NS, NORMALIZED_GENERATED_MTIME_NS),
        )
    except OSError as error:
        _integrity(f"cannot normalize materialization receipt metadata: {error}")


def _digest_record(digest: object, tag: bytes, value: bytes) -> None:
    digest.update(tag)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _checked_permission_mode(st_mode: int, location: object) -> int:
    permission_mode = stat.S_IMODE(st_mode)
    if permission_mode & 0o7000:
        _integrity(f"materialized path has unsupported special permission bits: {location}")
    return permission_mode & 0o777


def _require_real_workspace_root(workspace: Path) -> os.stat_result:
    try:
        root_stat = workspace.lstat()
    except OSError as error:
        _integrity(f"cannot inspect materialized workspace {workspace}: {error}")
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or workspace.is_symlink()
        or (
            getattr(root_stat, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    ):
        _integrity(f"materialized workspace must be a real directory: {workspace}")
    return root_stat


def _tree_digest(
    workspace: Path,
    expected_entries: dict[PurePosixPath, ExpectedEntry] | None = None,
) -> dict[str, object]:
    root_stat = _require_real_workspace_root(workspace)
    root_mode = _checked_permission_mode(root_stat.st_mode, workspace)
    expected_root_mode = _expected_permission_mode(0o755, "directory")
    if (
        expected_entries is not None
        and expected_root_mode is not None
        and root_mode != expected_root_mode
    ):
        _integrity(
            f"materialized workspace root mode mismatch: expected "
            f"{expected_root_mode:04o}, got {root_mode:04o}"
        )
    if root_stat.st_mtime_ns != NORMALIZED_GENERATED_MTIME_NS:
        _integrity("materialized workspace root has a non-normalized timestamp")

    digest = hashlib.sha256()
    digest.update(TREE_DIGEST_FORMAT.encode("ascii") + b"\0")
    digest.update(root_mode.to_bytes(2, "big"))
    counts = {"entryCount": 0, "fileCount": 0, "directoryCount": 0, "symlinkCount": 0}
    total_file_bytes = 0
    seen_expected: set[PurePosixPath] = set()

    def walk(directory: Path, relative_directory: PurePosixPath) -> None:
        nonlocal total_file_bytes
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name.encode("utf-8"))
        except (OSError, UnicodeError) as error:
            _integrity(f"cannot enumerate materialized directory {directory}: {error}")
        normalized_names: dict[str, str] = {}
        for child in children:
            if relative_directory == PurePosixPath(".") and child.name == RECEIPT_NAME:
                try:
                    receipt_stat = child.lstat()
                except OSError as error:
                    _integrity(f"cannot inspect materialization receipt {child}: {error}")
                if (
                    not stat.S_ISREG(receipt_stat.st_mode)
                    or child.is_symlink()
                    or (
                        getattr(receipt_stat, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                    )
                ):
                    _integrity(f"materialization receipt is not a regular file: {child}")
                continue
            _portable_component(child.name, f"materialized path below {directory}")
            normalized_name = unicodedata.normalize("NFKC", child.name).casefold()
            previous_name = normalized_names.get(normalized_name)
            if previous_name is not None and previous_name != child.name:
                _integrity(
                    f"materialized directory has a case/Unicode collision: "
                    f"{previous_name} and {child.name} below {directory}"
                )
            normalized_names[normalized_name] = child.name
            relative = (
                PurePosixPath(child.name)
                if relative_directory == PurePosixPath(".")
                else relative_directory / child.name
            )
            path_bytes = relative.as_posix().encode("utf-8")
            try:
                child_stat = child.lstat()
            except OSError as error:
                _integrity(f"cannot inspect materialized path {child}: {error}")
            if (
                getattr(child_stat, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                and not stat.S_ISLNK(child_stat.st_mode)
            ):
                _integrity(f"materialized tree contains unsupported reparse point: {child}")
            permission_mode = _checked_permission_mode(child_stat.st_mode, child)
            if child_stat.st_mtime_ns != NORMALIZED_GENERATED_MTIME_NS:
                _integrity(f"materialized path has a non-normalized timestamp: {child}")

            if stat.S_ISDIR(child_stat.st_mode):
                actual_kind = "directory"
            elif stat.S_ISLNK(child_stat.st_mode):
                actual_kind = "symlink"
            elif stat.S_ISREG(child_stat.st_mode):
                actual_kind = "file"
            else:
                _integrity(f"materialized tree contains unsupported entry type: {child}")
            expected_entry = (
                expected_entries.get(relative) if expected_entries is not None else None
            )
            if expected_entries is not None:
                if expected_entry is None:
                    _integrity(
                        f"materialized workspace contains an unlocked entry: "
                        f"{relative.as_posix()}"
                    )
                if expected_entry.kind != actual_kind:
                    _integrity(
                        f"materialized entry type mismatch at {relative.as_posix()}: "
                        f"expected {expected_entry.kind}, got {actual_kind}"
                    )
                if expected_entry.mode is not None and expected_entry.mode != permission_mode:
                    _integrity(
                        f"materialized entry mode mismatch at {relative.as_posix()}: "
                        f"expected {expected_entry.mode:04o}, got {permission_mode:04o}"
                    )
                seen_expected.add(relative)

            counts["entryCount"] += 1
            if counts["entryCount"] > MAX_TREE_ENTRIES:
                _integrity(
                    f"materialized workspace exceeds {MAX_TREE_ENTRIES} tree entries"
                )
            if actual_kind == "directory":
                counts["directoryCount"] += 1
                _digest_record(digest, b"D", path_bytes)
                digest.update(permission_mode.to_bytes(2, "big"))
                walk(child, relative)
                continue
            if actual_kind == "symlink":
                counts["symlinkCount"] += 1
                try:
                    link_target_text = os.readlink(child)
                    resolved_target = _resolve_symlink_target(
                        relative,
                        link_target_text,
                        f"materialized symbolic link {relative.as_posix()}",
                    )
                    if link_target_text.endswith("/"):
                        target_path = workspace / Path(*resolved_target.parts)
                        if _lexists(target_path) and not stat.S_ISDIR(
                            target_path.lstat().st_mode
                        ):
                            _integrity(
                                f"materialized symbolic link {relative.as_posix()} "
                                "uses a directory target spelling for a non-directory entry"
                            )
                    link_target = link_target_text.encode("utf-8")
                except (OSError, UnicodeError) as error:
                    _integrity(f"cannot read materialized symbolic link {child}: {error}")
                _digest_record(digest, b"L", path_bytes)
                digest.update(permission_mode.to_bytes(2, "big"))
                _digest_record(digest, b"T", link_target)
                continue
            counts["fileCount"] += 1
            total_file_bytes += child_stat.st_size
            if total_file_bytes > MAX_TREE_BYTES:
                _integrity("materialized workspace exceeds the tree byte limit")
            _digest_record(digest, b"F", path_bytes)
            digest.update(permission_mode.to_bytes(2, "big"))
            digest.update(child_stat.st_size.to_bytes(8, "big"))
            if expected_entry is not None and expected_entry.file is not None:
                if child_stat.st_size != expected_entry.file.size:
                    _integrity(
                        f"materialized file size mismatch at {relative.as_posix()}: "
                        f"expected {expected_entry.file.size}, got {child_stat.st_size}"
                    )
                file_digest = hashlib.sha256()
            else:
                file_digest = None
            descriptor = -1
            try:
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
                descriptor = os.open(child, flags)
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or (
                        getattr(opened_stat, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                    )
                    or (opened_stat.st_dev, opened_stat.st_ino)
                    != (child_stat.st_dev, child_stat.st_ino)
                    or opened_stat.st_size != child_stat.st_size
                ):
                    _integrity(f"materialized file changed while hashing: {child}")
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        if file_digest is not None:
                            file_digest.update(chunk)
                current_stat = child.lstat()
                if (
                    current_stat.st_dev,
                    current_stat.st_ino,
                    current_stat.st_size,
                ) != (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_size):
                    _integrity(f"materialized file changed while hashing: {child}")
                if (
                    getattr(current_stat, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                ):
                    _integrity(f"materialized file became a reparse point: {child}")
            except source_tool.SourceToolError:
                raise
            except OSError as error:
                _integrity(f"cannot hash materialized file {child}: {error}")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if (
                file_digest is not None
                and expected_entry is not None
                and expected_entry.file is not None
                and file_digest.hexdigest() != expected_entry.file.sha256
            ):
                _integrity(
                    f"materialized file content mismatch at {relative.as_posix()}"
                )

    walk(workspace, PurePosixPath("."))
    if expected_entries is not None and seen_expected != set(expected_entries):
        missing = sorted(
            set(expected_entries) - seen_expected,
            key=lambda path: path.as_posix().encode("utf-8"),
        )
        _integrity(
            f"materialized workspace is missing locked entry: {missing[0].as_posix()}"
        )
    return {
        "format": TREE_DIGEST_FORMAT,
        "sha256": digest.hexdigest(),
        **counts,
    }


def _remove_tree(path: Path, expected_identity: tuple[int, int] | None = None) -> str | None:
    def make_writable_and_retry(function: object, failed_path: str, exc_info: object) -> None:
        try:
            os.chmod(failed_path, stat.S_IWRITE | stat.S_IREAD)
            function(failed_path)
        except OSError:
            raise exc_info[1]

    try:
        if _lexists(path):
            path_stat = path.lstat()
            if (
                expected_identity is not None
                and (path_stat.st_dev, path_stat.st_ino) != expected_identity
            ):
                return "staging path identity changed; refusing recursive cleanup"
            if path.is_symlink() or not stat.S_ISDIR(path_stat.st_mode):
                return "staging path is no longer a real directory; refusing cleanup"
            shutil.rmtree(path, onerror=make_writable_and_retry)
    except OSError as error:
        return str(error)
    return None


def _manifest_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise source_tool.SourceToolError(
            f"cannot hash manifest {path}: {error}", source_tool.EXIT_SCHEMA
        ) from error


def _receipt_data(
    data: dict[str, object],
    sources: list[dict[str, object]],
    destinations: dict[str, PurePosixPath],
    manifest_digest: str,
    link_mode: str,
    tree: dict[str, object],
) -> dict[str, object]:
    project = data["project"]
    return {
        "schemaVersion": 1,
        "manifestSha256": manifest_digest,
        "linkMode": link_mode,
        "project": {
            "upstreamRelease": project["upstreamRelease"],
            "upstreamRevision": project["upstreamRevision"],
            "nativeApi": project["nativeApi"],
            "abis": project["abis"],
            "pageSizeBytes": project["pageSizeBytes"],
        },
        "sources": [
            {
                "id": source["id"],
                "revision": source["revision"],
                "archive": source["archive"],
                "sha256": source["sha256"],
                "destination": destinations[str(source["id"])].as_posix(),
            }
            for source in sources
        ],
        "tree": tree,
    }


def _json_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _integrity(f"materialization receipt repeats JSON key: {key}")
        result[key] = value
    return result


def _read_materialization_receipt(receipt_path: Path) -> dict[str, object]:
    descriptor = -1
    try:
        path_stat = receipt_path.lstat()
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or receipt_path.is_symlink()
            or path_stat.st_size > MAX_RECEIPT_BYTES
            or (
                getattr(path_stat, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
        ):
            _integrity(f"materialization receipt is not a bounded regular file: {receipt_path}")
        if path_stat.st_mtime_ns != NORMALIZED_GENERATED_MTIME_NS:
            _integrity("materialization receipt has a non-normalized timestamp")
        expected_mode = 0o666 if os.name == "nt" else 0o644
        if path_stat.st_mode & 0o777 != expected_mode:
            _integrity("materialization receipt has non-normalized permissions")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(receipt_path, flags)
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_size > MAX_RECEIPT_BYTES
            or (
                getattr(opened_stat, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            _integrity(f"materialization receipt changed while opening: {receipt_path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw_receipt = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(raw_receipt) > MAX_RECEIPT_BYTES:
            _integrity(f"materialization receipt exceeds {MAX_RECEIPT_BYTES} bytes")
        current_stat = receipt_path.lstat()
        if (
            current_stat.st_dev,
            current_stat.st_ino,
            current_stat.st_size,
        ) != (opened_stat.st_dev, opened_stat.st_ino, len(raw_receipt)) or (
            getattr(current_stat, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            _integrity(f"materialization receipt changed while reading: {receipt_path}")
        receipt = json.loads(
            raw_receipt.decode("utf-8"),
            object_pairs_hook=_json_without_duplicate_keys,
        )
    except source_tool.SourceToolError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _integrity(f"cannot read materialization receipt {receipt_path}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(receipt, dict):
        _integrity("materialization receipt root must be a JSON object")
    return receipt


def _validate_receipt_shape(receipt: dict[str, object]) -> None:
    if set(receipt) != {
        "schemaVersion",
        "manifestSha256",
        "linkMode",
        "project",
        "sources",
        "tree",
    }:
        _integrity("materialization receipt has unexpected or missing top-level fields")
    if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1:
        _integrity("materialization receipt has an unsupported schema version")
    if not isinstance(receipt["manifestSha256"], str) or not source_tool.SHA256_RE.fullmatch(
        receipt["manifestSha256"]
    ):
        _integrity("materialization receipt has an invalid manifest digest")
    if not isinstance(receipt["linkMode"], str) or receipt["linkMode"] not in {
        "preserve",
        "portable-copy",
    }:
        _integrity("materialization receipt has an unsupported link mode")

    project = receipt["project"]
    if not isinstance(project, dict) or set(project) != {
        "upstreamRelease",
        "upstreamRevision",
        "nativeApi",
        "abis",
        "pageSizeBytes",
    }:
        _integrity("materialization receipt has an invalid project record")
    if not isinstance(project["upstreamRelease"], str) or not isinstance(
        project["upstreamRevision"], str
    ):
        _integrity("materialization receipt project identities must be strings")
    if (
        type(project["nativeApi"]) is not int
        or type(project["pageSizeBytes"]) is not int
        or not isinstance(project["abis"], list)
        or not all(isinstance(abi, str) for abi in project["abis"])
    ):
        _integrity("materialization receipt has invalid Android tuple types")

    sources = receipt["sources"]
    if not isinstance(sources, list):
        _integrity("materialization receipt sources must be an array")
    source_keys = {"id", "revision", "archive", "sha256", "destination"}
    for index, source in enumerate(sources):
        if (
            not isinstance(source, dict)
            or set(source) != source_keys
            or not all(isinstance(source[key], str) for key in source_keys)
            or not source_tool.SHA256_RE.fullmatch(source["sha256"])
        ):
            _integrity(f"materialization receipt source[{index}] is invalid")

    tree = receipt["tree"]
    tree_keys = {
        "format",
        "sha256",
        "entryCount",
        "fileCount",
        "directoryCount",
        "symlinkCount",
    }
    if not isinstance(tree, dict) or set(tree) != tree_keys:
        _integrity("materialization receipt has an invalid tree digest record")
    if tree["format"] != TREE_DIGEST_FORMAT or not isinstance(
        tree["sha256"], str
    ) or not source_tool.SHA256_RE.fullmatch(tree["sha256"]):
        _integrity("materialization receipt has an invalid tree digest")
    for key in ("entryCount", "fileCount", "directoryCount", "symlinkCount"):
        if type(tree[key]) is not int or tree[key] < 0:
            _integrity(f"materialization receipt tree.{key} must be a non-negative integer")
    if tree["entryCount"] != (
        tree["fileCount"] + tree["directoryCount"] + tree["symlinkCount"]
    ):
        _integrity("materialization receipt tree counts are inconsistent")


def verify_materialized_workspace(
    manifest_path: Path,
    cache: Path,
    workspace: Path,
    *,
    require_preserve: bool = True,
) -> None:
    workspace = Path(os.path.abspath(workspace))
    cache = cache.resolve(strict=False)
    try:
        workspace.relative_to(cache)
    except ValueError:
        pass
    else:
        _schema("workspace must not be inside the source cache")
    digest_before = _manifest_digest(manifest_path)
    data, sources = source_tool.load_manifest(manifest_path)
    destinations = plan_destinations(sources)
    source_tool.verify_cache(sources, cache)
    scan = _scan_locked_archives(sources, cache, destinations)
    digest_after = _manifest_digest(manifest_path)
    if digest_before != digest_after:
        _integrity("manifest changed while materialization receipt was being verified")

    _require_real_workspace_parent(workspace)
    _require_real_workspace_root(workspace)
    try:
        children = list(workspace.iterdir())
    except OSError as error:
        _integrity(f"cannot enumerate materialized workspace {workspace}: {error}")
    receipt_key = unicodedata.normalize("NFKC", RECEIPT_NAME).casefold()
    receipt_matches = [
        child
        for child in children
        if unicodedata.normalize("NFKC", child.name).casefold() == receipt_key
    ]
    if len(receipt_matches) != 1 or receipt_matches[0].name != RECEIPT_NAME:
        _integrity("materialized workspace must contain exactly one canonical receipt")
    receipt_path = receipt_matches[0]
    receipt = _read_materialization_receipt(receipt_path)
    _validate_receipt_shape(receipt)
    link_mode = receipt["linkMode"]
    if link_mode == "portable-copy" and _same_path(workspace, DEFAULT_WORKSPACE):
        _integrity("the canonical native workspace cannot use portable-copy mode")
    if require_preserve and link_mode != "preserve":
        _integrity("canonical native builds require a preserve-mode materialization receipt")
    expected_entries = _expected_materialized_entries(
        sources,
        destinations,
        scan,
        str(link_mode),
    )
    actual_tree = _tree_digest(workspace, expected_entries)
    expected_symlink_count = _verify_locked_link_topology(
        scan.plans,
        destinations,
        workspace,
        str(link_mode),
    )
    if actual_tree["symlinkCount"] != expected_symlink_count:
        _integrity(
            "materialized workspace symbolic-link count differs from the locked archives"
        )
    expected = _receipt_data(
        data,
        sources,
        destinations,
        digest_after,
        str(link_mode),
        actual_tree,
    )
    if receipt != expected:
        _integrity("materialization receipt does not match the manifest or workspace tree")
    print(f"verified materialized workspace: {workspace}")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _require_real_workspace_parent(workspace: Path, *, create: bool = False) -> Path:
    literal_parent = Path(os.path.abspath(workspace.parent))
    if not literal_parent.anchor:
        _schema("workspace parent must be absolute")

    current = Path(literal_parent.anchor)
    try:
        components = literal_parent.parts[1:]
        for component in components:
            current /= component
            if not _lexists(current):
                if not create:
                    _schema(f"workspace parent does not exist: {current}")
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
            current_stat = current.lstat()
            if (
                not stat.S_ISDIR(current_stat.st_mode)
                or current.is_symlink()
                or (
                    getattr(current_stat, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                )
            ):
                _schema(
                    "workspace parent must be a real directory without symlink "
                    f"or junction traversal: {current}"
                )
        resolved = literal_parent.resolve(strict=True)
    except OSError as error:
        raise source_tool.SourceToolError(
            f"cannot prepare workspace parent {literal_parent}: {error}",
            source_tool.EXIT_SCHEMA,
        ) from error
    if not resolved.is_dir() or not _same_path(resolved, literal_parent):
        _schema("workspace parent must be a real directory without symlink or junction traversal")
    return literal_parent


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        _integrity(f"cannot fsync materialization directory {path}: {error}")


def _fsync_workspace_directories(workspace: Path) -> None:
    if os.name != "posix":
        return
    directories: list[Path] = []
    for current, names, _ in os.walk(workspace, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.append(current_path)
        names[:] = [name for name in names if not (current_path / name).is_symlink()]
    for directory in reversed(directories):
        _fsync_directory(directory)


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory while refusing an existing destination."""
    if _lexists(destination):
        _integrity(f"workspace appeared during materialization: {destination}")
    try:
        if os.name == "nt":
            os.rename(source, destination)
            return
        if not sys.platform.startswith("linux"):
            _schema("canonical no-replace publication is supported only on Linux and Windows")
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
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            1,
        )
        if result == 0:
            return
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            _integrity(f"workspace appeared during materialization: {destination}")
        raise OSError(error_number, os.strerror(error_number), destination)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        if _lexists(destination):
            _integrity(f"workspace appeared during materialization: {destination}")
        raise source_tool.SourceToolError(
            f"cannot publish materialized workspace: {error}",
            source_tool.EXIT_INTEGRITY,
        ) from error


def materialize(
    manifest_path: Path,
    cache: Path,
    workspace: Path,
    *,
    link_mode: str = "preserve",
) -> None:
    workspace = Path(os.path.abspath(workspace))
    cache = cache.resolve(strict=False)
    if link_mode not in {"preserve", "portable-copy"}:
        _schema("link mode must be preserve or portable-copy")
    if link_mode == "portable-copy" and _same_path(workspace, DEFAULT_WORKSPACE):
        _schema(
            "portable-copy is inspection-only and requires an explicit non-canonical "
            "--workspace path"
        )
    if workspace.parent == workspace:
        _schema("workspace must not be a filesystem root")
    if _lexists(workspace):
        _integrity(f"workspace already exists; refusing to overwrite: {workspace}")
    try:
        workspace.relative_to(cache)
    except ValueError:
        pass
    else:
        _schema("workspace must not be inside the source cache")

    digest_before = _manifest_digest(manifest_path)
    data, sources = source_tool.load_manifest(manifest_path)
    destinations = plan_destinations(sources)
    source_tool.verify_cache(sources, cache)
    scan = _scan_locked_archives(sources, cache, destinations)
    expected_entries = _expected_materialized_entries(
        sources,
        destinations,
        scan,
        link_mode,
    )
    digest_after = _manifest_digest(manifest_path)
    if digest_before != digest_after:
        _integrity("manifest changed while materialization inputs were being verified")

    try:
        trusted_parent = _require_real_workspace_parent(workspace, create=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{workspace.name}.",
                suffix=".part",
                dir=trusted_parent,
            )
        )
        staging_stat = staging.lstat()
        staging_identity = (staging_stat.st_dev, staging_stat.st_ino)
    except OSError as error:
        raise source_tool.SourceToolError(
            f"cannot create materialization staging directory: {error}",
            source_tool.EXIT_INTERNAL,
        ) from error

    by_id = {str(source["id"]): source for source in sources}
    ordered = sorted(
        sources,
        key=lambda source: (
            0
            if str(source["id"]) == ROOT_SOURCE_ID
            else 1 + _source_depth(source, by_id),
            str(source["id"]),
        ),
    )
    try:
        if staging.is_symlink() or not _same_path(
            staging.parent.resolve(strict=True), trusted_parent
        ):
            _integrity("materialization staging directory escaped the trusted output parent")
        for source in ordered:
            source_id = str(source["id"])
            archive_path = cache / str(source["archive"])
            relative_destination = destinations[source_id]
            destination = staging / Path(*relative_destination.parts)
            if destination != staging:
                _ensure_no_symlink_ancestor(staging, destination)

            with source_tool.open_verified_archive(source, archive_path) as archive_stream:
                if source_id == SINGLE_FILE_SOURCE_ID:
                    _copy_single_file(source, archive_stream, destination)
                    _verify_license_paths(source, destination, single_file=True)
                else:
                    _prepare_directory(
                        staging,
                        destination,
                        reuse_empty=source_id == ROOT_SOURCE_ID,
                    )
                    _extract_tar(
                        source,
                        archive_stream,
                        destination,
                        link_mode=link_mode,
                    )
                    _verify_license_paths(source, destination, single_file=False)
            print(f"materialized {source_id}: {relative_destination.as_posix()}")

        receipt_path = staging / RECEIPT_NAME
        receipt_key = unicodedata.normalize("NFKC", RECEIPT_NAME).casefold()
        for child in staging.iterdir():
            if unicodedata.normalize("NFKC", child.name).casefold() == receipt_key:
                _integrity(
                    f"upstream source collides with materialization receipt: {child.name}"
                )
        _normalize_workspace_directories(staging)
        tree = _tree_digest(staging, expected_entries)
        receipt = _receipt_data(
            data,
            sources,
            destinations,
            digest_after,
            link_mode,
            tree,
        )
        with receipt_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _normalize_receipt_metadata(receipt_path)
        if os.name == "posix":
            with receipt_path.open("r+b") as stream:
                os.fsync(stream.fileno())
        _fsync_workspace_directories(staging)

        _rename_no_replace(staging, workspace)
        staging = None
        try:
            _fsync_directory(trusted_parent)
        except source_tool.SourceToolError as error:
            raise source_tool.SourceToolError(
                f"workspace was published at {workspace}, but output-parent durability "
                "could not be confirmed; do not retry blindly because the destination "
                "now exists. Verify the published workspace, then either use it or remove "
                f"it explicitly before retrying: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
        print(f"workspace ready: {workspace}")
    except BaseException as error:
        cleanup_error = (
            _remove_tree(staging, staging_identity) if staging is not None else None
        )
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                f"materialization failed and staging cleanup failed: {cleanup_error}",
                source_tool.EXIT_INTERNAL,
            ) from error
        if isinstance(error, source_tool.SourceToolError):
            raise
        if isinstance(error, KeyboardInterrupt):
            raise
        if isinstance(error, Exception):
            raise source_tool.SourceToolError(
                f"materialization failed: {error}", source_tool.EXIT_INTEGRITY
            ) from error
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"locked TOML manifest (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument(
        "--verify-workspace",
        action="store_true",
        help="verify an existing receipt, locked cache, and workspace tree instead of materializing",
    )
    parser.add_argument(
        "--allow-portable-copy",
        action="store_true",
        help="allow inspection-only portable-copy receipts during --verify-workspace",
    )
    parser.add_argument(
        "--link-mode",
        choices=("preserve", "portable-copy"),
        default="preserve",
        help=(
            "preserve safe symlinks for canonical Linux builds; portable-copy is "
            "inspection-only for hosts that cannot create symlinks"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        manifest_path = arguments.manifest.resolve()
        cache = arguments.cache.resolve()
        workspace = Path(os.path.abspath(arguments.workspace))
        if arguments.allow_portable_copy and not arguments.verify_workspace:
            _schema("--allow-portable-copy requires --verify-workspace")
        if arguments.verify_workspace:
            verify_materialized_workspace(
                manifest_path,
                cache,
                workspace,
                require_preserve=not arguments.allow_portable_copy,
            )
            return source_tool.EXIT_OK
        materialize(
            manifest_path,
            cache,
            workspace,
            link_mode=arguments.link_mode,
        )
        return source_tool.EXIT_OK
    except source_tool.SourceToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return source_tool.EXIT_INTERNAL
    except Exception as error:  # pragma: no cover - protects stable CLI behavior.
        print(f"error: unexpected failure: {error}", file=sys.stderr)
        return source_tool.EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
