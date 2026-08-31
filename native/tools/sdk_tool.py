# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan, materialize, and verify ZivPlayer's locked Android/Python tool roots."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import signal
import stat
import struct
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn

if sys.version_info < (3, 11):
    raise SystemExit("sdk_tool.py requires Python 3.11 or newer")

import environment_tool
import materialize_sources
import rootfs_tool
import source_tool
import toolchain_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_CACHE = NATIVE_DIR / "cache" / "toolchain"
DEFAULT_OUTPUT = Path("/var/tmp/zivplayer-sdk-projection")

RECEIPT_NAME = "ziv-sdk-projection.json"
RECEIPT_FORMAT = "ziv-sdk-projection-receipt-v1"
PROJECTION_FORMAT = "ziv-sdk-content-projection-v1"
MOUNT_PATH = "/opt/zivplayer/toolchain"
NORMALIZED_MTIME_NS = environment_tool.NORMALIZED_MTIME_NS

MAX_ZIP_MEMBERS = 20_000
MAX_ZIP_CENTRAL_BYTES = 16 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_ZIP_EXTRA_BYTES = 1024
MAX_SYMLINK_BYTES = 4096
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_HELPER_BYTES = 8 * 1024 * 1024
MAX_PATH_BYTES = rootfs_tool.MAX_PATH_BYTES
MAX_PATH_DEPTH = rootfs_tool.MAX_PATH_DEPTH
MAX_COMPONENT_BYTES = rootfs_tool.MAX_COMPONENT_BYTES

ANDROID_ARTIFACT_IDS = {
    "android-build-tools",
    "android-cmdline-tools",
    "android-ndk",
    "android-platform",
}
ALLOWED_NDK_CASE_PAIRS = frozenset(
    {
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_rateest.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_RATEEST.h",
            }
        ),
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_dscp.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_DSCP.h",
            }
        ),
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_MARK.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_mark.h",
            }
        ),
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_TCPMSS.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_tcpmss.h",
            }
        ),
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_connmark.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter/xt_CONNMARK.h",
            }
        ),
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter_ipv4/ipt_ECN.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter_ipv4/ipt_ecn.h",
            }
        ),
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter_ipv4/ipt_TTL.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter_ipv4/ipt_ttl.h",
            }
        ),
        frozenset(
            {
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter_ipv6/ip6t_hl.h",
                "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/"
                "sysroot/usr/include/linux/netfilter_ipv6/ip6t_HL.h",
            }
        ),
    }
)
WHEEL_ARTIFACT_ID = "meson-wheel"
ALLOWED_ZIP_FLAGS = 0x080A
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
EOCD_SIGNATURE = b"PK\x05\x06"
EOCD_STRUCT = struct.Struct("<4s4H2LH")

MESON_DIST_INFO = "meson-1.11.0.dist-info"
MESON_DATA = "meson-1.11.0.data"
MESON_SITE = PurePosixPath("python/site-packages")
MESON_LAUNCHER = PurePosixPath("bin/meson")
MESON_BOOTSTRAP = PurePosixPath("python/meson_launcher.py")
MESON_LAUNCHER_BYTES = (
    b"#!/bin/sh\n"
    b"exec /usr/bin/python3.12 -I -S -B "
    b"/opt/zivplayer/toolchain/python/meson_launcher.py \"$@\"\n"
)
MESON_BOOTSTRAP_BYTES = (
    b"from __future__ import annotations\n"
    b"import os\n"
    b"import sys\n"
    b"sys.dont_write_bytecode = True\n"
    b"os.environ['PYTHONDONTWRITEBYTECODE'] = '1'\n"
    b"sys.path.insert(0, '/opt/zivplayer/toolchain/python/site-packages')\n"
    b"sys.argv[0] = '/opt/zivplayer/toolchain/bin/meson'\n"
    b"from mesonbuild.mesonmain import main\n"
    b"raise SystemExit(main())\n"
)

HELPER_PATHS = (
    "native/tools/sdk_tool.py",
    "native/tools/environment_tool.py",
    "native/tools/toolchain_tool.py",
    "native/tools/source_tool.py",
    "native/tools/rootfs_tool.py",
    "native/tools/materialize_sources.py",
)


@dataclass(frozen=True)
class PlannedEntry:
    origin: str
    path: PurePosixPath
    kind: str
    mode: int
    size: int
    archive_member: str | None = None
    crc32: int | None = None
    link: str | None = None
    payload: bytes | None = None
    opaque_archive: bool = False


@dataclass(frozen=True)
class ArtifactPlan:
    artifact_id: str
    kind: str
    version: str
    archive: str
    archive_size: int
    archive_sha256: str
    install_path: str
    archive_prefix: str | None
    archive_members: int
    uncompressed_bytes: int
    source_properties_sha256: str | None
    metadata_sha256: str | None
    record_sha256: str | None
    entries: tuple[PlannedEntry, ...]
    plan_sha256: str


@dataclass(frozen=True)
class ProjectionRecord:
    origin: str
    path: str
    kind: str
    mode: int
    size: int
    sha256: str | None
    link: str | None


@dataclass(frozen=True)
class LockedSdkState:
    data: dict[str, object]
    artifacts: tuple[dict[str, object], ...]
    source_data: dict[str, object]
    manifest_sha256: str
    plans: tuple[ArtifactPlan, ...]


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: dict[str, object]) -> bytes:
    return environment_tool._canonical_json(value)


def _path_sort(value: PurePosixPath | str) -> bytes:
    text = value.as_posix() if isinstance(value, PurePosixPath) else value
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as error:
        _integrity(f"tool projection path is not UTF-8 encodable: {error}")


def _validate_projection_path(path: PurePosixPath, location: str) -> None:
    value = path.as_posix()
    try:
        encoded = value.encode("utf-8")
        components = [part.encode("utf-8") for part in path.parts]
    except UnicodeEncodeError as error:
        _integrity(f"{location} is not UTF-8 encodable: {error}")
    if (
        path.is_absolute()
        or not path.parts
        or len(path.parts) > MAX_PATH_DEPTH
        or len(encoded) > MAX_PATH_BYTES
        or any(len(component) > MAX_COMPONENT_BYTES for component in components)
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(unicodedata.normalize("NFKC", part) != part for part in path.parts)
        or "\\" in value
        or any(byte < 0x20 or byte == 0x7F for byte in encoded)
    ):
        _integrity(f"{location} is not a canonical bounded projection path: {value!r}")


def _safe_parts(name: str, location: str, *, directory_spelling: bool) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or not name.isascii():
        _integrity(f"{location} must use a non-empty ASCII path")
    if (
        "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
    ):
        _integrity(f"{location} is not a safe ZIP path: {name!r}")
    if name.endswith("/") != directory_spelling:
        _integrity(f"{location} has inconsistent directory spelling")
    body = name[:-1] if directory_spelling else name
    parts = body.split("/")
    if (
        not body
        or any(part in {"", ".", ".."} for part in parts)
        or len(parts) > MAX_PATH_DEPTH
        or len(body.encode("ascii")) > MAX_PATH_BYTES
    ):
        _integrity(f"{location} is not a canonical bounded ZIP path: {name!r}")
    for part in parts:
        encoded = part.encode("ascii")
        if len(encoded) > MAX_COMPONENT_BYTES:
            _integrity(f"{location} has an oversized path component: {part!r}")
        materialize_sources._portable_component(part, location)
        if unicodedata.normalize("NFKC", part) != part:
            _integrity(f"{location} has a non-canonical Unicode path component")
    return tuple(parts)


def _safe_link_target(path: PurePosixPath, raw: bytes, location: str) -> tuple[str, PurePosixPath]:
    if not raw or len(raw) > MAX_SYMLINK_BYTES:
        _integrity(f"{location} has an empty or oversized symbolic-link payload")
    try:
        target = raw.decode("ascii")
    except UnicodeDecodeError as error:
        _integrity(f"{location} symbolic-link target is not ASCII: {error}")
    resolved = materialize_sources._resolve_symlink_target(path, target, location)
    return target, resolved


