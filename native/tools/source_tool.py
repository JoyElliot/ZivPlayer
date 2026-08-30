# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate, fetch, and verify ZivPlayer's locked native source inputs."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from contextlib import contextmanager
from typing import BinaryIO, Iterator, NoReturn

if sys.version_info < (3, 11):
    raise SystemExit("source_tool.py requires Python 3.11 or newer")

import tomllib


EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_SCHEMA = 2
EXIT_MISSING = 3
EXIT_INTEGRITY = 4
EXIT_NETWORK = 5

NATIVE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_CACHE = NATIVE_DIR / "cache" / "sources"
MAX_SOURCE_ARCHIVE_BYTES = 512 * 1024 * 1024

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
PORTABLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

TOP_LEVEL_KEYS = {"schemaVersion", "project", "toolchain", "source"}
PROJECT_KEYS = {
    "name",
    "upstreamRelease",
    "upstreamRevision",
    "minimumAndroidApi",
    "nativeApi",
    "abis",
    "pageSizeBytes",
    "licenseProfile",
}
TOOLCHAIN_KEYS = {
    "hostOs",
    "ndkVersion",
    "sdkPlatform",
    "sdkBuildTools",
    "jdkMajor",
    "pythonMinimum",
    "mesonMinimum",
    "ninjaMinimum",
    "gnuMakeMinimum",
    "buildSystems",
    "cmake",
    "environmentStatus",
}
SOURCE_REQUIRED_KEYS = {
    "id",
    "version",
    "kind",
    "url",
    "archive",
    "size",
    "sha256",
    "revision",
    "license",
    "licenseFiles",
    "role",
    "linkage",
    "buildSystem",
    "dependencies",
    "requiredForBuild",
}
SOURCE_OPTIONAL_KEYS = {"parent", "destination", "notes", "selectedBuildLicense"}
SOURCE_KEYS = SOURCE_REQUIRED_KEYS | SOURCE_OPTIONAL_KEYS

KINDS = {"git-snapshot", "release-archive"}
LINKAGES = {
    "build-tool",
    "jni-shared",
    "shared",
    "source-only",
    "static",
    "unused-upstream-submodule",
}
MUTABLE_REVISIONS = {"head", "latest", "main", "master", "tip", "trunk"}
CANONICAL_SOURCE_IDS = (
    "curl",
    "dav1d",
    "dlg",
    "fast-float",
    "ffmpeg",
    "fontconfig",
    "freetype",
    "fribidi",
    "gas-preprocessor",
    "glad",
    "harfbuzz",
    "jinja",
    "libass",
    "libplacebo",
    "libunibreak",
    "libxml2",
    "lua",
    "markupsafe",
    "mbedtls",
    "mpv",
    "mpv-android",
    "nuklear",
    "vulkan-headers",
)


