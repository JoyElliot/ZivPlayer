# SPDX-License-Identifier: GPL-3.0-or-later
"""Safely materialize and verify ZivPlayer's locked single-layer OCI rootfs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn

if sys.version_info < (3, 11):
    raise SystemExit("rootfs_tool.py requires Python 3.11 or newer")

import materialize_sources
import source_tool
import toolchain_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_CACHE = NATIVE_DIR / "cache" / "toolchain"
DEFAULT_ROOTFS = Path("/var/tmp/zivplayer-toolchain-base")

RECEIPT_NAME = "ziv-toolchain-base.json"
TREE_FORMAT = "ziv-rootfs-layer-v1"
NORMALIZED_MTIME_NS = 946684800 * 1_000_000_000
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_LAYER_ENTRIES = 100_000
MAX_LAYER_BYTES = toolchain_tool.MAX_ROOTFS_UNCOMPRESSED_BYTES
MAX_PATH_DEPTH = 64
# A relative path still needs at least the leading root slash on Linux. The
# output-specific check below also reserves the actual staging/rootfs prefix.
MAX_PATH_BYTES = 4094
MAX_COMPONENT_BYTES = 255
MAX_TAR_TRAILING_BYTES = 1024 * 1024


@dataclass(frozen=True)
class LayerEntry:
    path: PurePosixPath
    kind: str
    mode: int
    uid: int
    gid: int
    size: int
    sha256: str | None
    link_text: str | None
    link_target: PurePosixPath | None


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _require_ext4(path: Path) -> None:
    findmnt = Path("/usr/bin/findmnt")
    try:
        info = findmnt.lstat()
        if not stat.S_ISREG(info.st_mode) or findmnt.is_symlink():
            _integrity("canonical /usr/bin/findmnt must be a regular file")
        result = subprocess.run(
            [str(findmnt), "--noheadings", "--output", "FSTYPE", "--target", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        _integrity(f"cannot inspect the rootfs output filesystem: {error}")
    filesystem = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode != 0 or filesystem != "ext4":
        _integrity(
            f"base rootfs output must be on ext4, observed {filesystem or 'unknown'}"
        )


def _require_destination_paths_fit(root: Path, entries: list[LayerEntry]) -> None:
    try:
        path_max = os.pathconf(root, "PC_PATH_MAX")
    except (OSError, ValueError) as error:
        _integrity(f"cannot determine the rootfs destination path limit: {error}")
    if not isinstance(path_max, int) or path_max <= 1:
        _integrity("rootfs destination returned an invalid path limit")
    relative_paths = [entry.path for entry in entries]
    relative_paths.append(PurePosixPath(RECEIPT_NAME))
    for relative in relative_paths:
        destination = root.joinpath(*relative.parts)
        try:
            encoded = os.fsencode(destination)
        except UnicodeEncodeError as error:
            _integrity(f"cannot encode rootfs destination {relative}: {error}")
        # POSIX PATH_MAX includes the terminating null byte.
        if len(encoded) >= path_max:
            _integrity(
                "rootfs destination exceeds the filesystem path limit at "
                f"{relative.as_posix()}"
            )


def _safe_member_path(name: str, location: str, *, directory: bool) -> PurePosixPath:
    raw = name[:-1] if directory and name.endswith("/") else name
    if (
        not raw
        or raw.startswith("/")
        or "\\" in raw
        or "\x00" in raw
        or not raw.isprintable()
    ):
        _integrity(f"{location} has an unsafe rootfs path")
    parts = raw.split("/")
    if (
        len(parts) > MAX_PATH_DEPTH
        or len(raw.encode("utf-8")) > MAX_PATH_BYTES
        or any(len(part.encode("utf-8")) > MAX_COMPONENT_BYTES for part in parts)
        or any(part in {"", ".", ".."} for part in parts)
        or any(unicodedata.normalize("NFKC", part) != part for part in parts)
    ):
        _integrity(f"{location} has a non-canonical rootfs path")
    return PurePosixPath(*parts)


def _normalized_path_key(path: PurePosixPath) -> str:
    return "/".join(
        unicodedata.normalize("NFKC", part).casefold() for part in path.parts
    )


def _resolve_symlink_target(
    path: PurePosixPath,
    target: str,
    location: str,
) -> PurePosixPath:
    if not target or "\\" in target or "\x00" in target or not target.isprintable():
        _integrity(f"{location} has an unsafe symbolic-link target")
    if len(target.encode("utf-8")) > MAX_PATH_BYTES:
        _integrity(f"{location} symbolic-link target is too long")
    absolute = target.startswith("/")
    parts = target.split("/")
    if absolute:
        parts = parts[1:]
    if any(part in {"", "."} for part in parts):
        _integrity(f"{location} has a non-canonical symbolic-link target")
    if any(len(part.encode("utf-8")) > MAX_COMPONENT_BYTES for part in parts):
        _integrity(f"{location} symbolic-link target component is too long")
    resolved = [] if absolute else list(path.parent.parts)
    for part in parts:
        if part == "..":
            if not resolved:
                _integrity(f"{location} symbolic link escapes the rootfs")
            resolved.pop()
        else:
            if unicodedata.normalize("NFKC", part) != part:
                _integrity(f"{location} has a non-canonical symbolic-link target")
            resolved.append(part)
        if len(resolved) > MAX_PATH_DEPTH:
            _integrity(f"{location} symbolic-link target is too deep")
    if not resolved:
        return PurePosixPath(".")
    return PurePosixPath(*resolved)


def _classify(info: tarfile.TarInfo, location: str) -> str:
    if info.isdir():
        return "directory"
    if info.isreg():
        return "file"
    if info.islnk():
        return "hardlink"
    if info.issym():
        return "symlink"
    _integrity(f"{location} has an unsupported rootfs entry type")


def _hash_exact_stream(stream: BinaryIO, size: int, location: str) -> str:
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            _integrity(f"{location} ended before its declared size")
        remaining -= len(chunk)
        digest.update(chunk)
    if stream.read(1):
        _integrity(f"{location} exceeds its declared size")
    return digest.hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            _integrity("cannot make progress while writing the rootfs")
        remaining = remaining[written:]


def _consume_tar_trailer(archive: tarfile.TarFile, label: str) -> None:
    trailing = 0
    while True:
        chunk = archive.fileobj.read(1024 * 1024)
        if not chunk:
            break
        trailing += len(chunk)
        if trailing > MAX_TAR_TRAILING_BYTES:
            _integrity(f"{label} exceeds the TAR trailing-padding limit")
        if any(chunk):
            _integrity(f"{label} has non-zero bytes after the TAR end marker")
    if trailing < tarfile.BLOCKSIZE:
        _integrity(f"{label} is missing its second TAR end marker")


def _layer_entry(
    info: tarfile.TarInfo,
    index: int,
    archive: tarfile.TarFile,
) -> LayerEntry:
    location = f"OCI rootfs member[{index}]"
    kind = _classify(info, location)
    path = _safe_member_path(info.name, location, directory=kind == "directory")
    if path.name.startswith(".wh."):
        _integrity("single-layer OCI rootfs must not contain whiteout entries")
    if info.pax_headers or info.sparse is not None:
        _integrity(f"{location} uses unsupported PAX or sparse metadata")
    if info.uid < 0 or info.gid < 0 or info.uid > 2**31 - 1 or info.gid > 2**31 - 1:
        _integrity(f"{location} has an invalid numeric owner")
    if info.mode < 0 or info.mode & ~0o7777:
        _integrity(f"{location} has an invalid permission mode")
    mode = info.mode
    size = 0
    sha256: str | None = None
    link_text: str | None = None
    link_target: PurePosixPath | None = None
    if kind == "file":
        if info.size < 0 or info.size > MAX_LAYER_BYTES:
            _integrity(f"{location} has an invalid file size")
        extracted = archive.extractfile(info)
        if extracted is None:
            _integrity(f"cannot read {location}")
        size = info.size
        sha256 = _hash_exact_stream(extracted, size, location)
    elif kind == "symlink":
        link_text = info.linkname
        link_target = _resolve_symlink_target(path, info.linkname, location)
    elif kind == "hardlink":
        link_text = info.linkname
        link_target = _safe_member_path(
            info.linkname,
            f"{location} hard-link target",
            directory=False,
        )
    return LayerEntry(
        path=path,
        kind=kind,
        mode=mode,
        uid=info.uid,
        gid=info.gid,
        size=size,
        sha256=sha256,
        link_text=link_text,
        link_target=link_target,
    )


def _plan_layer(
    layer: dict[str, object],
    cache: Path,
) -> list[LayerEntry]:
    locked = {
        "id": layer["id"],
        "size": layer["size"],
        "sha256": str(layer["digest"]).removeprefix("sha256:"),
    }
    path = cache / str(layer["archive"])
    entries: list[LayerEntry] = []
    by_path: dict[PurePosixPath, LayerEntry] = {}
    normalized: dict[str, PurePosixPath] = {}
    normalized_prefixes: dict[str, PurePosixPath] = {}
    receipt_key = unicodedata.normalize("NFKC", RECEIPT_NAME).casefold()
    total_bytes = 0
    with source_tool.open_verified_archive(locked, path) as stream:
        with materialize_sources._open_bounded_tar(
            stream,
            str(layer["id"]),
            int(layer["size"]),
        ) as archive:
            for index, info in enumerate(archive):
                if index >= MAX_LAYER_ENTRIES:
                    _integrity("OCI rootfs exceeds the entry-count limit")
                entry = _layer_entry(info, index, archive)
                if entry.path in by_path:
                    _integrity(f"OCI rootfs repeats path {entry.path.as_posix()}")
                key = _normalized_path_key(entry.path)
                previous = normalized.get(key)
                if previous is not None and previous != entry.path:
                    _integrity(
                        "OCI rootfs has a case/Unicode path collision: "
                        f"{previous.as_posix()} and {entry.path.as_posix()}"
                    )
                normalized[key] = entry.path
                if (
                    key == receipt_key
                    or unicodedata.normalize("NFKC", entry.path.parts[0]).casefold()
                    == receipt_key
                ):
                    _integrity("OCI rootfs collides with the canonical base receipt")
                for prefix_length in range(1, len(entry.path.parts) + 1):
                    prefix = PurePosixPath(*entry.path.parts[:prefix_length])
                    prefix_key = _normalized_path_key(prefix)
                    previous_prefix = normalized_prefixes.get(prefix_key)
                    if previous_prefix is not None and previous_prefix != prefix:
                        _integrity(
                            "OCI rootfs has a case/Unicode prefix collision: "
                            f"{previous_prefix.as_posix()} and {prefix.as_posix()}"
                        )
                    if previous_prefix is None:
                        normalized_prefixes[prefix_key] = prefix
                        if len(normalized_prefixes) > MAX_LAYER_ENTRIES:
                            _integrity("OCI rootfs exceeds the planned-entry limit")
                by_path[entry.path] = entry
                entries.append(entry)
                total_bytes += entry.size
                if total_bytes > MAX_LAYER_BYTES:
                    _integrity("OCI rootfs exceeds the uncompressed file-byte limit")
            _consume_tar_trailer(archive, "OCI rootfs layer")
    if not entries:
        _integrity("OCI rootfs layer is empty")

    for entry in list(entries):
        for parent in entry.path.parents:
            if parent == PurePosixPath("."):
                break
            ancestor = by_path.get(parent)
            if ancestor is None:
                synthetic = LayerEntry(
                    path=parent,
                    kind="directory",
                    mode=0o755,
                    uid=0,
                    gid=0,
                    size=0,
                    sha256=None,
                    link_text=None,
                    link_target=None,
                )
                by_path[parent] = synthetic
                entries.append(synthetic)
                if len(by_path) > MAX_LAYER_ENTRIES:
                    _integrity("OCI rootfs exceeds the planned-entry limit")
            elif ancestor.kind != "directory":
                _integrity(
                    f"OCI rootfs places {entry.path.as_posix()} below non-directory "
                    f"{parent.as_posix()}"
                )
        if entry.kind == "hardlink":
            target = by_path.get(entry.link_target)
            if target is None or target.kind != "file":
                _integrity(
                    f"OCI rootfs hard link {entry.path.as_posix()} must target a file"
                )
            if (entry.mode, entry.uid, entry.gid) != (
                target.mode,
                target.uid,
                target.gid,
            ):
                _integrity("OCI rootfs hard-link metadata differs from its target")
    entries.sort(key=lambda entry: entry.path.as_posix().encode("utf-8"))
    return entries


def _digest_record(digest: object, tag: bytes, value: bytes) -> None:
    digest.update(tag)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _entry_bytes(entry: LayerEntry) -> bytes:
    fields = [
        entry.path.as_posix(),
        entry.kind,
        format(entry.mode, "04o"),
        str(entry.uid),
        str(entry.gid),
        str(entry.size),
        entry.sha256 or "-",
        entry.link_text or "-",
    ]
    return "\t".join(fields).encode("utf-8")


def _tree_record(entries: list[LayerEntry]) -> dict[str, object]:
    digest = hashlib.sha256()
    _digest_record(digest, b"F", TREE_FORMAT.encode("ascii"))
    counts = {"directories": 0, "files": 0, "hardlinks": 0, "symlinks": 0}
    count_keys = {
        "directory": "directories",
        "file": "files",
        "hardlink": "hardlinks",
        "symlink": "symlinks",
    }
    total_bytes = 0
    for entry in entries:
        _digest_record(digest, b"E", _entry_bytes(entry))
        counts[count_keys[entry.kind]] += 1
        total_bytes += entry.size
    return {
        "format": TREE_FORMAT,
        "sha256": digest.hexdigest(),
        "entries": len(entries),
        "fileBytes": total_bytes,
        **counts,
    }


def _receipt_data(
    data: dict[str, object],
    layer: dict[str, object],
    manifest_sha256: str,
    entries: list[LayerEntry],
) -> dict[str, object]:
    project = data["project"]
    assert isinstance(project, dict)
    return {
        "schemaVersion": 1,
        "kind": "ziv-toolchain-base",
        "manifestSha256": manifest_sha256,
        "sourceManifestSha256": project["sourceManifestSha256"],
        "platform": project["hostPlatform"],
        "policy": {
            "layerCount": 1,
            "whiteouts": "reject",
            "specialEntries": "reject",
            "rootMode": "0700",
            "filesystem": "ext4",
            "normalizedMtimeNs": NORMALIZED_MTIME_NS,
        },
        "layer": {
            "id": layer["id"],
            "archive": layer["archive"],
            "size": layer["size"],
            "digest": layer["digest"],
        },
        "tree": _tree_record(entries),
    }


def _set_metadata(path: Path, entry: LayerEntry, *, symlink: bool = False) -> None:
    try:
        os.chown(path, entry.uid, entry.gid, follow_symlinks=False)
        # chown can clear setuid/setgid bits, so restore the locked mode after
        # ownership is final.
        if not symlink:
            os.chmod(path, entry.mode, follow_symlinks=False)
        os.utime(
            path,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
    except (NotImplementedError, OSError) as error:
        _integrity(f"cannot normalize rootfs metadata for {entry.path}: {error}")


def _write_regular_files(
    staging: Path,
    layer: dict[str, object],
    cache: Path,
    by_path: dict[PurePosixPath, LayerEntry],
) -> None:
    locked = {
        "id": layer["id"],
        "size": layer["size"],
        "sha256": str(layer["digest"]).removeprefix("sha256:"),
    }
    seen: set[PurePosixPath] = set()
    with source_tool.open_verified_archive(
        locked,
        cache / str(layer["archive"]),
    ) as stream:
        with materialize_sources._open_bounded_tar(
            stream,
            str(layer["id"]),
            int(layer["size"]),
        ) as archive:
            for index, info in enumerate(archive):
                if not info.isreg():
                    continue
                path = _safe_member_path(
                    info.name,
                    f"OCI rootfs member[{index}]",
                    directory=False,
                )
                entry = by_path.get(path)
                if entry is None or entry.kind != "file" or path in seen:
                    _integrity("OCI rootfs changed between planning and extraction")
                source = archive.extractfile(info)
                if source is None:
                    _integrity(f"cannot extract OCI rootfs file {path.as_posix()}")
                destination = staging.joinpath(*path.parts)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(destination, flags, entry.mode or 0o600)
                digest = hashlib.sha256()
                remaining = entry.size
                try:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            _integrity(f"rootfs file {path.as_posix()} ended early")
                        _write_all(descriptor, chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if source.read(1):
                        _integrity(f"rootfs file {path.as_posix()} exceeds its lock")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                if digest.hexdigest() != entry.sha256:
                    _integrity(f"rootfs file digest changed for {path.as_posix()}")
                _set_metadata(destination, entry)
                seen.add(path)
            _consume_tar_trailer(archive, "OCI rootfs layer")
    expected = {entry.path for entry in by_path.values() if entry.kind == "file"}
    if seen != expected:
        _integrity("OCI rootfs extraction omitted a regular file")


def _canonical_receipt_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _read_receipt(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        info = path.lstat()
    except OSError as error:
        raise source_tool.SourceToolError(
            f"rootfs base receipt is missing: {error}",
            source_tool.EXIT_MISSING,
        ) from error
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or stat.S_IMODE(info.st_mode) != 0o644
        or (info.st_uid, info.st_gid) != (0, 0)
        or info.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("rootfs base receipt metadata is not canonical")
    raw = toolchain_tool._read_stable_bytes(
        path,
        maximum=MAX_RECEIPT_BYTES,
        label="rootfs base receipt",
        missing_exit=source_tool.EXIT_MISSING,
    )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=toolchain_tool._json_without_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        _integrity(f"cannot decode rootfs base receipt: {error}")
    if not isinstance(value, dict):
        _integrity("rootfs base receipt must be a JSON object")
    try:
        current = path.lstat()
    except OSError as error:
        _integrity(f"rootfs base receipt changed while reading: {error}")
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_mtime_ns",
    )
    if any(getattr(info, field) != getattr(current, field) for field in stable_fields):
        _integrity("rootfs base receipt metadata changed while reading")
    return value, raw


def _verify_materialized_tree(rootfs: Path, entries: list[LayerEntry]) -> None:
    expected = {entry.path: entry for entry in entries}
    observed: set[PurePosixPath] = set()
    for directory, names, files in os.walk(rootfs, topdown=True, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(rootfs)
        combined = sorted(names + files, key=lambda value: value.encode("utf-8"))
        names[:] = [name for name in names if not (current / name).is_symlink()]
        for name in combined:
            relative = PurePosixPath(*(relative_dir / name).parts)
            if relative.as_posix() == RECEIPT_NAME:
                continue
            entry = expected.get(relative)
            if entry is None or relative in observed:
                _integrity(f"materialized rootfs has an unlocked path: {relative}")
            path = rootfs.joinpath(*relative.parts)
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if entry.kind == "directory":
                matches = stat.S_ISDIR(info.st_mode) and not path.is_symlink()
            elif entry.kind == "file":
                matches = stat.S_ISREG(info.st_mode) and not path.is_symlink()
            elif entry.kind == "hardlink":
                matches = stat.S_ISREG(info.st_mode) and not path.is_symlink()
            else:
                matches = stat.S_ISLNK(info.st_mode)
            if not matches:
                _integrity(f"materialized rootfs type mismatch at {relative}")
            if entry.kind != "symlink" and mode != entry.mode:
                _integrity(f"materialized rootfs mode mismatch at {relative}")
            if (info.st_uid, info.st_gid) != (entry.uid, entry.gid):
                _integrity(f"materialized rootfs owner mismatch at {relative}")
            if info.st_mtime_ns != NORMALIZED_MTIME_NS:
                _integrity(f"materialized rootfs timestamp mismatch at {relative}")
            if entry.kind in {"file", "hardlink"}:
                expected_file = entry
                if entry.kind == "hardlink":
                    expected_file = expected[entry.link_target]
                    target = rootfs.joinpath(*entry.link_target.parts)
                    target_info = target.lstat()
                    if (info.st_dev, info.st_ino) != (target_info.st_dev, target_info.st_ino):
                        _integrity(f"materialized hard link differs at {relative}")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if info.st_size != expected_file.size or digest.hexdigest() != expected_file.sha256:
                    _integrity(f"materialized rootfs file mismatch at {relative}")
            elif entry.kind == "symlink" and os.readlink(path) != entry.link_text:
                _integrity(f"materialized symbolic link mismatch at {relative}")
            observed.add(relative)
    missing = sorted(set(expected) - observed, key=lambda value: value.as_posix())
    if missing:
        _integrity(f"materialized rootfs is missing {missing[0].as_posix()}")


def _load_and_plan(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    *,
    verify_all_cache: bool,
) -> tuple[dict[str, object], dict[str, object], list[LayerEntry], str]:
    (
        data,
        artifacts,
        oci_objects,
        _source_data,
        manifest_digest,
    ) = toolchain_tool.load_manifest_snapshot(
        manifest,
        source_manifest_path=source_manifest,
    )
    if verify_all_cache:
        toolchain_tool.verify_cache(data, artifacts, oci_objects, cache)
    else:
        toolchain_tool.verify_roots(data, artifacts, oci_objects, cache)
    layer = next(
        entry for entry in oci_objects if entry["id"] == "ubuntu-layer-amd64"
    )
    entries = _plan_layer(layer, cache)
    return data, layer, entries, manifest_digest


def materialize_base(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    output: Path,
) -> None:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        _schema("base rootfs materialization requires Linux root")
    data, layer, entries, manifest_digest = _load_and_plan(
        manifest,
        source_manifest,
        cache,
        verify_all_cache=True,
    )
    parent = materialize_sources._require_real_workspace_parent(output, create=False)
    _require_ext4(parent)
    cache_root = cache.resolve(strict=True)
    output_path = parent / output.name
    if output_path == cache_root or cache_root in output_path.parents:
        _integrity("base rootfs output must stay outside the toolchain cache")
    parent_info = parent.lstat()
    parent_mode = stat.S_IMODE(parent_info.st_mode)
    if parent_info.st_uid != 0 or (
        parent_mode & 0o022 and not parent_mode & stat.S_ISVTX
    ):
        _integrity(
            "base rootfs output parent must be root-owned and either private or sticky"
        )
    parent_identity = (parent_info.st_dev, parent_info.st_ino)
    if output.exists() or output.is_symlink():
        _integrity(f"refusing to replace existing rootfs: {output}")
    staging: Path | None = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.",
            suffix=".part",
            dir=parent,
        )
    )
    identity = (staging.lstat().st_dev, staging.lstat().st_ino)
    try:
        current_parent = parent.lstat()
        if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
            _integrity("base rootfs output parent changed while creating staging")
        # The locked base contains setuid/setgid programs. Keep the entire
        # staging tree root-only so those files are never executable by other
        # local users before a later private nosuid sandbox consumes it.
        staging.chmod(0o700)
        os.chown(staging, 0, 0)
        _require_destination_paths_fit(staging, entries)
        by_path = {entry.path: entry for entry in entries}
        directories = sorted(
            (entry for entry in entries if entry.kind == "directory"),
            key=lambda entry: (len(entry.path.parts), entry.path.as_posix()),
        )
        for entry in directories:
            staging.joinpath(*entry.path.parts).mkdir(exist_ok=False)
        _write_regular_files(staging, layer, cache, by_path)
        for entry in entries:
            destination = staging.joinpath(*entry.path.parts)
            if entry.kind == "hardlink":
                target = staging.joinpath(*entry.link_target.parts)
                os.link(target, destination, follow_symlinks=False)
            elif entry.kind == "symlink":
                os.symlink(entry.link_text, destination)
                _set_metadata(destination, entry, symlink=True)
        for entry in reversed(directories):
            _set_metadata(staging.joinpath(*entry.path.parts), entry)
        os.utime(
            staging,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
        _verify_materialized_tree(staging, entries)
        receipt = _receipt_data(data, layer, manifest_digest, entries)
        receipt_path = staging / RECEIPT_NAME
        with receipt_path.open("xb") as stream:
            stream.write(_canonical_receipt_bytes(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        receipt_path.chmod(0o644)
        os.chown(receipt_path, 0, 0)
        os.utime(
            receipt_path,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
        staging.chmod(0o700)
        os.chown(staging, 0, 0)
        os.utime(
            staging,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            follow_symlinks=False,
        )
        materialize_sources._fsync_workspace_directories(staging)
        current_parent = parent.lstat()
        if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
            _integrity("base rootfs output parent changed before publication")
        materialize_sources._rename_no_replace(staging, output)
        staging = None
        try:
            materialize_sources._fsync_directory(parent)
        except source_tool.SourceToolError as error:
            raise source_tool.SourceToolError(
                f"base rootfs was published at {output}, but output-parent durability "
                "could not be confirmed; do not retry blindly because the destination "
                "now exists. Verify it, then explicitly keep or remove it before "
                f"retrying: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
        print(f"materialized locked base rootfs: {output}")
    except BaseException as error:
        cleanup_error = (
            materialize_sources._remove_tree(staging, identity)
            if staging is not None
            else None
        )
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                f"base rootfs materialization failed and staging cleanup failed: "
                f"{cleanup_error}",
                source_tool.EXIT_INTERNAL,
            ) from error
        if isinstance(error, (source_tool.SourceToolError, KeyboardInterrupt)):
            raise
        if isinstance(error, Exception):
            raise source_tool.SourceToolError(
                f"base rootfs materialization failed: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
        raise


def verify_base(
    manifest: Path,
    source_manifest: Path,
    cache: Path,
    rootfs: Path,
) -> None:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        _schema("base rootfs verification requires Linux root")
    parent = materialize_sources._require_real_workspace_parent(rootfs, create=False)
    rootfs_path = parent / rootfs.name
    if not rootfs_path.is_dir() or rootfs_path.is_symlink():
        raise source_tool.SourceToolError(
            f"base rootfs is missing or not a real directory: {rootfs_path}",
            source_tool.EXIT_MISSING,
        )
    _require_ext4(rootfs_path)
    data, layer, entries, manifest_digest = _load_and_plan(
        manifest,
        source_manifest,
        cache,
        verify_all_cache=False,
    )
    cache_root = cache.resolve(strict=True)
    if rootfs_path == cache_root or cache_root in rootfs_path.parents:
        _integrity("base rootfs must stay outside the toolchain cache")
    _require_destination_paths_fit(rootfs_path, entries)
    root_info = rootfs_path.lstat()
    if (
        stat.S_IMODE(root_info.st_mode) != 0o700
        or (root_info.st_uid, root_info.st_gid) != (0, 0)
        or root_info.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("materialized rootfs root metadata is not canonical")
    _verify_materialized_tree(rootfs_path, entries)
    actual_receipt, actual_receipt_bytes = _read_receipt(
        rootfs_path / RECEIPT_NAME
    )
    expected_receipt = _receipt_data(data, layer, manifest_digest, entries)
    if (
        actual_receipt != expected_receipt
        or actual_receipt_bytes != _canonical_receipt_bytes(expected_receipt)
    ):
        _integrity("rootfs base receipt does not match the locked layer")
    print(f"verified locked base rootfs: {rootfs_path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="verify inputs and safely plan the OCI layer")
    materialize = subparsers.add_parser(
        "materialize-base",
        help="materialize a fresh base rootfs and receipt without replacement",
    )
    materialize.add_argument("--output", type=Path, default=DEFAULT_ROOTFS)
    verify = subparsers.add_parser(
        "verify-base",
        help="verify an unmodified materialized base rootfs and receipt",
    )
    verify.add_argument("--rootfs", type=Path, default=DEFAULT_ROOTFS)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "preflight":
            _data, _layer, entries, _manifest_digest = _load_and_plan(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                verify_all_cache=True,
            )
            tree = _tree_record(entries)
            print(
                f"rootfs preflight valid: {tree['entries']} entries, "
                f"{tree['fileBytes']} file bytes, tree={tree['sha256']}"
            )
        elif arguments.command == "materialize-base":
            materialize_base(
                arguments.manifest,
                arguments.source_manifest,
                arguments.cache,
                arguments.output,
            )
        elif arguments.command == "verify-base":
            verify_base(
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