def _preflight_zip_directory(stream: BinaryIO, artifact: dict[str, object]) -> None:
    size = int(artifact["size"])
    if size < EOCD_STRUCT.size:
        _integrity(f"ZIP for {artifact['id']} is too small")
    stream.seek(0, os.SEEK_END)
    actual_size = stream.tell()
    if actual_size != size:
        _integrity(
            f"ZIP for {artifact['id']} has size {actual_size}, expected {size}"
        )
    tail_size = min(size, EOCD_STRUCT.size + 65_535)
    stream.seek(size - tail_size)
    tail = stream.read(tail_size)
    if len(tail) != tail_size:
        _integrity(f"ZIP for {artifact['id']} ended while reading its directory trailer")
    position = tail.rfind(EOCD_SIGNATURE)
    if position < 0 or len(tail) - position < EOCD_STRUCT.size:
        _integrity(f"ZIP for {artifact['id']} has no bounded end-of-directory record")
    fields = EOCD_STRUCT.unpack_from(tail, position)
    (
        _signature,
        disk,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = fields
    eocd_offset = size - tail_size + position
    if (
        disk != 0
        or directory_disk != 0
        or disk_entries != total_entries
        or total_entries in {0, 0xFFFF}
        or total_entries > MAX_ZIP_MEMBERS
        or directory_size in {0, 0xFFFFFFFF}
        or directory_size > MAX_ZIP_CENTRAL_BYTES
        or directory_offset == 0xFFFFFFFF
        or directory_offset + directory_size != eocd_offset
        or comment_size != 0
        or position + EOCD_STRUCT.size != len(tail)
    ):
        _integrity(f"ZIP for {artifact['id']} has an unsupported directory layout")
    stream.seek(directory_offset)
    if stream.read(4) != b"PK\x01\x02":
        _integrity(f"ZIP for {artifact['id']} has an invalid central directory")
    stream.seek(0)


def _zip_kind_and_mode(
    info: zipfile.ZipInfo,
    location: str,
    *,
    allow_symlink: bool,
    wheel: bool,
) -> tuple[str, int]:
    raw_name = info.orig_filename
    if info.filename != raw_name:
        _integrity(f"{location} was sanitized while ZIP metadata was decoded")
    if info.create_system != 3:
        _integrity(f"{location} lacks canonical Unix file type metadata")
    if info.flag_bits & ~ALLOWED_ZIP_FLAGS or info.flag_bits & 0x1:
        _integrity(f"{location} uses unsupported or encrypted ZIP flags")
    if info.compress_type not in ALLOWED_COMPRESSION:
        _integrity(f"{location} uses an unsupported ZIP compression method")
    if len(info.extra) > MAX_ZIP_EXTRA_BYTES or info.comment:
        _integrity(f"{location} has excessive ZIP metadata")
    raw_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(raw_mode)
    permissions = stat.S_IMODE(raw_mode)
    if permissions & 0o7000:
        _integrity(f"{location} uses unsupported special permission bits")
    directory_spelling = raw_name.endswith("/")
    if file_type == stat.S_IFDIR:
        if not directory_spelling or info.file_size != 0 or permissions != 0o755:
            _integrity(f"{location} has invalid directory metadata")
        return "directory", 0o755
    if file_type == stat.S_IFLNK:
        if not allow_symlink or directory_spelling or permissions != 0o777:
            _integrity(f"{location} has an unsupported symbolic link")
        return "symlink", 0o777
    if file_type != stat.S_IFREG or directory_spelling:
        _integrity(f"{location} uses an unsupported ZIP entry type")
    allowed_modes = {0o644, 0o664} if wheel else {0o644, 0o755}
    if permissions not in allowed_modes:
        _integrity(f"{location} has unsupported regular-file mode {permissions:04o}")
    return "file", (0o644 if wheel else permissions)


def _entry_plan_bytes(entry: PlannedEntry) -> bytes:
    values = (
        entry.origin,
        entry.path.as_posix(),
        entry.kind,
        format(entry.mode, "04o"),
        str(entry.size),
        entry.archive_member or "-",
        "-" if entry.crc32 is None else format(entry.crc32, "08x"),
        entry.link or "-",
        "-" if entry.payload is None else _sha256(entry.payload),
        "opaque" if entry.opaque_archive else "member",
    )
    return "\t".join(values).encode("utf-8")


def _plan_digest(artifact_id: str, prefix: str | None, entries: list[PlannedEntry]) -> str:
    digest = hashlib.sha256()
    rootfs_tool._digest_record(digest, b"F", b"ziv-sdk-archive-plan-v1")
    rootfs_tool._digest_record(
        digest,
        b"A",
        f"{artifact_id}\t{prefix or '-'}".encode("ascii"),
    )
    for entry in sorted(entries, key=lambda value: _path_sort(value.path)):
        rootfs_tool._digest_record(digest, b"E", _entry_plan_bytes(entry))
    return digest.hexdigest()


def _properties(raw: bytes, location: str) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        _integrity(f"{location} is not UTF-8: {error}")
    # Google ships build-tools r36's locked source.properties without a final
    # newline.  Preserve and hash those upstream bytes instead of silently
    # rewriting them; the parser only needs to reject ambiguous line endings.
    if any(
        ord(character) < 0x20 and character != "\n" or ord(character) == 0x7F
        for character in text
    ):
        _integrity(f"{location} uses unsupported line endings or NUL bytes")
    result: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            _integrity(f"{location}:{line_number} is not a property assignment")
        key, value = (part.strip() for part in line.split("=", 1))
        if not key or key in result:
            _integrity(f"{location}:{line_number} repeats or omits a property key")
        result[key] = value
    return result


def _validate_android_identity(
    artifact: dict[str, object],
    archive: zipfile.ZipFile,
    prefix: str,
) -> str:
    member = f"{prefix}/source.properties"
    try:
        info = archive.getinfo(member)
    except KeyError as error:
        _integrity(f"Android archive {artifact['id']} lacks source.properties")
    kind, _mode = _zip_kind_and_mode(
        info,
        f"Android archive {artifact['id']} source.properties",
        allow_symlink=False,
        wheel=False,
    )
    if kind != "file":
        _integrity(f"Android archive {artifact['id']} source.properties is not a file")
    raw = archive.read(info)
    if len(raw) > 4096:
        _integrity(f"Android archive {artifact['id']} has oversized source.properties")
    values = _properties(raw, f"{artifact['id']} source.properties")
    artifact_id = str(artifact["id"])
    version = str(artifact["version"])
    if artifact_id == "android-build-tools":
        valid = values.get("Pkg.Revision") == version
    elif artifact_id == "android-cmdline-tools":
        valid = (
            values.get("Pkg.Revision") == version
            and values.get("Pkg.Path") == f"cmdline-tools;{version}"
        )
    elif artifact_id == "android-ndk":
        valid = (
            values.get("Pkg.Revision") == version
            and values.get("Pkg.BaseRevision") == version
            and values.get("Pkg.ReleaseName") == "r29"
        )
    elif artifact_id == "android-platform":
        valid = (
            version == "36-r02"
            and values.get("AndroidVersion.ApiLevel") == "36"
            and values.get("Pkg.Revision") == "2"
            and values.get("AndroidVersion.IsBaseSdk") == "true"
        )
    else:  # pragma: no cover - guarded by manifest validation
        valid = False
    if not valid:
        _integrity(f"Android archive {artifact_id} source.properties disagrees with its lock")
    return _sha256(raw)


def _plan_android_archive(
    artifact: dict[str, object],
    stream: BinaryIO,
    *,
    verify_payloads: bool = True,
) -> ArtifactPlan:
    artifact_id = str(artifact["id"])
    _preflight_zip_directory(stream, artifact)
    try:
        with zipfile.ZipFile(stream, mode="r", allowZip64=False) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ZIP_MEMBERS or archive.comment:
                _integrity(f"Android archive {artifact_id} has an invalid entry set")
            prefix: str | None = None
            root_entry_seen = False
            seen_names: set[str] = set()
            seen_paths: dict[str, PurePosixPath] = {}
            entries: list[PlannedEntry] = []
            uncompressed = 0
            resolved_links: dict[PurePosixPath, PurePosixPath] = {}
            entry_kinds: dict[PurePosixPath, str] = {}
            install = PurePosixPath(str(artifact["installPath"]))

            for index, info in enumerate(infos):
                location = f"Android archive {artifact_id} member[{index}]"
                raw_name = info.orig_filename
                if raw_name in seen_names:
                    _integrity(f"Android archive {artifact_id} repeats {raw_name!r}")
                seen_names.add(raw_name)
                kind, mode = _zip_kind_and_mode(
                    info,
                    location,
                    allow_symlink=True,
                    wheel=False,
                )
                parts = _safe_parts(
                    raw_name,
                    location,
                    directory_spelling=kind == "directory",
                )
                if prefix is None:
                    prefix = parts[0]
                elif parts[0] != prefix:
                    _integrity(f"Android archive {artifact_id} must have one top-level prefix")
                if len(parts) == 1:
                    if kind != "directory" or root_entry_seen:
                        _integrity(f"Android archive {artifact_id} has an invalid root entry")
                    root_entry_seen = True
                    continue
                relative = PurePosixPath(*parts[1:])
                target = install / relative
                _validate_projection_path(
                    target,
                    f"Android archive {artifact_id} output",
                )
                normalized = "/".join(
                    unicodedata.normalize("NFKC", part) for part in target.parts
                )
                previous = seen_paths.get(normalized)
                if previous is not None and previous != target:
                    _integrity(
                        f"Android archive {artifact_id} has an NFKC path collision: "
                        f"{previous.as_posix()} and {target.as_posix()}"
                    )
                if target in entry_kinds:
                    _integrity(f"Android archive {artifact_id} repeats {target.as_posix()}")
                seen_paths[normalized] = target
                entry_kinds[target] = kind
                if info.file_size < 0 or info.file_size > MAX_ZIP_MEMBER_BYTES:
                    _integrity(f"{location} has an invalid or excessive expanded size")
                uncompressed += info.file_size
                if uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    _integrity(f"Android archive {artifact_id} exceeds the expanded-byte limit")
                link: str | None = None
                if kind == "symlink":
                    if info.file_size > MAX_SYMLINK_BYTES:
                        _integrity(f"{location} has an oversized symbolic-link payload")
                    raw = archive.read(info)
                    link, resolved = _safe_link_target(relative, raw, location)
                    resolved_links[target] = install / resolved
                elif kind == "file" and verify_payloads:
                    with archive.open(info, mode="r") as source:
                        _consume_exact(source, info.file_size, location)
                entries.append(
                    PlannedEntry(
                        artifact_id,
                        target,
                        kind,
                        mode,
                        info.file_size,
                        raw_name,
                        info.CRC,
                        link,
                    )
                )

            if prefix is None:
                _integrity(f"Android archive {artifact_id} has no top-level prefix")
            for entry in entries:
                for parent in entry.path.parents:
                    if parent == PurePosixPath("."):
                        break
                    parent_kind = entry_kinds.get(parent)
                    if parent_kind is not None and parent_kind != "directory":
                        _integrity(
                            f"Android archive {artifact_id} places {entry.path.as_posix()} "
                            f"below non-directory {parent.as_posix()}"
                        )
            for link_path, target in resolved_links.items():
                if target not in entry_kinds:
                    _integrity(
                        f"Android archive {artifact_id} link {link_path.as_posix()} "
                        f"targets missing path {target.as_posix()}"
                    )
                link_text = next(
                    entry.link for entry in entries if entry.path == link_path
                )
                if (
                    link_text is not None
                    and link_text.endswith("/")
                    and entry_kinds[target] != "directory"
                ):
                    _integrity(
                        f"Android archive {artifact_id} link {link_path.as_posix()} "
                        "uses directory spelling for a non-directory target"
                    )
                visited = {link_path}
                current = target
                while entry_kinds[current] == "symlink":
                    if current in visited:
                        _integrity(
                            f"Android archive {artifact_id} has a symbolic-link cycle "
                            f"at {link_path.as_posix()}"
                        )
                    visited.add(current)
                    current = resolved_links[current]
                    if current not in entry_kinds:
                        _integrity(
                            f"Android archive {artifact_id} link chain from "
                            f"{link_path.as_posix()} targets missing path "
                            f"{current.as_posix()}"
                        )
            properties_sha256 = _validate_android_identity(artifact, archive, prefix)
    except source_tool.SourceToolError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        _integrity(f"cannot plan Android archive {artifact_id}: {error}")

    return ArtifactPlan(
        artifact_id=artifact_id,
        kind=str(artifact["kind"]),
        version=str(artifact["version"]),
        archive=str(artifact["archive"]),
        archive_size=int(artifact["size"]),
        archive_sha256=str(artifact["sha256"]),
        install_path=str(artifact["installPath"]),
        archive_prefix=prefix,
        archive_members=len(infos),
        uncompressed_bytes=uncompressed,
        source_properties_sha256=properties_sha256,
        metadata_sha256=None,
        record_sha256=None,
        entries=tuple(entries),
        plan_sha256=_plan_digest(artifact_id, prefix, entries),
    )


def _decode_record_digest(value: str, location: str) -> bytes:
    if not value.startswith("sha256="):
        _integrity(f"{location} must use a SHA-256 wheel RECORD digest")
    encoded = value.removeprefix("sha256=")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded):
        _integrity(f"{location} has a malformed wheel RECORD digest")
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=")
    except (ValueError, base64.binascii.Error) as error:
        _integrity(f"{location} has an invalid wheel RECORD digest: {error}")
    if len(decoded) != hashlib.sha256().digest_size:
        _integrity(f"{location} has a non-SHA-256 wheel RECORD digest")
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != encoded:
        _integrity(f"{location} has a non-canonical wheel RECORD digest")
    return decoded