class SourceToolError(Exception):
    """An expected failure with a stable command-line exit code."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _schema(message: str) -> NoReturn:
    raise SourceToolError(message, EXIT_SCHEMA)


def _validate_https_url(
    url: str,
    location: str,
    *,
    allow_query: bool,
    exit_code: int,
) -> urllib.parse.SplitResult:
    if url != url.strip() or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in url
    ):
        raise SourceToolError(f"{location} contains whitespace or control characters", exit_code)
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
    except ValueError as error:
        raise SourceToolError(f"{location} is not a valid URL: {error}", exit_code) from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.query and not allow_query)
        or parsed.fragment
    ):
        query_rule = "" if allow_query else " or query"
        raise SourceToolError(
            f"{location} must be credential-free HTTPS with a host and no fragment{query_rule}",
            exit_code,
        )
    return parsed


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject HTTPS downgrade and credential-bearing URLs at every redirect."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request | None:
        absolute_url = urllib.parse.urljoin(request.full_url, new_url)
        _validate_https_url(
            absolute_url,
            "download redirect",
            allow_query=True,
            exit_code=EXIT_NETWORK,
        )
        return super().redirect_request(request, file_pointer, code, message, headers, absolute_url)


HTTPS_OPENER = urllib.request.build_opener(HTTPSOnlyRedirectHandler())


def _open_https(request: urllib.request.Request, timeout: float) -> object:
    return HTTPS_OPENER.open(request, timeout=timeout)


def _expect_table(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _schema(f"{location} must be a TOML table")
    return value


def _expect_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        _schema(f"{location} must be a non-empty string")
    return value


def _expect_int(value: object, location: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _schema(f"{location} must be an integer >= {minimum}")
    return value


def _expect_bool(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        _schema(f"{location} must be a boolean")
    return value


def _expect_string_list(value: object, location: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        suffix = "" if allow_empty else " and non-empty"
        _schema(f"{location} must be a string array{suffix}")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_expect_string(item, f"{location}[{index}]"))
    if len(result) != len(set(result)):
        _schema(f"{location} must not contain duplicates")
    return result


def _check_exact_keys(
    table: dict[str, object],
    location: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - table.keys())
    unknown = sorted(table.keys() - required - optional)
    if missing:
        _schema(f"{location} is missing keys: {', '.join(missing)}")
    if unknown:
        _schema(f"{location} has unknown keys: {', '.join(unknown)}")


def _check_safe_relative_path(value: str, location: str) -> None:
    path = PurePosixPath(value)
    raw_parts = value.split("/")
    if (
        path.is_absolute()
        or value in {"", ".", ".."}
        or "\\" in value
        or any(part in {"", ".", ".."} for part in raw_parts)
        or value != "/".join(path.parts)
    ):
        _schema(f"{location} must be a safe POSIX relative path")
    for part in path.parts:
        _check_portable_name(part, location)


def _check_portable_name(value: str, location: str) -> None:
    if (
        len(value) > 200
        or not PORTABLE_NAME_RE.fullmatch(value)
        or value.endswith((".", " "))
        or value.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
    ):
        _schema(f"{location} must be a portable ASCII file name")


def _validate_project(value: object) -> dict[str, object]:
    project = _expect_table(value, "project")
    _check_exact_keys(project, "project", PROJECT_KEYS)

    if _expect_string(project["name"], "project.name") != "ZivPlayer":
        _schema("project.name must be ZivPlayer")
    _expect_string(project["upstreamRelease"], "project.upstreamRelease")
    revision = _expect_string(project["upstreamRevision"], "project.upstreamRevision")
    if not COMMIT_RE.fullmatch(revision):
        _schema("project.upstreamRevision must be a lowercase 40-hex commit")

    minimum_api = _expect_int(project["minimumAndroidApi"], "project.minimumAndroidApi", minimum=1)
    native_api = _expect_int(project["nativeApi"], "project.nativeApi", minimum=1)
    if minimum_api != 26 or native_api != minimum_api:
        _schema("project minimumAndroidApi and nativeApi must both be 26")

    abis = _expect_string_list(project["abis"], "project.abis", allow_empty=False)
    if abis != ["arm64-v8a", "x86_64"]:
        _schema("project.abis must be ordered as arm64-v8a, x86_64")
    if _expect_int(project["pageSizeBytes"], "project.pageSizeBytes", minimum=1) != 16384:
        _schema("project.pageSizeBytes must be 16384")
    _expect_string(project["licenseProfile"], "project.licenseProfile")
    return project


def _validate_toolchain(value: object) -> dict[str, object]:
    toolchain = _expect_table(value, "toolchain")
    _check_exact_keys(toolchain, "toolchain", TOOLCHAIN_KEYS)

    if _expect_string(toolchain["hostOs"], "toolchain.hostOs") != "linux":
        _schema("toolchain.hostOs must be linux")
    if _expect_string(toolchain["ndkVersion"], "toolchain.ndkVersion") != "29.0.14206865":
        _schema("toolchain.ndkVersion must be 29.0.14206865")
    if _expect_int(toolchain["sdkPlatform"], "toolchain.sdkPlatform", minimum=1) != 36:
        _schema("toolchain.sdkPlatform must be 36")
    if _expect_string(toolchain["sdkBuildTools"], "toolchain.sdkBuildTools") != "36.0.0":
        _schema("toolchain.sdkBuildTools must be 36.0.0")
    if _expect_int(toolchain["jdkMajor"], "toolchain.jdkMajor", minimum=1) != 17:
        _schema("toolchain.jdkMajor must be 17")

    for key in ("pythonMinimum", "mesonMinimum", "ninjaMinimum", "gnuMakeMinimum"):
        version = _expect_string(toolchain[key], f"toolchain.{key}")
        if not VERSION_RE.fullmatch(version):
            _schema(f"toolchain.{key} is not a valid version")

    build_systems = _expect_string_list(
        toolchain["buildSystems"], "toolchain.buildSystems", allow_empty=False
    )
    if build_systems != sorted(build_systems):
        _schema("toolchain.buildSystems must be sorted")
    if _expect_string(toolchain["cmake"], "toolchain.cmake") != "not-used":
        _schema("toolchain.cmake must be not-used for the locked upstream pipeline")
    _expect_string(toolchain["environmentStatus"], "toolchain.environmentStatus")
    return toolchain


def _validate_source(value: object, index: int) -> dict[str, object]:
    location = f"source[{index}]"
    source = _expect_table(value, location)
    _check_exact_keys(source, location, SOURCE_REQUIRED_KEYS, SOURCE_OPTIONAL_KEYS)

    source_id = _expect_string(source["id"], f"{location}.id")
    if not ID_RE.fullmatch(source_id):
        _schema(f"{location}.id must match {ID_RE.pattern}")
    _check_portable_name(source_id, f"{location}.id")
    version = _expect_string(source["version"], f"{location}.version")
    if not VERSION_RE.fullmatch(version):
        _schema(f"{location}.version is not a valid immutable label")

    kind = _expect_string(source["kind"], f"{location}.kind")
    if kind not in KINDS:
        _schema(f"{location}.kind must be one of: {', '.join(sorted(KINDS))}")
    revision = _expect_string(source["revision"], f"{location}.revision")
    if revision.lower() in MUTABLE_REVISIONS:
        _schema(f"{location}.revision must not be a mutable ref")
    if kind == "git-snapshot" and not COMMIT_RE.fullmatch(revision):
        _schema(f"{location}.revision must be a lowercase 40-hex commit")
    if kind == "release-archive" and not VERSION_RE.fullmatch(revision):
        _schema(f"{location}.revision must be an immutable release label")

    url = _expect_string(source["url"], f"{location}.url")
    _validate_https_url(
        url,
        f"{location}.url",
        allow_query=False,
        exit_code=EXIT_SCHEMA,
    )

    archive = _expect_string(source["archive"], f"{location}.archive")
    if Path(archive).name != archive or "/" in archive or "\\" in archive or archive in {".", ".."}:
        _schema(f"{location}.archive must be a safe basename")
    _check_portable_name(archive, f"{location}.archive")
    source_size = _expect_int(source["size"], f"{location}.size", minimum=1)
    if source_size > MAX_SOURCE_ARCHIVE_BYTES:
        _schema(
            f"{location}.size must be <= {MAX_SOURCE_ARCHIVE_BYTES} bytes"
        )
    digest = _expect_string(source["sha256"], f"{location}.sha256")
    if not SHA256_RE.fullmatch(digest):
        _schema(f"{location}.sha256 must be a lowercase 64-hex digest")

    _expect_string(source["license"], f"{location}.license")
    license_files = _expect_string_list(
        source["licenseFiles"], f"{location}.licenseFiles", allow_empty=False
    )
    for item_index, path in enumerate(license_files):
        _check_safe_relative_path(path, f"{location}.licenseFiles[{item_index}]")

    _expect_string(source["role"], f"{location}.role")
    linkage = _expect_string(source["linkage"], f"{location}.linkage")
    if linkage not in LINKAGES:
        _schema(f"{location}.linkage must be one of: {', '.join(sorted(LINKAGES))}")
    _expect_string(source["buildSystem"], f"{location}.buildSystem")
    _expect_string_list(source["dependencies"], f"{location}.dependencies", allow_empty=True)
    _expect_bool(source["requiredForBuild"], f"{location}.requiredForBuild")

    if "parent" in source:
        parent = _expect_string(source["parent"], f"{location}.parent")
        if not ID_RE.fullmatch(parent):
            _schema(f"{location}.parent must be a source id")
    if "destination" in source:
        destination = _expect_string(source["destination"], f"{location}.destination")
        _check_safe_relative_path(destination, f"{location}.destination")
    if ("parent" in source) != ("destination" in source):
        _schema(f"{location}.parent and destination must appear together")
    if "notes" in source:
        _expect_string(source["notes"], f"{location}.notes")
    if "selectedBuildLicense" in source:
        _expect_string(source["selectedBuildLicense"], f"{location}.selectedBuildLicense")
    return source


def validate_manifest_data(data: object) -> list[dict[str, object]]:
    manifest = _expect_table(data, "manifest")
    _check_exact_keys(manifest, "manifest", TOP_LEVEL_KEYS)
    if _expect_int(manifest["schemaVersion"], "schemaVersion", minimum=1) != 1:
        _schema("schemaVersion must be 1")
    _validate_project(manifest["project"])
    _validate_toolchain(manifest["toolchain"])

    raw_sources = manifest["source"]
    if not isinstance(raw_sources, list) or not raw_sources:
        _schema("source must be a non-empty array of tables")
    sources = [_validate_source(value, index) for index, value in enumerate(raw_sources)]

    ids = [_expect_string(source["id"], "source.id") for source in sources]
    archives = [_expect_string(source["archive"], "source.archive") for source in sources]
    if ids != sorted(ids):
        _schema("source entries must be sorted by id")
    if len(ids) != len(set(ids)):
        _schema("source ids must be unique")
    if len(archives) != len(set(archives)):
        _schema("source archive names must be unique")
    portable_archives = [archive.casefold() for archive in archives]
    if len(portable_archives) != len(set(portable_archives)):
        _schema("source archive names must be unique on case-insensitive filesystems")

    known_ids = set(ids)
    for source in sources:
        source_id = str(source["id"])
        dependencies = set(source["dependencies"])
        unknown = sorted(dependencies - known_ids)
        if unknown:
            _schema(f"source {source_id} has unknown dependencies: {', '.join(unknown)}")
        if source_id in dependencies:
            _schema(f"source {source_id} must not depend on itself")
        parent = source.get("parent")
        if parent is not None and parent not in known_ids:
            _schema(f"source {source_id} has unknown parent: {parent}")
        if parent == source_id:
            _schema(f"source {source_id} must not be its own parent")
    _validate_acyclic_graph(
        {str(source["id"]): list(source["dependencies"]) for source in sources},
        "dependency",
    )
    _validate_acyclic_graph(
        {
            str(source["id"]): [str(source["parent"])] if "parent" in source else []
            for source in sources
        },
        "parent",
    )

    placements = [
        (str(source["parent"]).casefold(), str(source["destination"]).casefold())
        for source in sources
        if "parent" in source
    ]
    if len(placements) != len(set(placements)):
        _schema("source parent/destination placements must be unique")
    return sources


def _validate_acyclic_graph(edges: dict[str, list[str]], label: str) -> None:
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            start = visiting.index(node)
            cycle = visiting[start:] + [node]
            _schema(f"source {label} graph contains a cycle: {' -> '.join(cycle)}")
        visiting.append(node)
        for target in edges[node]:
            visit(target)
        visiting.pop()
        visited.add(node)

    for source_id in edges:
        visit(source_id)


def load_manifest(path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError as error:
        raise SourceToolError(f"manifest not found: {path}", EXIT_SCHEMA) from error
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SourceToolError(f"cannot read manifest {path}: {error}", EXIT_SCHEMA) from error
    sources = validate_manifest_data(data)
    source_ids = tuple(str(source["id"]) for source in sources)
    if source_ids != CANONICAL_SOURCE_IDS:
        _schema(
            "source manifest must contain the canonical 23-input closure: "
            + ", ".join(CANONICAL_SOURCE_IDS)
        )
    return data, sources


def _hash_stream(stream: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _is_windows_reparse(stat_result: os.stat_result) -> bool:
    return bool(
        os.name == "nt"
        and (
            getattr(stat_result, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


@contextmanager
def open_verified_archive(
    source: dict[str, object], path: Path
) -> Iterator[BinaryIO]:
    """Yield an immutable temporary snapshot of one verified cache input."""
    expected_size = int(source["size"])
    expected_digest = str(source["sha256"])
    descriptor = -1
    source_stream: BinaryIO | None = None
    snapshot: BinaryIO | None = None
    try:
        path_stat = os.lstat(path)
        if not stat.S_ISREG(path_stat.st_mode) or _is_windows_reparse(path_stat):
            raise SourceToolError(
                f"cached archive must be a regular non-symlink file: {path}",
                EXIT_INTEGRITY,
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SourceToolError(
                f"cached archive must be a regular file: {path}", EXIT_INTEGRITY
            )
        if _is_windows_reparse(file_stat):
            raise SourceToolError(
                f"cached archive must not be a reparse point: {path}", EXIT_INTEGRITY
            )
        current_path_stat = os.lstat(path)
        if (
            current_path_stat.st_dev,
            current_path_stat.st_ino,
        ) != (file_stat.st_dev, file_stat.st_ino) or _is_windows_reparse(current_path_stat):
            raise SourceToolError(
                f"cached archive changed while it was being opened: {path}",
                EXIT_INTEGRITY,
            )
        stat_size = file_stat.st_size
        if stat_size != expected_size:
            raise SourceToolError(
                f"size mismatch for {path.name}: expected {expected_size}, got {stat_size}",
                EXIT_INTEGRITY,
            )
        source_stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        snapshot = tempfile.TemporaryFile(mode="w+b")
        digest = hashlib.sha256()
        actual_size = 0
        while True:
            chunk = source_stream.read(1024 * 1024)
            if not chunk:
                break
            actual_size += len(chunk)
            if actual_size > expected_size:
                raise SourceToolError(
                    f"size mismatch for {path.name}: expected {expected_size}, "
                    f"got more than {actual_size}",
                    EXIT_INTEGRITY,
                )
            digest.update(chunk)
            snapshot.write(chunk)
        actual_digest = digest.hexdigest()
        source_stream.close()
        source_stream = None
        if actual_size != expected_size:
            raise SourceToolError(
                f"size mismatch for {path.name}: expected {expected_size}, got {actual_size}",
                EXIT_INTEGRITY,
            )
        if actual_digest != expected_digest:
            raise SourceToolError(
                f"SHA-256 mismatch for {path.name}: expected {expected_digest}, "
                f"got {actual_digest}",
                EXIT_INTEGRITY,
            )
        snapshot.flush()
        snapshot.seek(0)
    except BaseException as error:
        if source_stream is not None:
            source_stream.close()
        if snapshot is not None:
            snapshot.close()
        if isinstance(error, SourceToolError):
            raise
        if isinstance(error, OSError):
            raise SourceToolError(
                f"cannot read cached archive {path}: {error}", EXIT_INTEGRITY
            ) from error
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        yield snapshot
    finally:
        snapshot.close()


def _verify_archive(source: dict[str, object], path: Path) -> None:
    with open_verified_archive(source, path):
        pass


def verify_archive(source: dict[str, object], path: Path) -> None:
    """Verify one locked cache input as a regular, byte-exact file."""
    _verify_archive(source, path)


def verify_cache(sources: list[dict[str, object]], cache: Path) -> None:
    missing: list[str] = []
    for source in sources:
        archive = cache / str(source["archive"])
        if archive.is_symlink():
            raise SourceToolError(
                f"cached archive must not be a symlink: {archive}",
                EXIT_INTEGRITY,
            )
        if not archive.exists():
            missing.append(str(source["archive"]))
        elif not archive.is_file():
            raise SourceToolError(
                f"cache path is not a regular file: {archive}",
                EXIT_INTEGRITY,
            )
    if missing:
        raise SourceToolError(
            f"offline cache is missing {len(missing)} archive(s): {', '.join(missing)}",
            EXIT_MISSING,
        )
    for source in sources:
        archive = cache / str(source["archive"])
        verify_archive(source, archive)
        print(f"verified {source['id']}: {archive.name}")


def _cleanup_partial(
    part: Path | None, expected_identity: tuple[int, int] | None = None
) -> str | None:
    if part is None:
        return None
    try:
        if part.is_symlink() or part.exists():
            part_stat = part.lstat()
            if (
                expected_identity is not None
                and (part_stat.st_dev, part_stat.st_ino) != expected_identity
            ):
                return "partial path identity changed; refusing cleanup"
            part.unlink()
    except OSError as error:
        return str(error)
    return None


def _publish_download_no_replace(
    part: Path, archive: Path, expected_identity: tuple[int, int]
) -> bool:
    """Publish a verified partial without replacing a concurrent cache winner."""
    try:
        part_stat = part.lstat()
        if (part_stat.st_dev, part_stat.st_ino) != expected_identity:
            raise SourceToolError(
                "download partial identity changed before publication", EXIT_INTEGRITY
            )
        if os.name == "nt":
            os.rename(part, archive)
        else:
            os.link(part, archive, follow_symlinks=False)
            part.unlink()
        return True
    except FileExistsError:
        return False


def _download_source(source: dict[str, object], cache: Path, timeout: float) -> None:
    archive = cache / str(source["archive"])
    if archive.is_symlink():
        raise SourceToolError(f"cache archive must not be a symlink: {archive}", EXIT_INTEGRITY)
    if archive.exists():
        if not archive.is_file():
            raise SourceToolError(f"cache path is not a file: {archive}", EXIT_INTEGRITY)
        verify_archive(source, archive)
        print(f"cached {source['id']}: {archive.name}")
        return

    part: Path | None = None
    part_identity: tuple[int, int] | None = None
    try:
        request = urllib.request.Request(
            str(source["url"]),
            headers={"User-Agent": "ZivPlayer-native-source-tool/1"},
        )
        with _open_https(request, timeout=timeout) as response:
            _validate_https_url(
                response.geturl(),
                f"final download URL for {source['id']}",
                allow_query=True,
                exit_code=EXIT_NETWORK,
            )
            descriptor, part_name = tempfile.mkstemp(
                prefix=f".{archive.name}.",
                suffix=".part",
                dir=cache,
            )
            part = Path(part_name)
            descriptor_stat = os.fstat(descriptor)
            part_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
            expected_size = int(source["size"])
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > expected_size:
                        raise SourceToolError(
                            f"download for {source['id']} exceeded locked size {expected_size}",
                            EXIT_INTEGRITY,
                        )
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if size != int(source["size"]):
            raise SourceToolError(
                f"size mismatch for {source['id']}: expected {source['size']}, got {size}",
                EXIT_INTEGRITY,
            )
        if digest.hexdigest() != source["sha256"]:
            raise SourceToolError(
                f"SHA-256 mismatch for downloaded {source['id']}",
                EXIT_INTEGRITY,
            )
        published = _publish_download_no_replace(part, archive, part_identity)
        if published:
            part = None
            verify_archive(source, archive)
            print(f"fetched {source['id']}: {archive.name}")
        else:
            verify_archive(source, archive)
            cleanup_error = _cleanup_partial(part, part_identity)
            if cleanup_error is not None:
                raise SourceToolError(
                    f"cannot remove losing download partial: {cleanup_error}",
                    EXIT_NETWORK,
                )
            part = None
            print(f"cached {source['id']}: {archive.name}")
    except BaseException as error:
        cleanup_error = _cleanup_partial(part, part_identity)
        if cleanup_error is not None:
            raise SourceToolError(
                f"download failed for {source['id']} and partial cleanup failed: {cleanup_error}",
                EXIT_NETWORK,
            ) from error
        if isinstance(error, SourceToolError):
            raise
        if isinstance(error, KeyboardInterrupt):
            raise
        if isinstance(error, Exception):
            raise SourceToolError(
                f"download failed for {source['id']}: {error}",
                EXIT_NETWORK,
            ) from error
        raise


def fetch_sources(sources: list[dict[str, object]], cache: Path, timeout: float) -> None:
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SourceToolError(f"cannot create cache directory {cache}: {error}", EXIT_NETWORK) from error
    for source in sources:
        _download_source(source, cache, timeout)
    verify_cache(sources, cache)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"locked TOML manifest (default: {DEFAULT_MANIFEST})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the manifest without network or writes")
    verify = subparsers.add_parser("verify-cache", help="verify a complete cache without network or writes")
    verify.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    fetch = subparsers.add_parser("fetch", help="explicitly fetch missing archives over HTTPS")
    fetch.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    fetch.add_argument("--timeout", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        manifest_path = arguments.manifest.resolve()
        _, sources = load_manifest(manifest_path)
        if arguments.command == "validate":
            print(f"manifest valid: {len(sources)} locked source input(s)")
        elif arguments.command == "verify-cache":
            verify_cache(sources, arguments.cache.resolve())
        elif arguments.command == "fetch":
            if not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
                raise SourceToolError("--timeout must be finite and greater than zero", EXIT_SCHEMA)
            fetch_sources(sources, arguments.cache.resolve(), arguments.timeout)
        else:  # pragma: no cover - argparse guarantees this branch is unreachable.
            raise SourceToolError(f"unsupported command: {arguments.command}", EXIT_SCHEMA)
        return EXIT_OK
    except SourceToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return EXIT_INTERNAL
    except Exception as error:  # pragma: no cover - protects stable CLI behavior.
        print(f"error: unexpected failure: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