def _wheel_target(parts: tuple[str, ...], location: str) -> PurePosixPath:
    first = parts[0]
    if first == "mesonbuild" or first == MESON_DIST_INFO:
        target = MESON_SITE / PurePosixPath(*parts)
        _validate_projection_path(target, f"{location} output")
        return target
    if first == MESON_DATA and len(parts) >= 3 and parts[1] == "data":
        target = PurePosixPath(*parts[2:])
        _validate_projection_path(target, f"{location} output")
        return target
    _integrity(f"{location} is outside the supported Meson wheel roots")


def _validate_wheel_headers(
    metadata: bytes,
    wheel: bytes,
    entry_points: bytes,
    artifact: dict[str, object],
) -> None:
    for name, raw in (
        ("METADATA", metadata),
        ("WHEEL", wheel),
        ("entry_points.txt", entry_points),
    ):
        if b"\r" in raw or b"\x00" in raw or not raw.endswith(b"\n"):
            _integrity(f"Meson wheel {name} is not canonical LF-terminated text")
    try:
        metadata_message = BytesParser(policy=email_policy.default).parsebytes(metadata)
        wheel_message = BytesParser(policy=email_policy.default).parsebytes(wheel)
    except (UnicodeError, ValueError) as error:
        _integrity(f"Meson wheel metadata cannot be parsed: {error}")
    if metadata_message.defects or wheel_message.defects:
        _integrity("Meson wheel metadata contains MIME parser defects")
    if (
        metadata_message.get_all("Name", []) != ["meson"]
        or metadata_message.get_all("Version", []) != [artifact["version"]]
        or metadata_message.get_all("Metadata-Version", []) != ["2.4"]
    ):
        _integrity("Meson wheel METADATA disagrees with the locked identity")
    if (
        wheel_message.get_all("Wheel-Version", []) != ["1.0"]
        or wheel_message.get_all("Root-Is-Purelib", []) != ["true"]
        or wheel_message.get_all("Tag", []) != ["py3-none-any"]
    ):
        _integrity("Meson wheel WHEEL metadata is not the locked pure-Python layout")
    expected_entry_points = (
        b"[console_scripts]\n"
        b"meson = mesonbuild.mesonmain:main\n"
    )
    if entry_points != expected_entry_points:
        _integrity("Meson wheel console entry point disagrees with the fixed launcher")


def _plan_wheel(
    artifact: dict[str, object],
    stream: BinaryIO,
) -> ArtifactPlan:
    artifact_id = str(artifact["id"])
    _preflight_zip_directory(stream, artifact)
    record_name = f"{MESON_DIST_INFO}/RECORD"
    metadata_name = f"{MESON_DIST_INFO}/METADATA"
    wheel_name = f"{MESON_DIST_INFO}/WHEEL"
    entry_points_name = f"{MESON_DIST_INFO}/entry_points.txt"
    required = {record_name, metadata_name, wheel_name, entry_points_name}
    try:
        with zipfile.ZipFile(stream, mode="r", allowZip64=False) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ZIP_MEMBERS or archive.comment:
                _integrity("Meson wheel has an invalid entry set")
            by_name: dict[str, zipfile.ZipInfo] = {}
            by_target: dict[PurePosixPath, str] = {}
            entries: list[PlannedEntry] = []
            uncompressed = 0
            required_payloads: dict[str, bytes] = {}
            payload_hashes: dict[str, bytes] = {}
            required_bytes = 0
            for index, info in enumerate(infos):
                location = f"Meson wheel member[{index}]"
                kind, mode = _zip_kind_and_mode(
                    info,
                    location,
                    allow_symlink=False,
                    wheel=True,
                )
                if kind != "file":
                    _integrity(f"{location} must be a regular file")
                parts = _safe_parts(
                    info.orig_filename,
                    location,
                    directory_spelling=False,
                )
                if info.orig_filename in by_name:
                    _integrity(f"Meson wheel repeats {info.orig_filename!r}")
                by_name[info.orig_filename] = info
                target = _wheel_target(parts, location)
                previous = by_target.get(target)
                if previous is not None:
                    _integrity(
                        f"Meson wheel maps {previous!r} and {info.orig_filename!r} "
                        f"to {target.as_posix()}"
                    )
                by_target[target] = info.orig_filename
                if info.file_size < 0 or info.file_size > MAX_ZIP_MEMBER_BYTES:
                    _integrity(f"{location} has an invalid or excessive expanded size")
                uncompressed += info.file_size
                if uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    _integrity("Meson wheel exceeds the expanded-byte limit")
                if info.orig_filename in required:
                    required_bytes += info.file_size
                    if required_bytes > MAX_RECEIPT_BYTES:
                        _integrity("Meson wheel control metadata exceeds its memory limit")
                    raw = archive.read(info)
                    if len(raw) != info.file_size:
                        _integrity(f"{location} changed size while planning")
                    required_payloads[info.orig_filename] = raw
                    payload_hashes[info.orig_filename] = hashlib.sha256(raw).digest()
                else:
                    with archive.open(info, mode="r") as source:
                        digest = _consume_exact(source, info.file_size, location)
                    payload_hashes[info.orig_filename] = bytes.fromhex(digest)
                entries.append(
                    PlannedEntry(
                        artifact_id,
                        target,
                        "file",
                        mode,
                        info.file_size,
                        info.orig_filename,
                        info.CRC,
                    )
                )

            missing = sorted(required - set(by_name))
            if missing:
                _integrity(f"Meson wheel is missing required member {missing[0]}")
            try:
                record_text = required_payloads[record_name].decode("utf-8")
            except UnicodeDecodeError as error:
                _integrity(f"Meson wheel RECORD is not UTF-8: {error}")
            if "\r" in record_text or not record_text.endswith("\n"):
                _integrity("Meson wheel RECORD must be LF-terminated canonical CSV")
            rows: dict[str, tuple[str, str]] = {}
            try:
                reader = csv.reader(io.StringIO(record_text, newline=""), strict=True)
                for number, row in enumerate(reader, start=1):
                    if len(row) != 3 or not row[0] or row[0] in rows:
                        _integrity(f"Meson wheel RECORD row {number} is not canonical")
                    rows[row[0]] = (row[1], row[2])
            except csv.Error as error:
                _integrity(f"Meson wheel RECORD is invalid CSV: {error}")
            if set(rows) != set(by_name):
                _integrity("Meson wheel RECORD member set differs from its ZIP members")
            for name, info in by_name.items():
                digest_text, size_text = rows[name]
                if name == record_name:
                    if digest_text or size_text:
                        _integrity("Meson wheel RECORD must leave its own hash and size empty")
                    continue
                expected = _decode_record_digest(
                    digest_text,
                    f"Meson wheel RECORD entry {name}",
                )
                if size_text != str(info.file_size):
                    _integrity(f"Meson wheel RECORD size differs for {name}")
                if payload_hashes[name] != expected:
                    _integrity(f"Meson wheel RECORD hash differs for {name}")

            _validate_wheel_headers(
                required_payloads[metadata_name],
                required_payloads[wheel_name],
                required_payloads[entry_points_name],
                artifact,
            )
            entries.extend(
                (
                    PlannedEntry(
                        artifact_id,
                        PurePosixPath(str(artifact["installPath"])),
                        "file",
                        0o644,
                        int(artifact["size"]),
                        opaque_archive=True,
                    ),
                    PlannedEntry(
                        artifact_id,
                        MESON_LAUNCHER,
                        "file",
                        0o755,
                        len(MESON_LAUNCHER_BYTES),
                        payload=MESON_LAUNCHER_BYTES,
                    ),
                    PlannedEntry(
                        artifact_id,
                        MESON_BOOTSTRAP,
                        "file",
                        0o644,
                        len(MESON_BOOTSTRAP_BYTES),
                        payload=MESON_BOOTSTRAP_BYTES,
                    ),
                )
            )
            metadata_sha256 = _sha256(required_payloads[metadata_name])
            record_sha256 = _sha256(required_payloads[record_name])
    except source_tool.SourceToolError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        _integrity(f"cannot plan Meson wheel: {error}")

    return ArtifactPlan(
        artifact_id=artifact_id,
        kind=str(artifact["kind"]),
        version=str(artifact["version"]),
        archive=str(artifact["archive"]),
        archive_size=int(artifact["size"]),
        archive_sha256=str(artifact["sha256"]),
        install_path=str(artifact["installPath"]),
        archive_prefix=None,
        archive_members=len(infos),
        uncompressed_bytes=uncompressed,
        source_properties_sha256=None,
        metadata_sha256=metadata_sha256,
        record_sha256=record_sha256,
        entries=tuple(entries),
        plan_sha256=_plan_digest(artifact_id, None, entries),
    )


def _plan_artifact(
    artifact: dict[str, object],
    stream: BinaryIO,
    *,
    verify_payloads: bool = True,
) -> ArtifactPlan:
    artifact_id = str(artifact["id"])
    if artifact_id in ANDROID_ARTIFACT_IDS and artifact["kind"] == "android-archive":
        return _plan_android_archive(
            artifact,
            stream,
            verify_payloads=verify_payloads,
        )
    if artifact_id == WHEEL_ARTIFACT_ID and artifact["kind"] == "python-wheel":
        return _plan_wheel(artifact, stream)
    _schema(f"unsupported SDK projection artifact: {artifact_id}")


def _projection_entries(plans: tuple[ArtifactPlan, ...]) -> tuple[PlannedEntry, ...]:
    by_path: dict[PurePosixPath, PlannedEntry] = {}
    normalized: dict[str, PurePosixPath] = {}
    receipt_key = unicodedata.normalize("NFKC", RECEIPT_NAME).casefold()
    for plan in plans:
        for entry in plan.entries:
            _validate_projection_path(entry.path, "projection output")
            if (
                unicodedata.normalize("NFKC", entry.path.parts[0]).casefold()
                == receipt_key
            ):
                _integrity("projection collides with its canonical receipt")
            key = "/".join(unicodedata.normalize("NFKC", part) for part in entry.path.parts)
            previous_normalized = normalized.get(key)
            if previous_normalized is not None and previous_normalized != entry.path:
                _integrity(
                    "projection has a Unicode path collision: "
                    f"{previous_normalized.as_posix()} and {entry.path.as_posix()}"
                )
            normalized[key] = entry.path
            previous = by_path.get(entry.path)
            if previous is not None:
                if previous.kind == entry.kind == "directory":
                    continue
                _integrity(f"projection repeats output path {entry.path.as_posix()}")
            by_path[entry.path] = entry

    for entry in list(by_path.values()):
        for parent in entry.path.parents:
            if parent == PurePosixPath("."):
                break
            previous = by_path.get(parent)
            if previous is None:
                by_path[parent] = PlannedEntry(
                    "generated",
                    parent,
                    "directory",
                    0o755,
                    0,
                )
            elif previous.kind != "directory":
                _integrity(
                    f"projection places {entry.path.as_posix()} below non-directory "
                    f"{parent.as_posix()}"
                )
    folded: dict[str, PlannedEntry] = {}
    for entry in sorted(by_path.values(), key=lambda value: _path_sort(value.path)):
        folded_key = "/".join(
            unicodedata.normalize("NFKC", part).casefold()
            for part in entry.path.parts
        )
        previous_folded = folded.get(folded_key)
        if previous_folded is not None and previous_folded.path != entry.path:
            pair = frozenset(
                {previous_folded.path.as_posix(), entry.path.as_posix()}
            )
            if not (
                previous_folded.origin == entry.origin == "android-ndk"
                and pair in ALLOWED_NDK_CASE_PAIRS
            ):
                _integrity(
                    "projection has a cross-artifact case path collision: "
                    f"{previous_folded.path.as_posix()} and {entry.path.as_posix()}"
                )
        else:
            folded[folded_key] = entry
    if len(by_path) > environment_tool.MAX_TREE_ENTRIES:
        _integrity("projection exceeds the installed-tree entry limit")
    physical_bytes = sum(
        entry.size for entry in by_path.values() if entry.kind == "file"
    )
    if physical_bytes > environment_tool.MAX_TREE_FILE_BYTES:
        _integrity("projection exceeds the installed-tree file-byte limit")
    return tuple(sorted(by_path.values(), key=lambda entry: _path_sort(entry.path)))


def _load_locked_state(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
) -> LockedSdkState:
    (
        data,
        artifacts,
        oci_objects,
        source_data,
        manifest_sha256,
    ) = toolchain_tool.load_manifest_snapshot(
        manifest,
        source_manifest_path=source_manifest,
    )
    toolchain_tool.verify_roots(data, artifacts, oci_objects, cache)
    if {str(artifact["id"]) for artifact in artifacts} != (
        ANDROID_ARTIFACT_IDS | {WHEEL_ARTIFACT_ID}
    ):
        _schema("toolchain manifest does not contain the exact SDK projection artifact set")
    plans: list[ArtifactPlan] = []
    for artifact in artifacts:
        path = cache / str(artifact["archive"])
        with source_tool.open_verified_archive(artifact, path) as stream:
            plans.append(_plan_artifact(artifact, stream))
    result = LockedSdkState(
        data=data,
        artifacts=tuple(artifacts),
        source_data=source_data,
        manifest_sha256=manifest_sha256,
        plans=tuple(plans),
    )
    _projection_entries(result.plans)
    return result


def _consume_exact(
    stream: BinaryIO,
    size: int,
    location: str,
    *,
    destination: Path | None = None,
    mode: int = 0o644,
) -> str:
    if size < 0:
        _integrity(f"{location} has a negative declared size")
    descriptor = -1
    digest = hashlib.sha256()
    try:
        if destination is not None:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(destination, flags, mode)
        remaining = size
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                _integrity(f"{location} ended before its declared size")
            if len(chunk) > remaining:
                _integrity(f"{location} returned more bytes than requested")
            remaining -= len(chunk)
            digest.update(chunk)
            if descriptor >= 0:
                environment_tool._write_all(descriptor, chunk)
        if stream.read(1):
            _integrity(f"{location} exceeds its declared size")
        if descriptor >= 0:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot project {location}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if destination is not None:
        try:
            os.utime(
                destination,
                ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
                follow_symlinks=False,
            )
        except OSError as error:
            _integrity(f"cannot normalize projected file {destination}: {error}")
    return digest.hexdigest()


def _artifact_file_hashes(
    artifact: dict[str, object],
    plan: ArtifactPlan,
    cache: Path,
    *,
    destination: Path | None,
) -> dict[PurePosixPath, str]:
    result: dict[PurePosixPath, str] = {}
    archive_path = cache / str(artifact["archive"])
    with source_tool.open_verified_archive(artifact, archive_path) as stream:
        current_plan = _plan_artifact(
            artifact,
            stream,
            verify_payloads=False,
        )
        if current_plan != plan:
            _integrity(f"artifact plan changed before projecting {plan.artifact_id}")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream, mode="r", allowZip64=False) as archive:
                by_name = {info.orig_filename: info for info in archive.infolist()}
                for entry in plan.entries:
                    if entry.kind != "file" or entry.archive_member is None:
                        continue
                    info = by_name.get(entry.archive_member)
                    if info is None or info.file_size != entry.size or info.CRC != entry.crc32:
                        _integrity(
                            f"archive member changed before projecting {entry.path.as_posix()}"
                        )
                    target = (
                        None
                        if destination is None
                        else destination.joinpath(*entry.path.parts)
                    )
                    with archive.open(info, mode="r") as source:
                        result[entry.path] = _consume_exact(
                            source,
                            entry.size,
                            f"{plan.artifact_id}:{entry.archive_member}",
                            destination=target,
                            mode=entry.mode,
                        )
        except source_tool.SourceToolError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            _integrity(f"cannot project {plan.artifact_id}: {error}")

        for entry in plan.entries:
            if entry.kind != "file" or entry.archive_member is not None:
                continue
            target = (
                None if destination is None else destination.joinpath(*entry.path.parts)
            )
            if entry.payload is not None:
                source = io.BytesIO(entry.payload)
            elif entry.opaque_archive:
                stream.seek(0)
                source = stream
            else:
                _integrity(f"projected file {entry.path.as_posix()} has no content source")
            result[entry.path] = _consume_exact(
                source,
                entry.size,
                f"{plan.artifact_id}:{entry.path.as_posix()}",
                destination=target,
                mode=entry.mode,
            )
    expected = {
        entry.path for entry in plan.entries if entry.kind == "file"
    }
    if set(result) != expected:
        _integrity(f"artifact projection omitted a file from {plan.artifact_id}")
    return result


def _file_hashes(
    state: LockedSdkState,
    cache: Path,
    *,
    destination: Path | None,
) -> dict[PurePosixPath, str]:
    by_id = {str(artifact["id"]): artifact for artifact in state.artifacts}
    result: dict[PurePosixPath, str] = {}
    for plan in state.plans:
        artifact = by_id.get(plan.artifact_id)
        if artifact is None:
            _integrity(f"projection plan has no artifact lock for {plan.artifact_id}")
        for path, digest in _artifact_file_hashes(
            artifact,
            plan,
            cache,
            destination=destination,
        ).items():
            if path in result:
                _integrity(f"projection repeats file content at {path.as_posix()}")
            result[path] = digest
    return result


def _projection_records(
    entries: tuple[PlannedEntry, ...],
    file_hashes: dict[PurePosixPath, str],
) -> tuple[ProjectionRecord, ...]:
    records: list[ProjectionRecord] = []
    for entry in entries:
        sha256: str | None = None
        link: str | None = None
        size = 0
        if entry.kind == "file":
            sha256 = file_hashes.get(entry.path)
            if sha256 is None:
                _integrity(f"projection lacks a file hash for {entry.path.as_posix()}")
            size = entry.size
        elif entry.kind == "symlink":
            if entry.link is None:
                _integrity(f"projection lacks a link target for {entry.path.as_posix()}")
            link = entry.link
            size = len(link.encode("utf-8"))
        elif entry.kind != "directory":
            _integrity(f"projection uses unsupported entry type {entry.kind}")
        records.append(
            ProjectionRecord(
                entry.origin,
                entry.path.as_posix(),
                entry.kind,
                entry.mode,
                size,
                sha256,
                link,
            )
        )
    expected_files = {entry.path for entry in entries if entry.kind == "file"}
    if set(file_hashes) != expected_files:
        _integrity("projection file-hash set differs from its planned files")
    return tuple(records)


def _projection_record_bytes(record: ProjectionRecord) -> bytes:
    return "\t".join(
        (
            record.origin,
            record.path,
            record.kind,
            format(record.mode, "04o"),
            str(record.size),
            record.sha256 or "-",
            record.link or "-",
        )
    ).encode("utf-8")


def _projection_record_data(
    records: tuple[ProjectionRecord, ...],
) -> dict[str, object]:
    digest = hashlib.sha256()
    rootfs_tool._digest_record(digest, b"F", PROJECTION_FORMAT.encode("ascii"))
    counts = {"directories": 0, "files": 0, "symlinks": 0}
    count_key = {
        "directory": "directories",
        "file": "files",
        "symlink": "symlinks",
    }
    file_bytes = 0
    for record in records:
        rootfs_tool._digest_record(digest, b"E", _projection_record_bytes(record))
        counts[count_key[record.kind]] += 1
        if record.kind == "file":
            file_bytes += record.size
    return {
        "format": PROJECTION_FORMAT,
        "sha256": digest.hexdigest(),
        "entries": len(records),
        "fileBytes": file_bytes,
        **counts,
    }


def _expected_tree(records: tuple[ProjectionRecord, ...]) -> dict[str, object]:
    digest = hashlib.sha256()
    rootfs_tool._digest_record(
        digest,
        b"F",
        environment_tool.TREE_FORMAT.encode("ascii"),
    )
    rootfs_tool._digest_record(
        digest,
        b"R",
        f"0700\t0\t0\t{NORMALIZED_MTIME_NS}\t-".encode("ascii"),
    )
    counts = {"directories": 0, "files": 0, "hardlinks": 0, "symlinks": 0}
    count_key = {
        "directory": "directories",
        "file": "files",
        "symlink": "symlinks",
    }
    file_bytes = 0
    for record in records:
        scanned = environment_tool.ScannedEntry(
            path=record.path,
            kind=record.kind,
            mode=record.mode,
            uid=0,
            gid=0,
            mtime_ns=NORMALIZED_MTIME_NS,
            size=record.size,
            sha256=record.sha256,
            link=record.link,
            xattrs_sha256=None,
        )
        rootfs_tool._digest_record(digest, b"E", environment_tool._entry_bytes(scanned))
        counts[count_key[record.kind]] += 1
        if record.kind == "file":
            file_bytes += record.size
    return {
        "format": environment_tool.TREE_FORMAT,
        "sha256": digest.hexdigest(),
        "entries": len(records),
        "fileBytes": file_bytes,
        "rootXattrsSha256": None,
        **counts,
    }


def _case_collision_groups(
    records: tuple[ProjectionRecord, ...],
) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for record in records:
        key = "/".join(
            unicodedata.normalize("NFKC", part).casefold()
            for part in PurePosixPath(record.path).parts
        )
        groups.setdefault(key, []).append(record.path)
    collisions = [
        sorted(paths, key=lambda value: value.encode("utf-8"))
        for paths in groups.values()
        if len(paths) > 1
    ]
    collisions.sort(key=lambda paths: paths[0].encode("utf-8"))
    return collisions


def _legal_inventory(
    records: tuple[ProjectionRecord, ...],
    plans: tuple[ArtifactPlan, ...] = (),
) -> list[dict[str, object]]:
    pattern = re.compile(
        r"(?:^|/)(?:(?:notice|licen[cs]e|copying|copyright)(?:[._-][^/]*)?"
        r"|module_license_[^/]*)$",
        re.IGNORECASE,
    )
    sources: dict[str, tuple[str, str | None]] = {}
    for plan in plans:
        for entry in plan.entries:
            sources[entry.path.as_posix()] = (plan.archive, entry.archive_member)
    result: list[dict[str, object]] = []
    for record in records:
        if record.kind != "file" or not pattern.search(record.path):
            continue
        source = sources.get(record.path)
        item: dict[str, object] = {
            "origin": record.origin,
            "path": record.path,
            "size": record.size,
            "sha256": record.sha256,
        }
        if source is not None:
            item["archive"] = source[0]
            item["archiveMember"] = source[1]
        result.append(item)
    return result


def _helper_records() -> list[dict[str, object]]:
    repository_root = NATIVE_DIR.parent
    records: list[dict[str, object]] = []
    for relative in HELPER_PATHS:
        raw = toolchain_tool._read_stable_bytes(
            repository_root / relative,
            maximum=MAX_HELPER_BYTES,
            label=f"SDK projection helper {relative}",
            missing_exit=source_tool.EXIT_SCHEMA,
        )
        records.append({"path": relative, "size": len(raw), "sha256": _sha256(raw)})
    return records


def _artifact_receipt(
    plan: ArtifactPlan,
    artifact: dict[str, object] | None = None,
) -> dict[str, object]:
    counts = {"directories": 0, "files": 0, "symlinks": 0}
    count_key = {
        "directory": "directories",
        "file": "files",
        "symlink": "symlinks",
    }
    for entry in plan.entries:
        counts[count_key[entry.kind]] += 1
    return {
        "id": plan.artifact_id,
        "kind": plan.kind,
        "version": plan.version,
        "archive": plan.archive,
        "archiveSize": plan.archive_size,
        "archiveSha256": plan.archive_sha256,
        "url": None if artifact is None else artifact.get("url"),
        "publishedSha1": (
            None if artifact is None else artifact.get("publishedSha1")
        ),
        "installPath": plan.install_path,
        "archivePrefix": plan.archive_prefix,
        "archiveMembers": plan.archive_members,
        "uncompressedBytes": plan.uncompressed_bytes,
        "sourcePropertiesSha256": plan.source_properties_sha256,
        "metadataSha256": plan.metadata_sha256,
        "recordSha256": plan.record_sha256,
        "planSha256": plan.plan_sha256,
        **counts,
    }


def _receipt_data(
    state: LockedSdkState,
    records: tuple[ProjectionRecord, ...],
    tree: dict[str, object],
) -> dict[str, object]:
    project = toolchain_tool._expect_table(state.data["project"], "project")
    source_project = toolchain_tool._expect_table(
        state.source_data["project"],
        "source project",
    )
    compliance = toolchain_tool._expect_table(state.data["compliance"], "compliance")
    artifacts_by_id = {
        str(artifact["id"]): artifact for artifact in state.artifacts
    }
    return {
        "schemaVersion": 1,
        "kind": RECEIPT_FORMAT,
        "ready": False,
        "mountPath": MOUNT_PATH,
        "toolchainManifestSha256": state.manifest_sha256,
        "sourceManifestSha256": project["sourceManifestSha256"],
        "project": {
            "name": project["name"],
            "hostPlatform": project["hostPlatform"],
            "nativeApi": project["nativeApi"],
            "abis": project["abis"],
            "pageSizeBytes": project["pageSizeBytes"],
            "upstreamRelease": source_project["upstreamRelease"],
            "upstreamRevision": source_project["upstreamRevision"],
        },
        "policy": {
            "filesystem": "ext4",
            "rootMode": "0700",
            "rootOwner": "0:0",
            "normalizedMtimeNs": NORMALIZED_MTIME_NS,
            "caseSensitivePaths": True,
            "networkAtBuild": "none",
            "sdkmanagerAtBuild": "forbidden",
            "pipIndexAtBuild": "forbidden",
            "wheelInstaller": "direct-projection",
            "wheelRecordScope": "source-wheel members before data-path remapping",
            "installedProjectionRecord": PROJECTION_FORMAT,
            "packageXml": "not-generated",
        },
        "composition": {
            "type": "standalone-mountable-projection",
            "mountPath": MOUNT_PATH,
            "aptEnvironmentBound": False,
            "releaseInput": False,
        },
        "meson": {
            "version": next(
                plan.version for plan in state.plans if plan.artifact_id == WHEEL_ARTIFACT_ID
            ),
            "python": "/usr/bin/python3.12",
            "pythonFlags": ["-I", "-S", "-B"],
            "sitePackages": f"{MOUNT_PATH}/{MESON_SITE.as_posix()}",
            "launcher": f"{MOUNT_PATH}/{MESON_LAUNCHER.as_posix()}",
            "launcherSha256": _sha256(MESON_LAUNCHER_BYTES),
            "bootstrapSha256": _sha256(MESON_BOOTSTRAP_BYTES),
            "bytecode": "disabled",
            "scope": "Meson runtime only; source-build Python modules are not projected",
        },
        "artifacts": [
            _artifact_receipt(plan, artifacts_by_id.get(plan.artifact_id))
            for plan in state.plans
        ],
        "projection": _projection_record_data(records),
        "tree": tree,
        "caseSensitiveCollisionGroups": _case_collision_groups(records),
        "toolchainLegalInventory": _legal_inventory(records, state.plans),
        "legalInventoryScope": "projected builder tools only; not product notices",
        "helpers": _helper_records(),
        "compliance": dict(compliance),
        "releaseBlockers": [
            "accepted Android license evidence is not installed",
            "system notices bundle is not complete",
            "retention bundle is not complete",
            "source-build Python module closure is not yet projected",
            "verified APT environment binding and isolated runtime smoke are pending",
        ],
    }


def _require_destination_paths_fit(
    root: Path,
    entries: tuple[PlannedEntry, ...],
) -> None:
    try:
        path_max = os.pathconf(root, "PC_PATH_MAX")
    except (OSError, ValueError) as error:
        _integrity(f"cannot determine the projection path limit: {error}")
    if not isinstance(path_max, int) or path_max <= 1:
        _integrity("projection destination returned an invalid path limit")
    paths = [entry.path for entry in entries]
    paths.append(PurePosixPath(RECEIPT_NAME))
    for relative in paths:
        destination = root.joinpath(*relative.parts)
        try:
            encoded = os.fsencode(destination)
        except UnicodeEncodeError as error:
            _integrity(f"cannot encode projection destination {relative}: {error}")
        if len(encoded) >= path_max:
            _integrity(
                f"projection destination exceeds the filesystem limit at {relative}"
            )


def _require_case_sensitive_filesystem(root: Path) -> None:
    lower = root / ".ziv-sdk-case-probe"
    upper = root / ".ZIV-SDK-CASE-PROBE"
    descriptor = -1
    lower_created = False
    upper_created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(lower, flags, 0o600)
        lower_created = True
        os.close(descriptor)
        descriptor = -1
        descriptor = os.open(upper, flags, 0o600)
        upper_created = True
        os.close(descriptor)
        descriptor = -1
        lower_info = lower.lstat()
        upper_info = upper.lstat()
        if (lower_info.st_dev, lower_info.st_ino) == (
            upper_info.st_dev,
            upper_info.st_ino,
        ):
            _integrity("projection filesystem is not case-sensitive")
    except FileExistsError as error:
        _integrity(f"projection filesystem is not case-sensitive: {error}")
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot probe projection filesystem case behavior: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        for path, created in ((upper, upper_created), (lower, lower_created)):
            if not created:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                _integrity(f"cannot remove projection case probe {path}: {error}")


def _set_metadata(
    path: Path,
    mode: int,
    *,
    symlink: bool = False,
) -> None:
    try:
        os.chown(path, 0, 0, follow_symlinks=False)
        if not symlink:
            os.chmod(path, mode, follow_symlinks=False)
        os.utime(
            path,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
    except (NotImplementedError, OSError) as error:
        _integrity(f"cannot normalize projection metadata for {path}: {error}")


def _materialize_content(
    state: LockedSdkState,
    cache: Path,
    root: Path,
) -> tuple[tuple[ProjectionRecord, ...], dict[str, object]]:
    entries = _projection_entries(state.plans)
    _require_destination_paths_fit(root, entries)
    _require_case_sensitive_filesystem(root)
    directories = sorted(
        (entry for entry in entries if entry.kind == "directory"),
        key=lambda entry: (len(entry.path.parts), _path_sort(entry.path)),
    )
    try:
        for entry in directories:
            path = root.joinpath(*entry.path.parts)
            path.mkdir(mode=0o700, exist_ok=False)
            os.chown(path, 0, 0)
        hashes = _file_hashes(state, cache, destination=root)
        for entry in entries:
            if entry.kind != "symlink":
                continue
            if entry.link is None:
                _integrity(f"projection link {entry.path.as_posix()} has no target")
            path = root.joinpath(*entry.path.parts)
            os.symlink(entry.link, path)
            _set_metadata(path, entry.mode, symlink=True)
        for entry in reversed(directories):
            _set_metadata(root.joinpath(*entry.path.parts), entry.mode)
        _set_metadata(root, 0o700)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot materialize SDK projection content: {error}")
    records = _projection_records(entries, hashes)
    expected_tree = _expected_tree(records)
    actual_tree = environment_tool._scan_tree(root, receipt_name=RECEIPT_NAME)
    if actual_tree != expected_tree:
        _integrity("materialized SDK projection tree differs from its locked content")
    return records, actual_tree


def _write_receipt(root: Path, receipt: dict[str, object]) -> None:
    raw = _canonical_json(receipt)
    if len(raw) > MAX_RECEIPT_BYTES:
        _integrity("SDK projection receipt exceeds its bounded size")
    path = root / RECEIPT_NAME
    if path.exists() or path.is_symlink():
        _integrity("SDK projection receipt already exists")
    environment_tool._write_exclusive(path, raw, 0o644)
    try:
        os.utime(
            path,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
        os.utime(
            root,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
    except OSError as error:
        _integrity(f"cannot normalize SDK projection receipt: {error}")


def _read_receipt(root: Path) -> tuple[dict[str, object], bytes]:
    path = root / RECEIPT_NAME
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"SDK projection receipt is missing: {error}",
            source_tool.EXIT_MISSING,
        ) from error
    except OSError as error:
        _integrity(f"cannot inspect SDK projection receipt: {error}")
    if (
        not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o644
        or (before.st_uid, before.st_gid) != (0, 0)
        or before.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("SDK projection receipt metadata is not canonical")
    xattrs, _xattr_bytes = environment_tool._xattr_digest(path)
    if xattrs is not None:
        _integrity("SDK projection receipt must not carry extended attributes")
    raw = toolchain_tool._read_stable_bytes(
        path,
        maximum=MAX_RECEIPT_BYTES,
        label="SDK projection receipt",
        missing_exit=source_tool.EXIT_MISSING,
    )
    value = environment_tool._read_json_bytes(raw, "SDK projection receipt")
    try:
        after = path.lstat()
    except OSError as error:
        _integrity(f"SDK projection receipt changed while reading: {error}")
    for field in (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_size",
        "st_mtime_ns",
        "st_nlink",
    ):
        if getattr(before, field) != getattr(after, field):
            _integrity("SDK projection receipt metadata changed while reading")
    return value, raw


def _require_linux_root(action: str) -> None:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        _schema(f"SDK projection {action} requires Linux root")


def _resolved_projection_path(path: Path, *, create_parent: bool) -> tuple[Path, Path]:
    parent = materialize_sources._require_real_workspace_parent(
        path,
        create=create_parent,
    )
    return parent, parent / path.name


def _assert_outside_cache(path: Path, cache: Path) -> None:
    cache_root = cache.resolve(strict=True)
    if path == cache_root or cache_root in path.parents or path in cache_root.parents:
        _integrity("SDK projection must stay outside the toolchain cache")


def verify_sdk_projection(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    root: Path,
) -> None:
    _require_linux_root("verification")
    parent, root_path = _resolved_projection_path(root, create_parent=False)
    environment_tool._trusted_output_parent_identity(parent)
    try:
        info = root_path.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"SDK projection is missing: {root_path}",
            source_tool.EXIT_MISSING,
        ) from error
    except OSError as error:
        _integrity(f"cannot inspect SDK projection {root_path}: {error}")
    if not stat.S_ISDIR(info.st_mode) or root_path.is_symlink():
        _integrity(f"SDK projection must be a real directory: {root_path}")
    rootfs_tool._require_ext4(root_path)
    environment_tool._assert_no_nested_mounts(root_path)
    _assert_outside_cache(root_path, cache)
    state = _load_locked_state(manifest, source_manifest, cache)
    entries = _projection_entries(state.plans)
    hashes = _file_hashes(state, cache, destination=None)
    records = _projection_records(entries, hashes)
    expected_tree = _expected_tree(records)
    actual_tree = environment_tool._scan_tree(root_path, receipt_name=RECEIPT_NAME)
    if actual_tree != expected_tree:
        _integrity("SDK projection tree does not match its locked archives")
    actual_receipt, actual_raw = _read_receipt(root_path)
    expected_receipt = _receipt_data(state, records, expected_tree)
    expected_raw = _canonical_json(expected_receipt)
    if actual_receipt != expected_receipt or actual_raw != expected_raw:
        _integrity("SDK projection receipt does not match its locks or tree")
    print(
        f"verified SDK projection: {root_path} "
        f"({expected_tree['entries']} entries, {expected_tree['fileBytes']} file bytes)"
    )


def materialize_sdk_projection(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    output: Path,
) -> None:
    _require_linux_root("materialization")
    parent, output_path = _resolved_projection_path(output, create_parent=False)
    rootfs_tool._require_ext4(parent)
    parent_identity = environment_tool._trusted_output_parent_identity(parent)
    _assert_outside_cache(output_path, cache)
    if output_path.exists() or output_path.is_symlink():
        _integrity(f"refusing to replace existing SDK projection: {output_path}")
    state = _load_locked_state(manifest, source_manifest, cache)
    staging: Path | None = None
    staging_identity: tuple[int, int] | None = None
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

    protected_signals = {signal.SIGINT, *handled_signals}
    try:
        for value in handled_signals:
            signal.signal(value, interrupt_materialization)
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, protected_signals)
        try:
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{output_path.name}.",
                    suffix=".part",
                    dir=parent,
                )
            )
            staging_info = staging.lstat()
            staging_identity = (staging_info.st_dev, staging_info.st_ino)
            staging.chmod(0o700)
            os.chown(staging, 0, 0)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        assert staging is not None and staging_identity is not None
        if (parent.lstat().st_dev, parent.lstat().st_ino) != parent_identity:
            _integrity("SDK projection output parent changed while staging")
        rootfs_tool._require_ext4(staging)
        records, tree = _materialize_content(state, cache, staging)
        receipt = _receipt_data(state, records, tree)
        _write_receipt(staging, receipt)
        verify_sdk_projection(
            manifest,
            source_manifest,
            cache,
            staging,
        )
        environment_tool._sync_filesystem(staging)
        materialize_sources._fsync_workspace_directories(staging)
        current_parent = parent.lstat()
        if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
            _integrity("SDK projection output parent changed before publication")
        post_publish_error: BaseException | None = None
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, protected_signals)
        try:
            materialize_sources._rename_no_replace(staging, output_path)
            published = True
            staging = None
            try:
                materialize_sources._fsync_directory(parent)
                parent_durable = True
            except BaseException as error:
                post_publish_error = error
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        if post_publish_error is not None:
            raise source_tool.SourceToolError(
                f"SDK projection was published at {output_path}, but output-parent "
                f"durability could not be confirmed; verify before retrying: "
                f"{post_publish_error}",
                source_tool.EXIT_INTEGRITY,
            ) from post_publish_error
        print(f"materialized SDK projection: {output_path}")
    except BaseException as error:
        cleanup_error: str | None = None
        cleanup_mask = signal.pthread_sigmask(signal.SIG_BLOCK, protected_signals)
        try:
            if staging is not None and staging_identity is not None:
                cleanup_error = materialize_sources._remove_tree(
                    staging,
                    staging_identity,
                )
        finally:
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_mask)
            except KeyboardInterrupt:
                error = KeyboardInterrupt()
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                f"SDK projection failed and staging cleanup failed at {staging}: "
                f"{cleanup_error}",
                source_tool.EXIT_INTERNAL,
            ) from error
        if published and isinstance(error, KeyboardInterrupt):
            durability = "" if parent_durable else "; parent durability is unconfirmed"
            raise source_tool.SourceToolError(
                f"SDK projection was interrupted after publication at "
                f"{output_path}{durability}; verify it before retrying",
                source_tool.EXIT_INTEGRITY,
            ) from error
        if isinstance(error, (source_tool.SourceToolError, KeyboardInterrupt)):
            raise
        if isinstance(error, Exception):
            raise source_tool.SourceToolError(
                f"SDK projection materialization failed: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
        raise
    finally:
        for value, handler in previous_handlers.items():
            signal.signal(value, handler)


def _plan_summary(state: LockedSdkState) -> dict[str, object]:
    entries = _projection_entries(state.plans)
    digest = hashlib.sha256()
    rootfs_tool._digest_record(digest, b"F", b"ziv-sdk-global-plan-v1")
    for entry in entries:
        rootfs_tool._digest_record(digest, b"E", _entry_plan_bytes(entry))
    return {
        "entries": len(entries),
        "fileBytes": sum(entry.size for entry in entries if entry.kind == "file"),
        "sha256": digest.hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "preflight",
        help="verify locked archives and safely plan the SDK projection",
    )
    materialize = commands.add_parser(
        "materialize",
        help="materialize a fresh Linux/ext4 SDK projection without replacement",
    )
    materialize.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    verify = commands.add_parser(
        "verify",
        help="verify an existing SDK projection against all locked archives",
    )
    verify.add_argument("--root", type=Path, default=DEFAULT_OUTPUT)
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
            summary = _plan_summary(state)
            print(
                f"SDK projection preflight valid: {summary['entries']} entries, "
                f"{summary['fileBytes']} file bytes, plan={summary['sha256']}"
            )
        elif arguments.command == "materialize":
            materialize_sdk_projection(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.output,
            )
        elif arguments.command == "verify":
            verify_sdk_projection(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.root,
            )
        else:  # pragma: no cover - argparse guards this branch
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
