#!/usr/bin/env python3
"""Validate the locked source-built libmpv execution profile and its inputs."""

from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import subprocess
import sys
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

import composition_tool
import environment_tool
import materialize_sources
import rootfs_tool
import source_tool
import toolchain_tool


NATIVE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = NATIVE_DIR.parent
DEFAULT_PROFILE = NATIVE_DIR / "native-build-profile.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_TOOLCHAIN_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"
DEFAULT_SOURCE_CACHE = NATIVE_DIR / "cache" / "sources"
DEFAULT_TOOLCHAIN_CACHE = NATIVE_DIR / "cache" / "toolchain"
DEFAULT_SOURCE_WORKSPACE = Path("/var/tmp/zivplayer-native-source")
DEFAULT_BUILD_WORKSPACE = Path("/var/tmp/zivplayer-native-build")
DEFAULT_APT_ROOT = composition_tool.DEFAULT_APT_ROOT
DEFAULT_SDK_ROOT = composition_tool.DEFAULT_SDK_ROOT
DEFAULT_COMPOSITION_RECEIPT = composition_tool.DEFAULT_RECEIPT

MAX_PROFILE_BYTES = 1024 * 1024
MAX_RUNTIME_LIBRARY_BYTES = 32 * 1024 * 1024
MAX_PREPARATION_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_MOUNT_INVENTORY_BYTES = 4 * 1024 * 1024
MAX_MOUNT_INVENTORY_ENTRIES = 65536
PREPARATION_FREE_BYTE_MARGIN = 128 * 1024 * 1024
PREPARATION_FREE_INODE_MARGIN = 4096
PREPARATION_RECEIPT_NAME = "ziv-native-build-preparation.json"
PREPARATION_RECEIPT_KIND = "ziv-native-build-preparation-v1"
NORMALIZED_MTIME_NS = materialize_sources.NORMALIZED_GENERATED_MTIME_NS
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROFILE_NAME = "ziv-libmpv-stack-api26-v1"
EXPECTED_ABIS = (
    {
        "name": "arm64-v8a",
        "upstreamArch": "arm64",
        "clangTriple": "aarch64-linux-android26",
        "elfClass": 64,
        "elfMachine": "AArch64",
        "ndkRuntimeDirectory": "aarch64-linux-android",
        "runtimeSize": 9290184,
        "runtimeSha256": "0c52cfab2df0d957d8b346a2bdc5ae8d71feca2591924d77e1cd724d5bf74352",
    },
    {
        "name": "x86_64",
        "upstreamArch": "x86_64",
        "clangTriple": "x86_64-linux-android26",
        "elfClass": 64,
        "elfMachine": "Advanced Micro Devices X86-64",
        "ndkRuntimeDirectory": "x86_64-linux-android",
        "runtimeSize": 9015544,
        "runtimeSha256": "e28cb9e0f456caf8498d0e8562b3ac3f73bf78c07555a52b5602396f240abec3",
    },
)
EXPECTED_LIBRARIES = (
    "libavcodec.so",
    "libavdevice.so",
    "libavfilter.so",
    "libavformat.so",
    "libavutil.so",
    "libc++_shared.so",
    "libmpv.so",
    "libswresample.so",
    "libswscale.so",
)
EXPECTED_BUILT_LIBRARIES = tuple(
    library for library in EXPECTED_LIBRARIES if library != "libc++_shared.so"
)
EXPECTED_RUNTIME_LIBRARIES = ("libc++_shared.so",)
EXPECTED_OVERLAY_DESTINATIONS = (
    "buildscripts/buildall.sh",
    "buildscripts/include/path.sh",
)
EXPECTED_OVERLAY_REPLACEMENTS = (
    "native/overlays/mpv-android-api26/buildscripts/buildall.sh",
    "native/overlays/mpv-android-api26/buildscripts/include/path.sh",
)

TOP_LEVEL_KEYS = frozenset(
    {"schemaVersion", "project", "toolchain", "policy", "build", "abi", "overlay"}
)
PROJECT_KEYS = frozenset(
    {
        "name",
        "profile",
        "upstreamRevision",
        "sourceManifest",
        "sourceManifestSha256",
        "toolchainManifest",
        "toolchainManifestSha256",
        "nativeApi",
        "pageSizeBytes",
        "releaseReady",
    }
)
TOOLCHAIN_KEYS = frozenset(
    {
        "hostPlatform",
        "mount",
        "ndkVersion",
        "sdkPlatform",
        "sdkBuildTools",
        "jdkMajor",
        "pythonVersion",
        "mesonVersion",
        "ninjaVersion",
        "pkgConfigVersion",
    }
)
POLICY_KEYS = frozenset(
    {
        "networkAtBuild",
        "sourceMaterializationMode",
        "canonicalSourceReadOnly",
        "sourceCopyWritable",
        "outputWritable",
        "freshSourceCopyRequired",
        "freshOutputRequired",
        "freshBuildHomeRequired",
        "freshTemporaryRequired",
        "aptRootReadOnly",
        "sdkRootReadOnly",
        "inheritHostEnvironment",
        "allowGradle",
        "allowSdkManager",
        "allowAptRepositories",
        "allowPipIndex",
        "allowFloatingReferences",
        "allowNonfree",
        "sourceCopyMount",
        "outputMount",
        "buildHomeMount",
        "temporaryMount",
        "sourceDateEpoch",
        "jobs",
    }
)
BUILD_KEYS = frozenset(
    {
        "target",
        "commands",
        "expectedLibraries",
        "builtLibraries",
        "runtimeLibraries",
        "builtLibraryRootTemplate",
        "runtimeLibraryRootTemplate",
        "outputLibraryTemplate",
        "artifactStaging",
        "jniWrapperStatus",
        "gradleIntegrationStatus",
        "complianceStatus",
    }
)
ABI_KEYS = frozenset(
    {
        "name",
        "upstreamArch",
        "clangTriple",
        "elfClass",
        "elfMachine",
        "ndkRuntimeDirectory",
        "runtimeSize",
        "runtimeSha256",
    }
)
OVERLAY_KEYS = frozenset(
    {
        "destination",
        "replacement",
        "originalSize",
        "originalSha256",
        "replacementSize",
        "replacementSha256",
        "originalMode",
        "replacementMode",
    }
)


@dataclass(frozen=True)
class LoadedProfile:
    data: dict[str, object]
    raw: bytes
    sha256: str
    project: dict[str, object]
    toolchain: dict[str, object]
    policy: dict[str, object]
    build: dict[str, object]
    abis: tuple[dict[str, object], ...]
    overlays: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class TreePolicySnapshot:
    tree: dict[str, object]
    file_bytes: int
    symlinks: tuple[dict[str, str], ...]
    identities: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class PreparationInputs:
    profile: LoadedProfile
    source_receipt: dict[str, object]
    source_receipt_raw: bytes
    composition_receipt: dict[str, object]
    composition_receipt_raw: bytes
    overlay_raws: tuple[bytes, ...]
    canonical_source: TreePolicySnapshot


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _exact_keys(value: dict[str, object], expected: frozenset[str], location: str) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        _schema(f"{location} keys are not exact: {'; '.join(details)}")


def _table(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _schema(f"{location} must be a table")
    return value


def _tables(value: object, location: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not value:
        _schema(f"{location} must be a non-empty array of tables")
    result: list[dict[str, object]] = []
    for index, item in enumerate(value):
        result.append(_table(item, f"{location}[{index}]"))
    return tuple(result)


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        _schema(f"{location} must be a non-empty string")
    return value


def _integer(value: object, location: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _schema(f"{location} must be an integer >= {minimum}")
    return value


def _boolean(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        _schema(f"{location} must be a boolean")
    return value


def _string_list(value: object, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _schema(f"{location} must be a non-empty string array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_string(item, f"{location}[{index}]"))
    if len(set(result)) != len(result):
        _schema(f"{location} must not contain duplicates")
    return tuple(result)


def _sha256(value: object, location: str) -> str:
    digest = _string(value, location)
    if not SHA256_PATTERN.fullmatch(digest):
        _schema(f"{location} must be a lowercase SHA-256 digest")
    return digest


def _safe_relative_path(value: object, location: str) -> str:
    path = _string(value, location)
    if "\\" in path or path.startswith("/"):
        _schema(f"{location} must be a portable relative POSIX path")
    parsed = PurePosixPath(path)
    if str(parsed) != path or any(part in {"", ".", ".."} for part in parsed.parts):
        _schema(f"{location} must be a normalized relative POSIX path")
    return path


def _absolute_mount(value: object, location: str) -> str:
    path = _string(value, location)
    parsed = PurePosixPath(path)
    if not parsed.is_absolute() or str(parsed) != path or ".." in parsed.parts:
        _schema(f"{location} must be a normalized absolute POSIX path")
    return path


def _expect(value: object, expected: object, location: str) -> None:
    if value != expected:
        _schema(f"{location} must be {expected!r}")


def validate_profile_data(data: object) -> LoadedProfile:
    root = _table(data, "profile")
    _exact_keys(root, TOP_LEVEL_KEYS, "profile")
    _expect(_integer(root["schemaVersion"], "schemaVersion", minimum=1), 1, "schemaVersion")

    project = _table(root["project"], "project")
    toolchain = _table(root["toolchain"], "toolchain")
    policy = _table(root["policy"], "policy")
    build = _table(root["build"], "build")
    abis = _tables(root["abi"], "abi")
    overlays = _tables(root["overlay"], "overlay")
    _exact_keys(project, PROJECT_KEYS, "project")
    _exact_keys(toolchain, TOOLCHAIN_KEYS, "toolchain")
    _exact_keys(policy, POLICY_KEYS, "policy")
    _exact_keys(build, BUILD_KEYS, "build")

    _expect(_string(project["name"], "project.name"), "ZivPlayer", "project.name")
    _expect(_string(project["profile"], "project.profile"), PROFILE_NAME, "project.profile")
    upstream_revision = _string(project["upstreamRevision"], "project.upstreamRevision")
    if not re.fullmatch(r"[0-9a-f]{40}", upstream_revision):
        _schema("project.upstreamRevision must be a lowercase full Git commit")
    _expect(
        _safe_relative_path(project["sourceManifest"], "project.sourceManifest"),
        "native/source-manifest.toml",
        "project.sourceManifest",
    )
    _sha256(project["sourceManifestSha256"], "project.sourceManifestSha256")
    _expect(
        _safe_relative_path(project["toolchainManifest"], "project.toolchainManifest"),
        "native/toolchain-manifest.toml",
        "project.toolchainManifest",
    )
    _sha256(project["toolchainManifestSha256"], "project.toolchainManifestSha256")
    _expect(_integer(project["nativeApi"], "project.nativeApi"), 26, "project.nativeApi")
    _expect(
        _integer(project["pageSizeBytes"], "project.pageSizeBytes"),
        16384,
        "project.pageSizeBytes",
    )
    _expect(_boolean(project["releaseReady"], "project.releaseReady"), False, "project.releaseReady")

    expected_toolchain: dict[str, object] = {
        "hostPlatform": "linux/amd64",
        "mount": "/opt/zivplayer/toolchain",
        "ndkVersion": "29.0.14206865",
        "sdkPlatform": 36,
        "sdkBuildTools": "36.0.0",
        "jdkMajor": 17,
        "pythonVersion": "3.12.3",
        "mesonVersion": "1.11.0",
        "ninjaVersion": "1.11.1",
        "pkgConfigVersion": "1.8.1",
    }
    for key, expected in expected_toolchain.items():
        actual = toolchain[key]
        if isinstance(expected, int):
            actual = _integer(actual, f"toolchain.{key}")
        else:
            actual = _string(actual, f"toolchain.{key}")
        _expect(actual, expected, f"toolchain.{key}")
    _absolute_mount(toolchain["mount"], "toolchain.mount")

    expected_policy: dict[str, object] = {
        "networkAtBuild": "none",
        "sourceMaterializationMode": "preserve",
        "canonicalSourceReadOnly": True,
        "sourceCopyWritable": True,
        "outputWritable": True,
        "freshSourceCopyRequired": True,
        "freshOutputRequired": True,
        "freshBuildHomeRequired": True,
        "freshTemporaryRequired": True,
        "aptRootReadOnly": True,
        "sdkRootReadOnly": True,
        "inheritHostEnvironment": False,
        "allowGradle": False,
        "allowSdkManager": False,
        "allowAptRepositories": False,
        "allowPipIndex": False,
        "allowFloatingReferences": False,
        "allowNonfree": False,
        "sourceCopyMount": "/build/source",
        "outputMount": "/build/output",
        "buildHomeMount": "/build/home",
        "temporaryMount": "/build/tmp",
        "sourceDateEpoch": 946684800,
        "jobs": 4,
    }
    for key, expected in expected_policy.items():
        actual = policy[key]
        if isinstance(expected, bool):
            actual = _boolean(actual, f"policy.{key}")
        elif isinstance(expected, int):
            actual = _integer(actual, f"policy.{key}")
        else:
            actual = _string(actual, f"policy.{key}")
        _expect(actual, expected, f"policy.{key}")
    source_mount = _absolute_mount(policy["sourceCopyMount"], "policy.sourceCopyMount")
    output_mount = _absolute_mount(policy["outputMount"], "policy.outputMount")
    build_home_mount = _absolute_mount(policy["buildHomeMount"], "policy.buildHomeMount")
    temporary_mount = _absolute_mount(policy["temporaryMount"], "policy.temporaryMount")
    named_mounts = (
        ("source", PurePosixPath(source_mount)),
        ("output", PurePosixPath(output_mount)),
        ("build home", PurePosixPath(build_home_mount)),
        ("temporary", PurePosixPath(temporary_mount)),
    )
    for index, (left_name, left) in enumerate(named_mounts):
        for right_name, right in named_mounts[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                _schema(f"{left_name} and {right_name} mounts must be disjoint")

    _expect(_string(build["target"], "build.target"), "mpv", "build.target")
    expected_commands = [
        [f"{source_mount}/buildscripts/buildall.sh", "--arch", str(abi["upstreamArch"]), "mpv"]
        for abi in EXPECTED_ABIS
    ]
    commands = build["commands"]
    if commands != expected_commands:
        _schema("build.commands must be the fixed API-26 two-ABI libmpv command sequence")
    libraries = _string_list(build["expectedLibraries"], "build.expectedLibraries")
    _expect(libraries, EXPECTED_LIBRARIES, "build.expectedLibraries")
    built_libraries = _string_list(build["builtLibraries"], "build.builtLibraries")
    _expect(built_libraries, EXPECTED_BUILT_LIBRARIES, "build.builtLibraries")
    runtime_libraries = _string_list(build["runtimeLibraries"], "build.runtimeLibraries")
    _expect(runtime_libraries, EXPECTED_RUNTIME_LIBRARIES, "build.runtimeLibraries")
    if set(built_libraries) | set(runtime_libraries) != set(libraries):
        _schema("builtLibraries and runtimeLibraries must partition expectedLibraries")
    _expect(
        _string(build["builtLibraryRootTemplate"], "build.builtLibraryRootTemplate"),
        "/build/source/buildscripts/prefix/{upstreamArch}/lib",
        "build.builtLibraryRootTemplate",
    )
    _expect(
        _string(build["runtimeLibraryRootTemplate"], "build.runtimeLibraryRootTemplate"),
        "/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/{ndkRuntimeDirectory}",
        "build.runtimeLibraryRootTemplate",
    )
    _expect(
        _string(build["outputLibraryTemplate"], "build.outputLibraryTemplate"),
        "/build/output/{abi}/{library}",
        "build.outputLibraryTemplate",
    )
    _expect(
        _string(build["artifactStaging"], "build.artifactStaging"),
        "copy-exact-allowlist",
        "build.artifactStaging",
    )
    _expect(
        _string(build["jniWrapperStatus"], "build.jniWrapperStatus"),
        "pending-source-wrapper",
        "build.jniWrapperStatus",
    )
    _expect(
        _string(build["gradleIntegrationStatus"], "build.gradleIntegrationStatus"),
        "pending-offline-closure",
        "build.gradleIntegrationStatus",
    )
    _expect(
        _string(build["complianceStatus"], "build.complianceStatus"),
        "pending-actual-build-graph",
        "build.complianceStatus",
    )

    if len(abis) != len(EXPECTED_ABIS):
        _schema("abi must contain exactly arm64-v8a and x86_64")
    for index, (actual, expected) in enumerate(zip(abis, EXPECTED_ABIS, strict=True)):
        _exact_keys(actual, ABI_KEYS, f"abi[{index}]")
        for key, expected_value in expected.items():
            value = actual[key]
            if isinstance(expected_value, int):
                value = _integer(value, f"abi[{index}].{key}")
            else:
                value = _string(value, f"abi[{index}].{key}")
            _expect(value, expected_value, f"abi[{index}].{key}")

    destinations: list[str] = []
    replacements: list[str] = []
    for index, overlay in enumerate(overlays):
        location = f"overlay[{index}]"
        _exact_keys(overlay, OVERLAY_KEYS, location)
        destination = _safe_relative_path(overlay["destination"], f"{location}.destination")
        replacement = _safe_relative_path(overlay["replacement"], f"{location}.replacement")
        _integer(overlay["originalSize"], f"{location}.originalSize", minimum=1)
        _sha256(overlay["originalSha256"], f"{location}.originalSha256")
        _integer(overlay["replacementSize"], f"{location}.replacementSize", minimum=1)
        _sha256(overlay["replacementSha256"], f"{location}.replacementSha256")
        _expect(
            _integer(overlay["originalMode"], f"{location}.originalMode"),
            0o755,
            f"{location}.originalMode",
        )
        _expect(
            _integer(overlay["replacementMode"], f"{location}.replacementMode"),
            0o755,
            f"{location}.replacementMode",
        )
        destinations.append(destination)
        replacements.append(replacement)
    if destinations != sorted(destinations) or len(set(destinations)) != len(destinations):
        _schema("overlay destinations must be unique and sorted")
    if len(set(replacements)) != len(replacements):
        _schema("overlay replacements must be unique")
    _expect(tuple(destinations), EXPECTED_OVERLAY_DESTINATIONS, "overlay destinations")
    _expect(tuple(replacements), EXPECTED_OVERLAY_REPLACEMENTS, "overlay replacements")

    return LoadedProfile(
        data=copy.deepcopy(root),
        raw=b"",
        sha256="",
        project=copy.deepcopy(project),
        toolchain=copy.deepcopy(toolchain),
        policy=copy.deepcopy(policy),
        build=copy.deepcopy(build),
        abis=tuple(copy.deepcopy(item) for item in abis),
        overlays=tuple(copy.deepcopy(item) for item in overlays),
    )


def _stable_bytes(path: Path, *, maximum: int, label: str, missing_exit: int) -> bytes:
    return toolchain_tool._read_stable_bytes(  # noqa: SLF001 - shared repository primitive
        path,
        maximum=maximum,
        label=label,
        missing_exit=missing_exit,
    )


def _resolve_regular_file_beneath(
    base_path: Path,
    relative: str,
    label: str,
    *,
    base_label: str,
) -> Path:
    lexical_root = Path(os.path.abspath(base_path))
    try:
        root = base_path.resolve(strict=True)
        root_info = root.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"{base_label} not found for {label}: {base_path}",
            source_tool.EXIT_MISSING,
        ) from error
    except (OSError, RuntimeError) as error:
        _integrity(f"cannot inspect {base_label} for {label}: {error}")
    if root != lexical_root:
        _integrity(f"{base_label} for {label} must not resolve through a symlink: {base_path}")
    if not stat.S_ISDIR(root_info.st_mode) or toolchain_tool._is_reparse(root_info):  # noqa: SLF001
        _integrity(f"{base_label} for {label} must be a non-reparse directory: {root}")

    parts = PurePosixPath(relative).parts
    candidate = root.joinpath(*parts)
    current = root
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError as error:
            raise source_tool.SourceToolError(
                f"{label} not found: {current}",
                source_tool.EXIT_MISSING,
            ) from error
        except OSError as error:
            _integrity(f"cannot inspect {label} path component {current}: {error}")
        if stat.S_ISLNK(info.st_mode) or toolchain_tool._is_reparse(info):  # noqa: SLF001
            _integrity(f"{label} path must not contain symlinks or reparse points: {current}")
        if index < len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                _integrity(f"{label} parent must be a directory: {current}")
        elif not stat.S_ISREG(info.st_mode):
            _integrity(f"{label} must be a regular file: {current}")
    return candidate


def _resolve_repository_file(repository_root: Path, relative: str, label: str) -> Path:
    return _resolve_regular_file_beneath(
        repository_root,
        relative,
        label,
        base_label="repository root",
    )


def load_profile(
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> LoadedProfile:
    raw = _stable_bytes(
        profile_path,
        maximum=MAX_PROFILE_BYTES,
        label="native build profile",
        missing_exit=source_tool.EXIT_SCHEMA,
    )
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        _schema(f"cannot parse native build profile {profile_path}: {error}")
    loaded = validate_profile_data(parsed)

    declared_source = _resolve_repository_file(
        repository_root,
        str(loaded.project["sourceManifest"]),
        "project.sourceManifest",
    )
    declared_toolchain = _resolve_repository_file(
        repository_root,
        str(loaded.project["toolchainManifest"]),
        "project.toolchainManifest",
    )
    if declared_source != source_manifest_path.resolve():
        _schema("profile sourceManifest does not name the selected source manifest")
    if declared_toolchain != toolchain_manifest_path.resolve():
        _schema("profile toolchainManifest does not name the selected toolchain manifest")

    source_raw = _stable_bytes(
        declared_source,
        maximum=toolchain_tool.MAX_MANIFEST_BYTES,
        label="source manifest",
        missing_exit=source_tool.EXIT_SCHEMA,
    )
    source_digest = hashlib.sha256(source_raw).hexdigest()
    if source_digest != loaded.project["sourceManifestSha256"]:
        _integrity("source manifest digest differs from the native build profile")
    try:
        source_data = tomllib.loads(source_raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        _schema(f"cannot parse source manifest {declared_source}: {error}")
    sources = source_tool.validate_manifest_data(source_data)
    if tuple(str(source["id"]) for source in sources) != source_tool.CANONICAL_SOURCE_IDS:
        _schema("source manifest does not contain the canonical 23-input closure")

    toolchain_data, _artifacts, _oci, bound_source, toolchain_digest = (
        toolchain_tool.load_manifest_snapshot(
            declared_toolchain,
            source_manifest_path=declared_source,
        )
    )
    if toolchain_digest != loaded.project["toolchainManifestSha256"]:
        _integrity("toolchain manifest digest differs from the native build profile")

    source_project = _table(source_data.get("project"), "source manifest project")
    source_tools = _table(source_data.get("toolchain"), "source manifest toolchain")
    toolchain_project = _table(toolchain_data.get("project"), "toolchain manifest project")
    if bound_source != source_data:
        _integrity("toolchain manifest source snapshot differs from the selected source manifest")
    tuple_checks = (
        (loaded.project["upstreamRevision"], source_project.get("upstreamRevision"), "upstream revision"),
        (loaded.project["nativeApi"], source_project.get("nativeApi"), "source native API"),
        (loaded.project["nativeApi"], toolchain_project.get("nativeApi"), "toolchain native API"),
        (loaded.project["pageSizeBytes"], source_project.get("pageSizeBytes"), "source page size"),
        (loaded.project["pageSizeBytes"], toolchain_project.get("pageSizeBytes"), "toolchain page size"),
        ([item["name"] for item in loaded.abis], source_project.get("abis"), "source ABI set"),
        ([item["name"] for item in loaded.abis], toolchain_project.get("abis"), "toolchain ABI set"),
        (loaded.toolchain["ndkVersion"], source_tools.get("ndkVersion"), "NDK version"),
        (loaded.toolchain["sdkPlatform"], source_tools.get("sdkPlatform"), "SDK platform"),
        (loaded.toolchain["sdkBuildTools"], source_tools.get("sdkBuildTools"), "SDK build tools"),
        (loaded.toolchain["jdkMajor"], source_tools.get("jdkMajor"), "JDK major"),
    )
    for expected, actual, label in tuple_checks:
        if actual != expected:
            _integrity(f"native build profile {label} is not bound to its manifests")

    for index, overlay in enumerate(loaded.overlays):
        replacement = _resolve_repository_file(
            repository_root,
            str(overlay["replacement"]),
            f"overlay[{index}].replacement",
        )
        replacement_raw = _stable_bytes(
            replacement,
            maximum=MAX_PROFILE_BYTES,
            label=f"overlay replacement {overlay['replacement']}",
            missing_exit=source_tool.EXIT_MISSING,
        )
        if len(replacement_raw) != overlay["replacementSize"]:
            _integrity(f"overlay replacement size differs: {overlay['replacement']}")
        if hashlib.sha256(replacement_raw).hexdigest() != overlay["replacementSha256"]:
            _integrity(f"overlay replacement digest differs: {overlay['replacement']}")

    return LoadedProfile(
        data=loaded.data,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        project=loaded.project,
        toolchain=loaded.toolchain,
        policy=loaded.policy,
        build=loaded.build,
        abis=loaded.abis,
        overlays=loaded.overlays,
    )


def _require_linux_root(action: str) -> None:
    if (
        sys.platform != "linux"
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
        or platform.machine().lower() not in {"x86_64", "amd64"}
    ):
        _schema(f"native build {action} requires Linux root")


def _canonical_json(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _require_no_xattrs(path: Path, label: str) -> None:
    digest, _size = environment_tool._xattr_digest(path)  # noqa: SLF001
    if digest is not None:
        _integrity(f"{label} must not carry extended attributes: {path}")


def _mounts_below(path: Path) -> bytes:
    if not path.is_absolute():
        _integrity(f"mount inventory path must be absolute: {path}")
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
        _integrity(f"cannot inspect native build mount state: {error}")
    if not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes):
        _integrity("findmnt returned a non-byte mount inventory")
    if len(result.stdout) > MAX_MOUNT_INVENTORY_BYTES:
        _integrity("findmnt mount inventory exceeds the byte limit")
    if result.returncode != 0:
        _integrity(
            "findmnt failed while checking native build mount state: "
            + result.stderr.decode("utf-8", "replace").strip()
        )

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, item_value in pairs:
            if key in parsed:
                raise ValueError(f"duplicate JSON key: {key}")
            parsed[key] = item_value
        return parsed

    try:
        value = json.loads(
            result.stdout.decode("utf-8"),
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        _integrity(f"cannot decode findmnt mount inventory: {error}")
    if not isinstance(value, dict) or set(value) != {"filesystems"}:
        _integrity("findmnt mount inventory is not an exact filesystems object")
    filesystems = value["filesystems"]
    if not isinstance(filesystems, list):
        _integrity("findmnt mount inventory has an invalid filesystems list")

    records: list[str] = []
    path_text = path.as_posix()
    prefix = path_text.rstrip("/") + "/"
    pending: list[object] = list(reversed(filesystems))
    entry_count = 0
    while pending:
        item = pending.pop()
        entry_count += 1
        if entry_count > MAX_MOUNT_INVENTORY_ENTRIES:
            _integrity("findmnt mount inventory exceeds the entry limit")
        if not isinstance(item, dict):
            _integrity("findmnt mount inventory has an invalid entry")
        allowed_keys = {"target", "fstype", "options", "children"}
        if not {"target", "fstype", "options"}.issubset(item) or not set(item).issubset(
            allowed_keys
        ):
            _integrity("findmnt mount inventory has an invalid entry")
        target = item["target"]
        fstype = item["fstype"]
        options = item["options"]
        if not all(isinstance(field, str) for field in (target, fstype, options)):
            _integrity("findmnt mount inventory has an invalid entry")
        if (
            not target.startswith("/")
            or not fstype
            or not options
            or any(
                character in field
                for field in (target, fstype, options)
                for character in ("\0", "\n", "\r", "\t")
            )
        ):
            _integrity("findmnt mount inventory has an invalid entry")
        if target == path_text or target.startswith(prefix):
            records.append(f"{target}\t{fstype}\t{options}")
        if "children" in item:
            children = item["children"]
            if not isinstance(children, list):
                _integrity("findmnt mount inventory has an invalid children list")
            pending.extend(reversed(children))

    records.sort(key=lambda item: item.encode("utf-8"))
    return "\n".join(records).encode("utf-8")


def _require_no_nested_mounts(path: Path, label: str) -> None:
    mounts = _mounts_below(path)
    if mounts:
        _integrity(
            f"{label} has a host-visible nested mount and cannot be consumed: "
            + mounts.decode("utf-8", "replace")
        )


def _clear_xattrs(path: Path) -> None:
    try:
        for name in os.listxattr(path, follow_symlinks=False):
            os.removexattr(path, name, follow_symlinks=False)
    except (AttributeError, OSError) as error:
        _integrity(f"cannot clear extended attributes from prepared path {path}: {error}")


def _require_no_fd_xattrs(descriptor: int, label: str) -> None:
    try:
        names = os.listxattr(descriptor)
    except (AttributeError, OSError) as error:
        _integrity(f"cannot inspect extended attributes for {label}: {error}")
    if names:
        _integrity(f"{label} must not carry extended attributes")


def _clear_fd_xattrs(descriptor: int, label: str) -> None:
    try:
        for name in os.listxattr(descriptor):
            os.removexattr(descriptor, name)
    except (AttributeError, OSError) as error:
        _integrity(f"cannot clear extended attributes from {label}: {error}")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _pinned_child_path(directory_fd: int, name: str) -> Path:
    return Path(f"/proc/self/fd/{directory_fd}") / name


def _require_tree_metadata(
    path: Path,
    info: os.stat_result,
    *,
    label: str,
    root_device: int,
    expected_kind: str,
) -> int:
    if info.st_dev != root_device:
        _integrity(f"{label} traverses a nested filesystem: {path}")
    if (info.st_uid, info.st_gid) != (0, 0):
        _integrity(f"{label} must be owned by root:root: {path}")
    if info.st_mtime_ns != NORMALIZED_MTIME_NS:
        _integrity(f"{label} has a non-normalized timestamp: {path}")
    if (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        and not stat.S_ISLNK(info.st_mode)
    ):
        _integrity(f"{label} contains an unsupported reparse point: {path}")
    kind_matches = {
        "directory": stat.S_ISDIR(info.st_mode),
        "file": stat.S_ISREG(info.st_mode),
        "symlink": stat.S_ISLNK(info.st_mode),
    }
    if not kind_matches[expected_kind]:
        _integrity(f"{label} has an unexpected entry type: {path}")
    if expected_kind in {"file", "symlink"} and info.st_nlink != 1:
        _integrity(f"{label} rejects hard-linked entries: {path}")
    return materialize_sources._checked_permission_mode(  # noqa: SLF001
        info.st_mode,
        path,
    )


def _scan_policy_tree(
    root: Path,
    label: str,
    *,
    skip_materialization_receipt: bool,
    expected_root_mode: int = 0o755,
) -> TreePolicySnapshot:
    root = Path(os.path.abspath(root))
    root_fd = -1
    try:
        root_info = root.lstat()
        resolved = root.resolve(strict=True)
        root_fd = os.open(root, _directory_flags())
        opened_root = os.fstat(root_fd)
    except (OSError, RuntimeError) as error:
        if root_fd >= 0:
            os.close(root_fd)
        _integrity(f"cannot inspect {label} root {root}: {error}")
    if resolved != root or root.is_symlink() or not stat.S_ISDIR(root_info.st_mode):
        if root_fd >= 0:
            os.close(root_fd)
        _integrity(f"{label} root must be a canonical real directory: {root}")
    try:
        environment_tool._assert_stable_stat(  # noqa: SLF001
            root_info,
            opened_root,
            f"{label} root",
        )
        root_device = root_info.st_dev
        root_mode = _require_tree_metadata(
            root,
            root_info,
            label=label,
            root_device=root_device,
            expected_kind="directory",
        )
        if root_mode != expected_root_mode:
            _integrity(
                f"{label} root mode mismatch: expected {expected_root_mode:04o}, "
                f"got {root_mode:04o}"
            )
        _require_no_fd_xattrs(root_fd, label)
    except BaseException:
        os.close(root_fd)
        root_fd = -1
        raise

    digest = hashlib.sha256()
    digest.update(materialize_sources.TREE_DIGEST_FORMAT.encode("ascii") + b"\0")
    digest.update(root_mode.to_bytes(2, "big"))
    counts = {"entryCount": 0, "fileCount": 0, "directoryCount": 0, "symlinkCount": 0}
    file_bytes = 0
    symlinks: list[dict[str, str]] = []
    identities: dict[str, tuple[int, int]] = {".": (root_info.st_dev, root_info.st_ino)}
    seen_identities = {(root_info.st_dev, root_info.st_ino)}

    def add_identity(relative: str, info: os.stat_result, path: Path) -> None:
        identity = (info.st_dev, info.st_ino)
        if identity in seen_identities:
            _integrity(f"{label} reuses a filesystem identity: {path}")
        seen_identities.add(identity)
        identities[relative] = identity

    def walk(
        directory_fd: int,
        directory: Path,
        relative_directory: PurePosixPath,
    ) -> None:
        nonlocal file_bytes
        try:
            directory_before = os.fstat(directory_fd)
            child_names = sorted(os.listdir(directory_fd), key=lambda name: name.encode("utf-8"))
        except (OSError, UnicodeError) as error:
            _integrity(f"cannot enumerate {label} directory {directory}: {error}")
        normalized_names: dict[str, str] = {}
        for child_name in child_names:
            if (
                skip_materialization_receipt
                and relative_directory == PurePosixPath(".")
                and child_name == materialize_sources.RECEIPT_NAME
            ):
                continue
            materialize_sources._portable_component(  # noqa: SLF001
                child_name,
                f"{label} path below {directory}",
            )
            normalized_name = unicodedata.normalize("NFKC", child_name).casefold()
            previous = normalized_names.get(normalized_name)
            if previous is not None and previous != child_name:
                _integrity(
                    f"{label} has a case/Unicode collision below {directory}: "
                    f"{previous} and {child_name}"
                )
            normalized_names[normalized_name] = child_name
            relative = (
                PurePosixPath(child_name)
                if relative_directory == PurePosixPath(".")
                else relative_directory / child_name
            )
            relative_text = relative.as_posix()
            child = directory / child_name
            pinned_child = _pinned_child_path(directory_fd, child_name)
            try:
                relative_bytes = relative_text.encode("utf-8")
            except UnicodeEncodeError as error:
                _integrity(f"{label} contains a non-UTF-8 path: {error}")
            if (
                len(relative.parts) > materialize_sources.MAX_ARCHIVE_PATH_DEPTH
                or len(relative_bytes) > materialize_sources.MAX_ARCHIVE_PATH_BYTES
            ):
                _integrity(f"{label} path exceeds the locked resource limits: {relative_text}")
            try:
                before = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as error:
                _integrity(f"cannot inspect {label} path {child}: {error}")
            if stat.S_ISDIR(before.st_mode):
                kind = "directory"
            elif stat.S_ISREG(before.st_mode):
                kind = "file"
            elif stat.S_ISLNK(before.st_mode):
                kind = "symlink"
            else:
                _integrity(f"{label} contains an unsupported entry type: {child}")
            mode = _require_tree_metadata(
                child,
                before,
                label=label,
                root_device=root_device,
                expected_kind=kind,
            )
            add_identity(relative_text, before, child)
            counts["entryCount"] += 1
            if counts["entryCount"] > materialize_sources.MAX_TREE_ENTRIES:
                _integrity(f"{label} exceeds the locked entry-count limit")

            if kind == "directory":
                counts["directoryCount"] += 1
                materialize_sources._digest_record(digest, b"D", relative_bytes)  # noqa: SLF001
                digest.update(mode.to_bytes(2, "big"))
                child_fd = -1
                try:
                    child_fd = os.open(child_name, _directory_flags(), dir_fd=directory_fd)
                    opened_child = os.fstat(child_fd)
                    environment_tool._assert_stable_stat(  # noqa: SLF001
                        before,
                        opened_child,
                        f"{label} directory {child}",
                    )
                    _require_no_fd_xattrs(child_fd, f"{label} directory {child}")
                    walk(child_fd, child, relative)
                    final_child = os.fstat(child_fd)
                    environment_tool._assert_stable_stat(  # noqa: SLF001
                        opened_child,
                        final_child,
                        f"{label} directory {child}",
                    )
                except source_tool.SourceToolError:
                    raise
                except OSError as error:
                    _integrity(f"cannot traverse {label} directory {child}: {error}")
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)
                after = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False)
                environment_tool._assert_stable_stat(  # noqa: SLF001
                    before,
                    after,
                    f"{label} directory {child}",
                )
                continue

            if kind == "symlink":
                counts["symlinkCount"] += 1
                try:
                    target = os.readlink(child_name, dir_fd=directory_fd)
                    materialize_sources._resolve_symlink_target(  # noqa: SLF001
                        relative,
                        target,
                        f"{label} symbolic link {relative_text}",
                    )
                    target_bytes = target.encode("utf-8")
                    _require_no_xattrs(pinned_child, label)
                    after = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except (OSError, UnicodeError) as error:
                    _integrity(f"cannot inspect {label} symbolic link {child}: {error}")
                environment_tool._assert_stable_stat(  # noqa: SLF001
                    before,
                    after,
                    f"{label} symbolic link {child}",
                )
                _require_no_xattrs(pinned_child, label)
                materialize_sources._digest_record(digest, b"L", relative_bytes)  # noqa: SLF001
                digest.update(mode.to_bytes(2, "big"))
                materialize_sources._digest_record(digest, b"T", target_bytes)  # noqa: SLF001
                symlinks.append({"path": relative_text, "target": target})
                continue

            counts["fileCount"] += 1
            if before.st_size < 0 or before.st_size > materialize_sources.MAX_MEMBER_BYTES:
                _integrity(f"{label} file exceeds the per-file byte limit: {child}")
            file_bytes += before.st_size
            if file_bytes > materialize_sources.MAX_TREE_BYTES:
                _integrity(f"{label} exceeds the locked tree byte limit")
            materialize_sources._digest_record(digest, b"F", relative_bytes)  # noqa: SLF001
            digest.update(mode.to_bytes(2, "big"))
            digest.update(before.st_size.to_bytes(8, "big"))
            descriptor = -1
            try:
                descriptor = os.open(
                    child_name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=directory_fd,
                )
                opened = os.fstat(descriptor)
                environment_tool._assert_stable_stat(  # noqa: SLF001
                    before,
                    opened,
                    f"{label} file {child}",
                )
                remaining = opened.st_size
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        _integrity(f"{label} file changed while hashing: {child}")
                    digest.update(chunk)
                    remaining -= len(chunk)
                final_fd = os.fstat(descriptor)
                final_path = os.stat(
                    child_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                environment_tool._assert_stable_stat(  # noqa: SLF001
                    opened,
                    final_fd,
                    f"{label} file {child}",
                )
                environment_tool._assert_stable_stat(  # noqa: SLF001
                    final_fd,
                    final_path,
                    f"{label} file {child}",
                )
            except source_tool.SourceToolError:
                raise
            except OSError as error:
                _integrity(f"cannot hash {label} file {child}: {error}")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            _require_no_xattrs(pinned_child, label)

        try:
            directory_after = os.fstat(directory_fd)
        except OSError as error:
            _integrity(f"cannot re-inspect {label} directory {directory}: {error}")
        environment_tool._assert_stable_stat(  # noqa: SLF001
            directory_before,
            directory_after,
            f"{label} directory {directory}",
        )
        _require_no_fd_xattrs(directory_fd, f"{label} directory {directory}")

    try:
        walk(root_fd, root, PurePosixPath("."))
        root_after_fd = os.fstat(root_fd)
        root_after_path = root.lstat()
        environment_tool._assert_stable_stat(  # noqa: SLF001
            opened_root,
            root_after_fd,
            f"{label} root {root}",
        )
        environment_tool._assert_stable_stat(  # noqa: SLF001
            root_after_fd,
            root_after_path,
            f"{label} root {root}",
        )
        _require_no_fd_xattrs(root_fd, f"{label} root")
        return TreePolicySnapshot(
            tree={
                "format": materialize_sources.TREE_DIGEST_FORMAT,
                "sha256": digest.hexdigest(),
                **counts,
            },
            file_bytes=file_bytes,
            symlinks=tuple(symlinks),
            identities=identities,
        )
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            _integrity("prepared file write made no progress")
        offset += written


def _copy_regular_file_at(
    source_parent_fd: int,
    destination_parent_fd: int,
    name: str,
    source: Path,
    destination: Path,
    before: os.stat_result,
    destination_device: int,
) -> None:
    source_fd = -1
    destination_fd = -1
    try:
        source_fd = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_parent_fd,
        )
        opened_source = os.fstat(source_fd)
        environment_tool._assert_stable_stat(  # noqa: SLF001
            before,
            opened_source,
            f"canonical source file {source}",
        )
        destination_fd = os.open(
            name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=destination_parent_fd,
        )
        opened_destination = os.fstat(destination_fd)
        if not stat.S_ISREG(opened_destination.st_mode) or opened_destination.st_nlink != 1:
            _integrity(f"prepared destination is not a private regular file: {destination}")
        if (opened_source.st_dev, opened_source.st_ino) == (
            opened_destination.st_dev,
            opened_destination.st_ino,
        ):
            _integrity(f"prepared file aliases its canonical source: {source}")
        source_digest = hashlib.sha256()
        remaining = opened_source.st_size
        while remaining:
            chunk = os.read(source_fd, min(1024 * 1024, remaining))
            if not chunk:
                _integrity(f"canonical source file changed while copying: {source}")
            source_digest.update(chunk)
            _write_all(destination_fd, chunk)
            remaining -= len(chunk)
        os.fchown(destination_fd, 0, 0)
        os.fchmod(destination_fd, stat.S_IMODE(opened_source.st_mode))
        os.utime(
            destination_fd,
            ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
        )
        _clear_fd_xattrs(destination_fd, f"prepared file {destination}")
        os.fsync(destination_fd)
        os.lseek(destination_fd, 0, os.SEEK_SET)
        destination_digest = hashlib.sha256()
        remaining = opened_source.st_size
        while remaining:
            chunk = os.read(destination_fd, min(1024 * 1024, remaining))
            if not chunk:
                _integrity(f"prepared file changed while verifying: {destination}")
            destination_digest.update(chunk)
            remaining -= len(chunk)
        if destination_digest.digest() != source_digest.digest():
            _integrity(f"prepared file bytes differ from the canonical source: {destination}")
        final_destination = os.fstat(destination_fd)
        destination_path_info = os.stat(
            name,
            dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final_destination.st_mode)
            or final_destination.st_dev != destination_device
            or final_destination.st_nlink != 1
            or final_destination.st_size != opened_source.st_size
            or stat.S_IMODE(final_destination.st_mode) != stat.S_IMODE(opened_source.st_mode)
            or (final_destination.st_uid, final_destination.st_gid) != (0, 0)
            or final_destination.st_mtime_ns != NORMALIZED_MTIME_NS
            or (final_destination.st_dev, final_destination.st_ino)
            != (destination_path_info.st_dev, destination_path_info.st_ino)
        ):
            _integrity(f"prepared file metadata differs from its source: {destination}")
        final_source = os.fstat(source_fd)
        current_source = os.stat(
            name,
            dir_fd=source_parent_fd,
            follow_symlinks=False,
        )
        environment_tool._assert_stable_stat(  # noqa: SLF001
            opened_source,
            final_source,
            f"canonical source file {source}",
        )
        _require_no_fd_xattrs(source_fd, f"canonical source file {source}")
        _require_no_fd_xattrs(destination_fd, f"prepared file {destination}")
        environment_tool._assert_stable_stat(  # noqa: SLF001
            final_source,
            current_source,
            f"canonical source file {source}",
        )
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot copy canonical source file {source}: {error}")
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)


def _copy_source_tree(source_root: Path, destination_root: Path) -> None:
    source_root = Path(os.path.abspath(source_root))
    source_root_fd = -1
    destination_parent_fd = -1
    destination_root_fd = -1

    def close_opened_roots() -> None:
        nonlocal source_root_fd, destination_parent_fd, destination_root_fd
        for descriptor in (
            destination_root_fd,
            destination_parent_fd,
            source_root_fd,
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        source_root_fd = -1
        destination_parent_fd = -1
        destination_root_fd = -1

    try:
        source_root_info = source_root.lstat()
        if source_root.resolve(strict=True) != source_root or source_root.is_symlink():
            _integrity("canonical source root must be a real directory")
        source_root_fd = os.open(source_root, _directory_flags())
        opened_source_root = os.fstat(source_root_fd)
        environment_tool._assert_stable_stat(  # noqa: SLF001
            source_root_info,
            opened_source_root,
            "canonical source root",
        )
        destination_parent_fd, leaf = environment_tool._open_real_parent_beneath(  # noqa: SLF001
            destination_root.parent,
            PurePosixPath(destination_root.name),
            "prepared source root",
        )
        os.mkdir(leaf, mode=0o700, dir_fd=destination_parent_fd)
        destination_root_fd = os.open(
            leaf,
            _directory_flags(),
            dir_fd=destination_parent_fd,
        )
        opened_destination_root = os.fstat(destination_root_fd)
        opened_destination_parent = os.fstat(destination_parent_fd)
        if opened_destination_root.st_dev != opened_destination_parent.st_dev:
            _integrity("prepared source root crosses the staging filesystem boundary")
    except source_tool.SourceToolError:
        close_opened_roots()
        raise
    except (OSError, RuntimeError) as error:
        close_opened_roots()
        _integrity(f"cannot create prepared source root: {error}")
    root_device = source_root_info.st_dev
    destination_device = opened_destination_root.st_dev
    copied_entries = 0
    copied_file_bytes = 0

    def copy_directory(
        source_directory_fd: int,
        destination_directory_fd: int,
        source_directory: Path,
        destination_directory: Path,
        relative_directory: PurePosixPath,
    ) -> None:
        nonlocal copied_entries, copied_file_bytes
        try:
            before_directory = os.fstat(source_directory_fd)
        except OSError as error:
            _integrity(f"cannot inspect canonical source directory {source_directory}: {error}")
        source_mode = _require_tree_metadata(
            source_directory,
            before_directory,
            label="canonical source",
            root_device=root_device,
            expected_kind="directory",
        )
        _require_no_fd_xattrs(
            source_directory_fd,
            f"canonical source directory {source_directory}",
        )
        try:
            child_names = sorted(
                os.listdir(source_directory_fd),
                key=lambda name: name.encode("utf-8"),
            )
        except (OSError, UnicodeError) as error:
            _integrity(f"cannot enumerate canonical source directory {source_directory}: {error}")
        normalized_names: dict[str, str] = {}
        for child_name in child_names:
            if (
                relative_directory == PurePosixPath(".")
                and child_name == materialize_sources.RECEIPT_NAME
            ):
                continue
            materialize_sources._portable_component(  # noqa: SLF001
                child_name,
                f"canonical source path below {source_directory}",
            )
            normalized_name = unicodedata.normalize("NFKC", child_name).casefold()
            previous = normalized_names.get(normalized_name)
            if previous is not None and previous != child_name:
                _integrity(
                    f"canonical source has a case/Unicode collision: "
                    f"{previous} and {child_name}"
                )
            normalized_names[normalized_name] = child_name
            source_child = source_directory / child_name
            destination_child = destination_directory / child_name
            relative = (
                PurePosixPath(child_name)
                if relative_directory == PurePosixPath(".")
                else relative_directory / child_name
            )
            try:
                relative_bytes = relative.as_posix().encode("utf-8")
            except UnicodeEncodeError as error:
                _integrity(f"canonical source contains a non-UTF-8 path: {error}")
            if (
                len(relative.parts) > materialize_sources.MAX_ARCHIVE_PATH_DEPTH
                or len(relative_bytes) > materialize_sources.MAX_ARCHIVE_PATH_BYTES
            ):
                _integrity(
                    "canonical source path exceeds the locked copy limits: "
                    f"{relative.as_posix()}"
                )
            try:
                before = os.stat(
                    child_name,
                    dir_fd=source_directory_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                _integrity(f"cannot inspect canonical source path {source_child}: {error}")
            copied_entries += 1
            if copied_entries > materialize_sources.MAX_TREE_ENTRIES:
                _integrity("canonical source changed beyond the locked entry-count limit")
            if stat.S_ISDIR(before.st_mode):
                _require_tree_metadata(
                    source_child,
                    before,
                    label="canonical source",
                    root_device=root_device,
                    expected_kind="directory",
                )
                source_child_fd = -1
                destination_child_fd = -1
                try:
                    source_child_fd = os.open(
                        child_name,
                        _directory_flags(),
                        dir_fd=source_directory_fd,
                    )
                    opened_source_child = os.fstat(source_child_fd)
                    environment_tool._assert_stable_stat(  # noqa: SLF001
                        before,
                        opened_source_child,
                        f"canonical source directory {source_child}",
                    )
                    os.mkdir(
                        child_name,
                        mode=0o700,
                        dir_fd=destination_directory_fd,
                    )
                    destination_child_fd = os.open(
                        child_name,
                        _directory_flags(),
                        dir_fd=destination_directory_fd,
                    )
                    opened_destination_child = os.fstat(destination_child_fd)
                    if (
                        not stat.S_ISDIR(opened_destination_child.st_mode)
                        or opened_destination_child.st_dev != destination_device
                        or (opened_destination_child.st_dev, opened_destination_child.st_ino)
                        == (opened_source_child.st_dev, opened_source_child.st_ino)
                    ):
                        _integrity(
                            f"prepared source directory is not independent: {destination_child}"
                        )
                    copy_directory(
                        source_child_fd,
                        destination_child_fd,
                        source_child,
                        destination_child,
                        relative,
                    )
                    final_source_child = os.stat(
                        child_name,
                        dir_fd=source_directory_fd,
                        follow_symlinks=False,
                    )
                    environment_tool._assert_stable_stat(  # noqa: SLF001
                        opened_source_child,
                        final_source_child,
                        f"canonical source directory {source_child}",
                    )
                except source_tool.SourceToolError:
                    raise
                except OSError as error:
                    _integrity(
                        f"cannot create prepared source directory {destination_child}: {error}"
                    )
                finally:
                    if destination_child_fd >= 0:
                        os.close(destination_child_fd)
                    if source_child_fd >= 0:
                        os.close(source_child_fd)
                continue
            if stat.S_ISREG(before.st_mode):
                _require_tree_metadata(
                    source_child,
                    before,
                    label="canonical source",
                    root_device=root_device,
                    expected_kind="file",
                )
                if before.st_size < 0 or before.st_size > materialize_sources.MAX_MEMBER_BYTES:
                    _integrity(f"canonical source file exceeds the copy limit: {source_child}")
                copied_file_bytes += before.st_size
                if copied_file_bytes > materialize_sources.MAX_TREE_BYTES:
                    _integrity("canonical source changed beyond the locked tree byte limit")
                _copy_regular_file_at(
                    source_directory_fd,
                    destination_directory_fd,
                    child_name,
                    source_child,
                    destination_child,
                    before,
                    destination_device,
                )
                continue
            if stat.S_ISLNK(before.st_mode):
                _require_tree_metadata(
                    source_child,
                    before,
                    label="canonical source",
                    root_device=root_device,
                    expected_kind="symlink",
                )
                pinned_source_child = _pinned_child_path(source_directory_fd, child_name)
                pinned_destination_child = _pinned_child_path(
                    destination_directory_fd,
                    child_name,
                )
                _require_no_xattrs(pinned_source_child, "canonical source")
                try:
                    target = os.readlink(child_name, dir_fd=source_directory_fd)
                    materialize_sources._resolve_symlink_target(  # noqa: SLF001
                        relative,
                        target,
                        f"canonical source symbolic link {relative.as_posix()}",
                    )
                    os.symlink(target, child_name, dir_fd=destination_directory_fd)
                    os.chown(
                        child_name,
                        0,
                        0,
                        dir_fd=destination_directory_fd,
                        follow_symlinks=False,
                    )
                    os.utime(
                        child_name,
                        ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
                        dir_fd=destination_directory_fd,
                        follow_symlinks=False,
                    )
                    _clear_xattrs(pinned_destination_child)
                    after = os.stat(
                        child_name,
                        dir_fd=source_directory_fd,
                        follow_symlinks=False,
                    )
                    environment_tool._assert_stable_stat(  # noqa: SLF001
                        before,
                        after,
                        f"canonical source symbolic link {source_child}",
                    )
                    copied = os.stat(
                        child_name,
                        dir_fd=destination_directory_fd,
                        follow_symlinks=False,
                    )
                except source_tool.SourceToolError:
                    raise
                except OSError as error:
                    _integrity(
                        f"cannot copy canonical source symbolic link {source_child}: {error}"
                    )
                if (
                    not stat.S_ISLNK(copied.st_mode)
                    or copied.st_dev != destination_device
                    or copied.st_nlink != 1
                    or (copied.st_uid, copied.st_gid) != (0, 0)
                    or copied.st_mtime_ns != NORMALIZED_MTIME_NS
                    or os.readlink(child_name, dir_fd=destination_directory_fd) != target
                    or (copied.st_dev, copied.st_ino) == (before.st_dev, before.st_ino)
                ):
                    _integrity(
                        f"prepared symbolic link differs from its source: {destination_child}"
                    )
                continue
            _integrity(f"canonical source contains an unsupported entry type: {source_child}")

        try:
            os.fchown(destination_directory_fd, 0, 0)
            os.fchmod(destination_directory_fd, source_mode)
            os.utime(
                destination_directory_fd,
                ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS),
            )
            _clear_fd_xattrs(
                destination_directory_fd,
                f"prepared source directory {destination_directory}",
            )
            os.fsync(destination_directory_fd)
            after_directory = os.fstat(source_directory_fd)
        except OSError as error:
            _integrity(
                f"cannot normalize prepared source directory {destination_directory}: {error}"
            )
        environment_tool._assert_stable_stat(  # noqa: SLF001
            before_directory,
            after_directory,
            f"canonical source directory {source_directory}",
        )
        _require_no_fd_xattrs(
            source_directory_fd,
            f"canonical source directory {source_directory}",
        )

    try:
        if (opened_source_root.st_dev, opened_source_root.st_ino) == (
            opened_destination_root.st_dev,
            opened_destination_root.st_ino,
        ):
            _integrity("prepared source root aliases the canonical source root")
        copy_directory(
            source_root_fd,
            destination_root_fd,
            source_root,
            destination_root,
            PurePosixPath("."),
        )
        final_source_root_fd = os.fstat(source_root_fd)
        final_source_root_path = source_root.lstat()
        environment_tool._assert_stable_stat(  # noqa: SLF001
            opened_source_root,
            final_source_root_fd,
            "canonical source root",
        )
        environment_tool._assert_stable_stat(  # noqa: SLF001
            final_source_root_fd,
            final_source_root_path,
            "canonical source root",
        )
        final_destination_root_fd = os.fstat(destination_root_fd)
        final_destination_root_path = os.stat(
            destination_root.name,
            dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
        if (
            final_destination_root_fd.st_dev != destination_device
            or (final_destination_root_fd.st_dev, final_destination_root_fd.st_ino)
            != (final_destination_root_path.st_dev, final_destination_root_path.st_ino)
        ):
            _integrity("prepared source root changed while copying")
    finally:
        close_opened_roots()


def _verify_overlay_origins(profile: LoadedProfile, workspace: Path) -> None:
    workspace = workspace.resolve()
    for overlay in profile.overlays:
        destination = workspace / PurePosixPath(str(overlay["destination"]))
        try:
            destination.relative_to(workspace)
        except ValueError:
            _integrity(f"overlay destination escapes the source workspace: {destination}")
        try:
            before = destination.lstat()
        except FileNotFoundError as error:
            raise source_tool.SourceToolError(
                f"overlay source not found: {destination}",
                source_tool.EXIT_MISSING,
            ) from error
        except OSError as error:
            _integrity(f"cannot inspect overlay source {destination}: {error}")
        raw = _stable_bytes(
            destination,
            maximum=MAX_PROFILE_BYTES,
            label=f"overlay source {overlay['destination']}",
            missing_exit=source_tool.EXIT_MISSING,
        )
        try:
            after = destination.lstat()
        except FileNotFoundError as error:
            _integrity(f"overlay source disappeared after verification: {destination}")
        except OSError as error:
            _integrity(f"cannot re-inspect overlay source {destination}: {error}")
        composition_tool._assert_stable_file_stat(  # noqa: SLF001
            before,
            after,
            f"overlay source {overlay['destination']}",
        )
        if len(raw) != overlay["originalSize"]:
            _integrity(f"overlay source size differs: {overlay['destination']}")
        if hashlib.sha256(raw).hexdigest() != overlay["originalSha256"]:
            _integrity(f"overlay source digest differs: {overlay['destination']}")
        mode = stat.S_IMODE(before.st_mode)
        if mode != overlay["originalMode"]:
            _integrity(f"overlay source mode differs: {overlay['destination']}")


def _verify_runtime_libraries(profile: LoadedProfile, sdk_root: Path) -> None:
    ndk_version = str(profile.toolchain["ndkVersion"])
    for index, abi in enumerate(profile.abis):
        relative = (
            f"android-sdk/ndk/{ndk_version}/toolchains/llvm/prebuilt/linux-x86_64/"
            f"sysroot/usr/lib/{abi['ndkRuntimeDirectory']}/libc++_shared.so"
        )
        label = f"abi[{index}] runtime library"
        runtime = _resolve_regular_file_beneath(
            sdk_root,
            relative,
            label,
            base_label="SDK projection root",
        )
        try:
            before = runtime.lstat()
        except FileNotFoundError as error:
            raise source_tool.SourceToolError(
                f"{label} not found: {runtime}",
                source_tool.EXIT_MISSING,
            ) from error
        except OSError as error:
            _integrity(f"cannot inspect {label} {runtime}: {error}")
        raw = _stable_bytes(
            runtime,
            maximum=MAX_RUNTIME_LIBRARY_BYTES,
            label=label,
            missing_exit=source_tool.EXIT_MISSING,
        )
        try:
            after = runtime.lstat()
        except FileNotFoundError:
            _integrity(f"{label} disappeared after verification: {runtime}")
        except OSError as error:
            _integrity(f"cannot re-inspect {label} {runtime}: {error}")
        composition_tool._assert_stable_file_stat(  # noqa: SLF001
            before,
            after,
            label,
        )
        if len(raw) != abi["runtimeSize"]:
            _integrity(f"{label} size differs: {runtime}")
        if hashlib.sha256(raw).hexdigest() != abi["runtimeSha256"]:
            _integrity(f"{label} digest differs: {runtime}")
        _resolve_regular_file_beneath(
            sdk_root,
            relative,
            label,
            base_label="SDK projection root",
        )


def _read_canonical_json_receipt(
    path: Path,
    *,
    label: str,
    maximum: int,
) -> tuple[dict[str, object], bytes]:
    descriptor = -1
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"{label} is missing: {path}",
            source_tool.EXIT_MISSING,
        ) from error
    except OSError as error:
        _integrity(f"cannot inspect {label} {path}: {error}")
    if (
        not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or before.st_nlink != 1
        or before.st_size < 0
        or before.st_size > maximum
        or stat.S_IMODE(before.st_mode) != 0o644
        or (before.st_uid, before.st_gid) != (0, 0)
        or before.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity(f"{label} metadata is not canonical: {path}")
    _require_no_xattrs(path, label)
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        environment_tool._assert_stable_stat(before, opened, label)  # noqa: SLF001
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                _integrity(f"{label} changed while reading: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        final_fd = os.fstat(descriptor)
        final_path = path.lstat()
        environment_tool._assert_stable_stat(opened, final_fd, label)  # noqa: SLF001
        environment_tool._assert_stable_stat(final_fd, final_path, label)  # noqa: SLF001
        raw = b"".join(chunks)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot read {label} {path}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _require_no_xattrs(path, label)
    receipt = environment_tool._read_json_bytes(raw, label)  # noqa: SLF001
    try:
        canonical = _canonical_json(receipt)
    except (TypeError, ValueError, RecursionError) as error:
        _integrity(f"{label} contains a non-canonical JSON value: {error}")
    if raw != canonical:
        _integrity(f"{label} is not canonical JSON")
    return receipt, raw


def _overlay_snapshots(profile: LoadedProfile) -> tuple[bytes, ...]:
    result: list[bytes] = []
    for index, overlay in enumerate(profile.overlays):
        replacement = _resolve_repository_file(
            REPOSITORY_ROOT,
            str(overlay["replacement"]),
            f"overlay[{index}].replacement",
        )
        raw = _stable_bytes(
            replacement,
            maximum=MAX_PROFILE_BYTES,
            label=f"overlay replacement {overlay['replacement']}",
            missing_exit=source_tool.EXIT_MISSING,
        )
        if (
            len(raw) != overlay["replacementSize"]
            or hashlib.sha256(raw).hexdigest() != overlay["replacementSha256"]
        ):
            _integrity(f"overlay replacement changed: {overlay['replacement']}")
        result.append(raw)
    return tuple(result)


def _snapshot_preparation_inputs(
    profile: LoadedProfile,
    source_workspace: Path,
    composition_receipt_path: Path,
) -> PreparationInputs:
    _require_no_nested_mounts(source_workspace, "canonical source workspace")
    source_receipt, source_receipt_raw = _read_canonical_json_receipt(
        source_workspace / materialize_sources.RECEIPT_NAME,
        label="source materialization receipt",
        maximum=materialize_sources.MAX_RECEIPT_BYTES,
    )
    materialize_sources._validate_receipt_shape(source_receipt)  # noqa: SLF001
    if source_receipt["linkMode"] != "preserve":
        _integrity("native build preparation requires a preserve-mode source receipt")
    if source_receipt["manifestSha256"] != profile.project["sourceManifestSha256"]:
        _integrity("source materialization receipt is not bound to the native build profile")
    canonical_source = _scan_policy_tree(
        source_workspace,
        "canonical source",
        skip_materialization_receipt=True,
    )
    if canonical_source.tree != source_receipt["tree"]:
        _integrity("canonical source policy scan differs from its materialization receipt")
    composition_receipt, composition_receipt_raw = (
        composition_tool._read_composition_receipt(  # noqa: SLF001
            composition_receipt_path
        )
    )
    try:
        canonical_composition_receipt = _canonical_json(composition_receipt)
    except (TypeError, ValueError, RecursionError) as error:
        _integrity(f"toolchain composition receipt contains a non-canonical JSON value: {error}")
    if composition_receipt_raw != canonical_composition_receipt:
        _integrity("toolchain composition receipt is not canonical JSON")
    if (
        composition_receipt.get("kind") != composition_tool.RECEIPT_KIND
        or composition_receipt.get("ready") is not False
        or composition_receipt.get("releaseInput") is not False
        or not isinstance(composition_receipt.get("inputs"), dict)
    ):
        _integrity("toolchain composition receipt has an unexpected preparation boundary")
    composition = composition_receipt.get("composition")
    if not isinstance(composition, dict) or not isinstance(composition.get("sha256"), str):
        _integrity("toolchain composition receipt omits its composition digest")
    return PreparationInputs(
        profile=profile,
        source_receipt=source_receipt,
        source_receipt_raw=source_receipt_raw,
        composition_receipt=composition_receipt,
        composition_receipt_raw=composition_receipt_raw,
        overlay_raws=_overlay_snapshots(profile),
        canonical_source=canonical_source,
    )


def _assert_same_preparation_inputs(
    before: PreparationInputs,
    after: PreparationInputs,
) -> None:
    comparisons = (
        (before.profile.raw, after.profile.raw, "native build profile"),
        (before.source_receipt_raw, after.source_receipt_raw, "source receipt"),
        (
            before.composition_receipt_raw,
            after.composition_receipt_raw,
            "toolchain composition receipt",
        ),
        (before.overlay_raws, after.overlay_raws, "overlay replacements"),
        (before.canonical_source.tree, after.canonical_source.tree, "canonical source tree"),
        (
            before.canonical_source.file_bytes,
            after.canonical_source.file_bytes,
            "canonical source file-byte count",
        ),
        (
            before.canonical_source.symlinks,
            after.canonical_source.symlinks,
            "canonical source symbolic links",
        ),
        (
            before.canonical_source.identities,
            after.canonical_source.identities,
            "canonical source identities",
        ),
    )
    for expected, actual, label in comparisons:
        if expected != actual:
            _integrity(f"{label} changed during native build preparation")


def _apply_overlays(
    profile: LoadedProfile,
    source_copy: Path,
    overlay_raws: tuple[bytes, ...],
) -> None:
    if len(overlay_raws) != len(profile.overlays):
        _integrity("overlay snapshot count differs from the native build profile")
    for index, (overlay, replacement_raw) in enumerate(
        zip(profile.overlays, overlay_raws, strict=True)
    ):
        relative = PurePosixPath(str(overlay["destination"]))
        parent_fd, leaf = environment_tool._open_real_parent_beneath(  # noqa: SLF001
            source_copy,
            relative,
            f"overlay[{index}] destination",
        )
        source_fd = -1
        temporary_fd = -1
        temporary_name = f".{leaf}.{secrets.token_hex(8)}.overlay"
        try:
            before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or (before.st_uid, before.st_gid) != (0, 0)
                or stat.S_IMODE(before.st_mode) != overlay["originalMode"]
                or before.st_mtime_ns != NORMALIZED_MTIME_NS
            ):
                _integrity(f"overlay destination metadata differs: {relative.as_posix()}")
            source_fd = os.open(
                leaf,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(source_fd)
            environment_tool._assert_stable_stat(  # noqa: SLF001
                before,
                opened,
                f"overlay destination {relative.as_posix()}",
            )
            original_chunks: list[bytes] = []
            remaining = opened.st_size
            while remaining:
                chunk = os.read(source_fd, min(1024 * 1024, remaining))
                if not chunk:
                    _integrity(
                        f"overlay destination changed while reading: {relative.as_posix()}"
                    )
                original_chunks.append(chunk)
                remaining -= len(chunk)
            original_raw = b"".join(original_chunks)
            if (
                len(original_raw) != overlay["originalSize"]
                or hashlib.sha256(original_raw).hexdigest() != overlay["originalSha256"]
            ):
                _integrity(f"overlay origin bytes differ: {relative.as_posix()}")
            if (
                len(replacement_raw) != overlay["replacementSize"]
                or hashlib.sha256(replacement_raw).hexdigest()
                != overlay["replacementSha256"]
            ):
                _integrity(f"overlay replacement bytes differ: {overlay['replacement']}")
            final_source_fd = os.fstat(source_fd)
            final_source_path = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            environment_tool._assert_stable_stat(  # noqa: SLF001
                opened,
                final_source_fd,
                f"overlay origin {relative.as_posix()}",
            )
            environment_tool._assert_stable_stat(  # noqa: SLF001
                final_source_fd,
                final_source_path,
                f"overlay origin {relative.as_posix()}",
            )
            temporary_fd = os.open(
                temporary_name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
            _write_all(temporary_fd, replacement_raw)
            os.fchown(temporary_fd, 0, 0)
            os.fchmod(temporary_fd, int(overlay["replacementMode"]))
            os.utime(temporary_fd, ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS))
            _clear_fd_xattrs(
                temporary_fd,
                f"overlay temporary file {relative.as_posix()}",
            )
            os.fsync(temporary_fd)
            os.lseek(temporary_fd, 0, os.SEEK_SET)
            remaining = len(replacement_raw)
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(temporary_fd, min(1024 * 1024, remaining))
                if not chunk:
                    _integrity(f"overlay write was truncated: {relative.as_posix()}")
                chunks.append(chunk)
                remaining -= len(chunk)
            applied = b"".join(chunks)
            temporary_info = os.fstat(temporary_fd)
            if (
                applied != replacement_raw
                or not stat.S_ISREG(temporary_info.st_mode)
                or temporary_info.st_nlink != 1
                or temporary_info.st_size != len(replacement_raw)
                or (temporary_info.st_uid, temporary_info.st_gid) != (0, 0)
                or stat.S_IMODE(temporary_info.st_mode) != overlay["replacementMode"]
                or temporary_info.st_mtime_ns != NORMALIZED_MTIME_NS
            ):
                _integrity(f"overlay application could not be verified: {relative.as_posix()}")
            current_source = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            environment_tool._assert_stable_stat(  # noqa: SLF001
                final_source_path,
                current_source,
                f"overlay origin {relative.as_posix()}",
            )
            os.replace(
                temporary_name,
                leaf,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            published = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (published.st_dev, published.st_ino) != (
                temporary_info.st_dev,
                temporary_info.st_ino,
            ):
                _integrity(f"overlay publication identity differs: {relative.as_posix()}")
            os.utime(parent_fd, ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS))
            os.fsync(parent_fd)
            _require_no_xattrs(
                _pinned_child_path(parent_fd, leaf),
                f"overlay[{index}] destination",
            )
        except source_tool.SourceToolError:
            raise
        except OSError as error:
            _integrity(f"cannot apply overlay {relative.as_posix()}: {error}")
        finally:
            if temporary_fd >= 0:
                os.close(temporary_fd)
            if source_fd >= 0:
                os.close(source_fd)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError as error:
                os.close(parent_fd)
                _integrity(
                    f"cannot clean overlay temporary file {temporary_name}: {error}"
                )
            os.close(parent_fd)


def _assert_independent_copy(
    canonical: TreePolicySnapshot,
    prepared: TreePolicySnapshot,
) -> None:
    if set(canonical.identities) != set(prepared.identities):
        _integrity("prepared source path set differs from the canonical source")
    for relative, prepared_identity in prepared.identities.items():
        if prepared_identity == canonical.identities[relative]:
            _integrity(f"prepared source aliases canonical identity at {relative}")
    shared_identities = set(canonical.identities.values()) & set(
        prepared.identities.values()
    )
    if shared_identities:
        _integrity("prepared source aliases a canonical identity across paths")


def _preparation_receipt_data(
    inputs: PreparationInputs,
    prepared_source: TreePolicySnapshot,
) -> dict[str, object]:
    profile = inputs.profile
    composition = inputs.composition_receipt["composition"]
    assert isinstance(composition, dict)
    overlay_records = [
        {
            "destination": overlay["destination"],
            "original": {
                "size": overlay["originalSize"],
                "sha256": overlay["originalSha256"],
                "mode": overlay["originalMode"],
            },
            "replacement": {
                "path": overlay["replacement"],
                "size": overlay["replacementSize"],
                "sha256": overlay["replacementSha256"],
                "mode": overlay["replacementMode"],
            },
            "appliedMode": overlay["replacementMode"],
        }
        for overlay in profile.overlays
    ]
    return {
        "schemaVersion": 1,
        "kind": PREPARATION_RECEIPT_KIND,
        "phase": "prepared",
        "buildExecuted": False,
        "ready": False,
        "releaseInput": False,
        "project": {
            "name": profile.project["name"],
            "profile": profile.project["profile"],
            "upstreamRevision": profile.project["upstreamRevision"],
            "nativeApi": profile.project["nativeApi"],
            "abis": [abi["name"] for abi in profile.abis],
            "pageSizeBytes": profile.project["pageSizeBytes"],
        },
        "locks": {
            "profileSha256": profile.sha256,
            "sourceManifestSha256": profile.project["sourceManifestSha256"],
            "toolchainManifestSha256": profile.project["toolchainManifestSha256"],
            "sourceMaterializationReceiptSha256": hashlib.sha256(
                inputs.source_receipt_raw
            ).hexdigest(),
            "toolchainCompositionReceiptSha256": hashlib.sha256(
                inputs.composition_receipt_raw
            ).hexdigest(),
            "toolchainCompositionSha256": composition["sha256"],
        },
        "toolchainInputs": copy.deepcopy(inputs.composition_receipt["inputs"]),
        "mounts": {
            "source": profile.policy["sourceCopyMount"],
            "output": profile.policy["outputMount"],
            "home": profile.policy["buildHomeMount"],
            "temporary": profile.policy["temporaryMount"],
        },
        "policy": {
            "networkAtBuild": profile.policy["networkAtBuild"],
            "canonicalSource": "verified-read-only-input",
            "sourceCopy": "fresh-independent-regular-file-copy",
            "sourceCopyOwner": "root:root",
            "sourceCopyWritableBy": "isolated-root-builder",
            "hardlinks": "rejected",
            "symlinks": "preserved-within-source-tree",
            "overlays": "locked-bytes-applied-before-publication",
            "inheritedEnvironment": "empty",
            "freshPaths": ["source", "output", "home", "tmp"],
        },
        "layout": {
            "rootMode": 0o700,
            "sourceMode": 0o755,
            "emptyDirectoryMode": 0o700,
            "emptyDirectories": ["home", "output", "tmp"],
            "normalizedMtimeNs": NORMALIZED_MTIME_NS,
        },
        "commands": copy.deepcopy(profile.build["commands"]),
        "source": {
            "linkMode": inputs.source_receipt["linkMode"],
            "treeBeforeOverlays": copy.deepcopy(inputs.canonical_source.tree),
            "treeAfterOverlays": copy.deepcopy(prepared_source.tree),
            "fileBytesAfterOverlays": prepared_source.file_bytes,
            "entryIdentitiesIndependent": True,
            "hardlinkCount": 0,
            "symlinks": [copy.deepcopy(item) for item in prepared_source.symlinks],
        },
        "overlays": overlay_records,
    }


def _write_preparation_receipt(path: Path, receipt: dict[str, object]) -> None:
    raw = _canonical_json(receipt)
    if len(raw) > MAX_PREPARATION_RECEIPT_BYTES:
        _integrity("native build preparation receipt exceeds its byte limit")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        _write_all(descriptor, raw)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o644)
        os.utime(descriptor, ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS))
        _clear_fd_xattrs(descriptor, "native build preparation receipt")
        os.fsync(descriptor)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot write native build preparation receipt: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _resolved_build_workspace(
    workspace: Path,
    *,
    source_cache: Path,
    toolchain_cache: Path,
    source_workspace: Path,
    apt_root: Path,
    sdk_root: Path,
    composition_receipt: Path,
    must_exist: bool,
) -> tuple[Path, Path]:
    if not workspace.is_absolute():
        _schema("native build workspace must be an absolute path")
    workspace = Path(os.path.abspath(workspace))
    if workspace.parent == workspace:
        _schema("native build workspace must not be a filesystem root")
    materialize_sources._portable_component(  # noqa: SLF001
        workspace.name,
        "native build workspace",
    )
    try:
        parent = materialize_sources._require_real_workspace_parent(  # noqa: SLF001
            workspace,
            create=False,
        )
    except RuntimeError as error:
        _schema(f"cannot resolve native build workspace parent: {error}")
    environment_tool._trusted_output_parent_identity(parent)  # noqa: SLF001
    rootfs_tool._require_ext4(parent)  # noqa: SLF001
    exists = _lexists(workspace)
    if must_exist and not exists:
        raise source_tool.SourceToolError(
            f"native build workspace is missing: {workspace}",
            source_tool.EXIT_MISSING,
        )
    if not must_exist and exists:
        _integrity(f"native build workspace already exists; refusing to overwrite: {workspace}")

    forbidden = (
        REPOSITORY_ROOT,
        source_cache,
        toolchain_cache,
        source_workspace,
        apt_root,
        sdk_root,
        composition_receipt,
    )
    for candidate in forbidden:
        try:
            candidate_path = Path(os.path.abspath(candidate)).resolve(strict=True)
        except FileNotFoundError as error:
            raise source_tool.SourceToolError(
                f"native build input is missing while checking workspace separation: {candidate}",
                source_tool.EXIT_MISSING,
            ) from error
        except (OSError, RuntimeError) as error:
            _schema(f"cannot resolve native build input {candidate}: {error}")
        if _paths_overlap(workspace, candidate_path):
            _schema(
                "native build workspace must be disjoint from repositories, caches, "
                f"and immutable inputs: {candidate_path}"
            )
    return parent, workspace


def _open_pinned_output_parent(parent: Path) -> tuple[int, tuple[int, int]]:
    descriptor = -1
    try:
        before = parent.lstat()
        descriptor = os.open(parent, _directory_flags())
        opened = os.fstat(descriptor)
        environment_tool._assert_stable_stat(  # noqa: SLF001
            before,
            opened,
            "native build output parent",
        )
        environment_tool._trusted_output_parent_identity(parent)  # noqa: SLF001
        return descriptor, (opened.st_dev, opened.st_ino)
    except source_tool.SourceToolError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        _integrity(f"cannot pin native build output parent {parent}: {error}")


def _assert_pinned_output_parent(
    parent: Path,
    descriptor: int,
    identity: tuple[int, int],
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = parent.lstat()
    except OSError as error:
        _integrity(f"cannot re-inspect native build output parent {parent}: {error}")
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or parent.is_symlink()
        or (opened.st_dev, opened.st_ino) != identity
        or (current.st_dev, current.st_ino) != identity
        or (opened.st_uid, opened.st_gid) != (0, 0)
        or (current.st_uid, current.st_gid) != (0, 0)
        or stat.S_IMODE(opened.st_mode) != stat.S_IMODE(current.st_mode)
    ):
        _integrity("native build output parent identity or policy changed")


def _assert_absent_at(parent_fd: int, name: str, label: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        _integrity(f"cannot inspect {label}: {error}")
    _integrity(f"{label} already exists; refusing to overwrite")


def _rename_workspace_no_replace_at(
    parent_fd: int,
    source: str,
    destination: str,
) -> None:
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
        _integrity(f"native build workspace appeared during publication: {destination}")
    raise source_tool.SourceToolError(
        f"cannot publish native build workspace: "
        f"{os.strerror(error_number)}: {destination}",
        source_tool.EXIT_INTEGRITY,
    )


def _create_staging_at(
    parent: Path,
    parent_fd: int,
    workspace_name: str,
) -> tuple[Path, tuple[int, int]]:
    for _attempt in range(128):
        name = f".{workspace_name}.{secrets.token_hex(8)}.part"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as error:
            _integrity(f"cannot create native build staging directory: {error}")
        try:
            parent_info = os.fstat(parent_fd)
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_dev != parent_info.st_dev
                or info.st_nlink < 2
                or (info.st_uid, info.st_gid) != (0, 0)
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                _integrity("new native build staging directory is not private and root-owned")
        except BaseException as error:
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError as cleanup_error:
                raise source_tool.SourceToolError(
                    "cannot validate or remove new native build staging directory: "
                    f"{cleanup_error}",
                    source_tool.EXIT_INTERNAL,
                ) from error
            if isinstance(error, source_tool.SourceToolError):
                raise
            if isinstance(error, OSError):
                _integrity(f"cannot inspect new native build staging directory: {error}")
            raise
        return parent / name, (info.st_dev, info.st_ino)
    _integrity("cannot allocate a unique native build staging directory")


def _remove_preparation_staging(
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> str | None:
    try:
        mounts = _mounts_below(path)
    except source_tool.SourceToolError as error:
        return f"cannot prove staging mount safety; refusing recursive cleanup: {error}"
    if mounts:
        return (
            "staging contains a host-visible nested mount; refusing recursive cleanup: "
            + mounts.decode("utf-8", "replace")
        )
    return materialize_sources._remove_tree(path, expected_identity)  # noqa: SLF001


def _assert_staging_identity_at(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    try:
        parent_info = os.fstat(parent_fd)
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        _integrity(f"cannot re-inspect native build staging directory: {error}")
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_dev != parent_info.st_dev
        or (info.st_dev, info.st_ino) != expected_identity
        or info.st_nlink < 2
        or (info.st_uid, info.st_gid) != (0, 0)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_mtime_ns != NORMALIZED_MTIME_NS
    ):
        _integrity("native build staging directory identity changed before publication")
    _require_no_xattrs(
        _pinned_child_path(parent_fd, name),
        "native build staging directory",
    )


def _require_preparation_capacity(
    parent_fd: int,
    canonical_source: TreePolicySnapshot,
) -> None:
    try:
        filesystem = os.fstatvfs(parent_fd)
    except OSError as error:
        _integrity(f"cannot inspect native build workspace capacity: {error}")
    required_bytes = canonical_source.file_bytes + max(
        PREPARATION_FREE_BYTE_MARGIN,
        canonical_source.file_bytes // 4,
    )
    available_bytes = filesystem.f_bavail * filesystem.f_frsize
    if available_bytes < required_bytes:
        _integrity(
            "native build workspace filesystem lacks preparation capacity: "
            f"requires at least {required_bytes} available bytes, observed {available_bytes}"
        )
    required_inodes = int(canonical_source.tree["entryCount"]) + PREPARATION_FREE_INODE_MARGIN
    if filesystem.f_favail and filesystem.f_favail < required_inodes:
        _integrity(
            "native build workspace filesystem lacks preparation inodes: "
            f"requires at least {required_inodes}, observed {filesystem.f_favail}"
        )


def _create_empty_prepared_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
        os.chown(path, 0, 0)
        os.chmod(path, 0o700)
        os.utime(path, ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS))
        _clear_xattrs(path)
        materialize_sources._fsync_directory(path)  # noqa: SLF001
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot create prepared directory {path}: {error}")


def _verify_empty_prepared_directory(
    path: Path,
    label: str,
    *,
    root_device: int,
) -> tuple[int, int]:
    try:
        before = path.lstat()
        children = list(path.iterdir())
        after = path.lstat()
    except OSError as error:
        _integrity(f"cannot inspect prepared {label} directory {path}: {error}")
    environment_tool._assert_stable_stat(  # noqa: SLF001
        before,
        after,
        f"prepared {label} directory",
    )
    mode = _require_tree_metadata(
        path,
        before,
        label=f"prepared {label}",
        root_device=root_device,
        expected_kind="directory",
    )
    if mode != 0o700:
        _integrity(f"prepared {label} directory mode must be 0700")
    if children:
        _integrity(f"prepared {label} directory must be empty")
    _require_no_xattrs(path, f"prepared {label}")
    return before.st_dev, before.st_ino


def _verify_applied_overlays(
    profile: LoadedProfile,
    source_copy: Path,
    overlay_raws: tuple[bytes, ...],
) -> None:
    if len(overlay_raws) != len(profile.overlays):
        _integrity("overlay snapshot count differs from the native build profile")
    for index, (overlay, expected_raw) in enumerate(
        zip(profile.overlays, overlay_raws, strict=True)
    ):
        relative = PurePosixPath(str(overlay["destination"]))
        raw, info = environment_tool._read_stable_beneath(  # noqa: SLF001
            source_copy,
            relative,
            maximum=MAX_PROFILE_BYTES,
            label=f"overlay[{index}] prepared destination",
        )
        if (
            raw != expected_raw
            or len(raw) != overlay["replacementSize"]
            or hashlib.sha256(raw).hexdigest() != overlay["replacementSha256"]
            or info.st_nlink != 1
            or (info.st_uid, info.st_gid) != (0, 0)
            or stat.S_IMODE(info.st_mode) != overlay["replacementMode"]
            or info.st_mtime_ns != NORMALIZED_MTIME_NS
        ):
            _integrity(f"prepared overlay differs: {relative.as_posix()}")
        parent_fd, leaf = environment_tool._open_real_parent_beneath(  # noqa: SLF001
            source_copy,
            relative,
            f"overlay[{index}] prepared destination",
        )
        descriptor = -1
        try:
            descriptor = os.open(
                leaf,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(descriptor)
            environment_tool._assert_stable_stat(  # noqa: SLF001
                info,
                opened,
                f"overlay[{index}] prepared destination",
            )
            _require_no_fd_xattrs(
                descriptor,
                f"overlay[{index}] prepared destination",
            )
        except source_tool.SourceToolError:
            raise
        except OSError as error:
            _integrity(f"cannot inspect prepared overlay {relative.as_posix()}: {error}")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)


def _verify_prepared_workspace(
    workspace: Path,
    inputs: PreparationInputs,
) -> tuple[dict[str, object], bytes]:
    try:
        root_before = workspace.lstat()
        resolved = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        _integrity(f"cannot inspect native build workspace {workspace}: {error}")
    if resolved != workspace or workspace.is_symlink() or not stat.S_ISDIR(root_before.st_mode):
        _integrity(f"native build workspace must be a canonical real directory: {workspace}")
    _require_no_nested_mounts(workspace, "native build workspace")
    root_device = root_before.st_dev
    root_mode = _require_tree_metadata(
        workspace,
        root_before,
        label="native build workspace",
        root_device=root_device,
        expected_kind="directory",
    )
    if root_mode != 0o700:
        _integrity("native build workspace root mode must be 0700")
    _require_no_xattrs(workspace, "native build workspace")
    try:
        children = sorted(child.name for child in workspace.iterdir())
    except OSError as error:
        _integrity(f"cannot enumerate native build workspace {workspace}: {error}")
    expected_children = sorted(
        ("source", "output", "home", "tmp", PREPARATION_RECEIPT_NAME)
    )
    if children != expected_children:
        _integrity("native build workspace has an unexpected root entry set")

    prepared_source = _scan_policy_tree(
        workspace / "source",
        "prepared source",
        skip_materialization_receipt=False,
    )
    _assert_independent_copy(inputs.canonical_source, prepared_source)
    if prepared_source.symlinks != inputs.canonical_source.symlinks:
        _integrity("prepared source symbolic-link topology differs from the canonical source")
    _verify_applied_overlays(inputs.profile, workspace / "source", inputs.overlay_raws)

    empty_identities = {
        _verify_empty_prepared_directory(
            workspace / name,
            name,
            root_device=root_device,
        )
        for name in ("output", "home", "tmp")
    }
    source_identity = prepared_source.identities["."]
    if (
        len(empty_identities) != 3
        or source_identity[0] != root_device
        or source_identity in empty_identities
        or (root_before.st_dev, root_before.st_ino) in empty_identities
        or source_identity == (root_before.st_dev, root_before.st_ino)
    ):
        _integrity("prepared workspace paths do not have distinct filesystem identities")

    expected_receipt = _preparation_receipt_data(inputs, prepared_source)
    actual_receipt, actual_raw = _read_canonical_json_receipt(
        workspace / PREPARATION_RECEIPT_NAME,
        label="native build preparation receipt",
        maximum=MAX_PREPARATION_RECEIPT_BYTES,
    )
    if actual_receipt != expected_receipt or actual_raw != _canonical_json(expected_receipt):
        _integrity("native build preparation receipt differs from its current inputs or tree")
    root_after = workspace.lstat()
    environment_tool._assert_stable_stat(  # noqa: SLF001
        root_before,
        root_after,
        "native build workspace root",
    )
    _require_no_xattrs(workspace, "native build workspace")
    return actual_receipt, actual_raw


def verify_preparation(
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
    source_cache: Path,
    toolchain_cache: Path,
    source_workspace: Path,
    apt_root: Path,
    sdk_root: Path,
    composition_receipt: Path,
    build_workspace: Path,
) -> None:
    _require_linux_root("preparation verification")
    _parent, build_workspace = _resolved_build_workspace(
        build_workspace,
        source_cache=source_cache,
        toolchain_cache=toolchain_cache,
        source_workspace=source_workspace,
        apt_root=apt_root,
        sdk_root=sdk_root,
        composition_receipt=composition_receipt,
        must_exist=True,
    )
    profile = preflight(
        profile_path,
        source_manifest_path,
        toolchain_manifest_path,
        source_cache,
        toolchain_cache,
        source_workspace,
        apt_root,
        sdk_root,
        composition_receipt,
        announce=False,
    )
    inputs = _snapshot_preparation_inputs(profile, source_workspace, composition_receipt)
    _receipt, raw = _verify_prepared_workspace(build_workspace, inputs)
    print(
        "verified native build preparation (build not executed): "
        f"workspace={build_workspace}; receipt={hashlib.sha256(raw).hexdigest()}"
    )


def prepare(
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
    source_cache: Path,
    toolchain_cache: Path,
    source_workspace: Path,
    apt_root: Path,
    sdk_root: Path,
    composition_receipt: Path,
    build_workspace: Path,
) -> None:
    _require_linux_root("preparation")
    trusted_parent, build_workspace = _resolved_build_workspace(
        build_workspace,
        source_cache=source_cache,
        toolchain_cache=toolchain_cache,
        source_workspace=source_workspace,
        apt_root=apt_root,
        sdk_root=sdk_root,
        composition_receipt=composition_receipt,
        must_exist=False,
    )
    parent_fd = -1
    parent_identity: tuple[int, int] | None = None
    staging: Path | None = None
    staging_identity: tuple[int, int] | None = None
    try:
        parent_fd, parent_identity = _open_pinned_output_parent(trusted_parent)
        _assert_absent_at(
            parent_fd,
            build_workspace.name,
            "native build workspace",
        )
        profile = preflight(
            profile_path,
            source_manifest_path,
            toolchain_manifest_path,
            source_cache,
            toolchain_cache,
            source_workspace,
            apt_root,
            sdk_root,
            composition_receipt,
            announce=False,
        )
        inputs_before = _snapshot_preparation_inputs(
            profile,
            source_workspace,
            composition_receipt,
        )
        _assert_pinned_output_parent(trusted_parent, parent_fd, parent_identity)
        _assert_absent_at(
            parent_fd,
            build_workspace.name,
            "native build workspace",
        )
        _require_preparation_capacity(parent_fd, inputs_before.canonical_source)
        staging, staging_identity = _create_staging_at(
            trusted_parent,
            parent_fd,
            build_workspace.name,
        )
        _require_no_nested_mounts(staging, "native build staging workspace")
        _clear_xattrs(staging)

        source_copy = staging / "source"
        _copy_source_tree(source_workspace, source_copy)
        copied_baseline = _scan_policy_tree(
            source_copy,
            "prepared source before overlays",
            skip_materialization_receipt=False,
        )
        if (
            copied_baseline.tree != inputs_before.canonical_source.tree
            or copied_baseline.file_bytes != inputs_before.canonical_source.file_bytes
            or copied_baseline.symlinks != inputs_before.canonical_source.symlinks
        ):
            _integrity("prepared source copy differs from the canonical source baseline")
        _assert_independent_copy(inputs_before.canonical_source, copied_baseline)

        _apply_overlays(profile, source_copy, inputs_before.overlay_raws)
        prepared_source = _scan_policy_tree(
            source_copy,
            "prepared source",
            skip_materialization_receipt=False,
        )
        _assert_independent_copy(inputs_before.canonical_source, prepared_source)
        if prepared_source.symlinks != inputs_before.canonical_source.symlinks:
            _integrity("prepared source symbolic-link topology changed while applying overlays")
        _verify_applied_overlays(profile, source_copy, inputs_before.overlay_raws)

        for name in ("output", "home", "tmp"):
            _create_empty_prepared_directory(staging / name)
        receipt = _preparation_receipt_data(inputs_before, prepared_source)
        _write_preparation_receipt(staging / PREPARATION_RECEIPT_NAME, receipt)
        os.chown(staging, 0, 0)
        os.chmod(staging, 0o700)
        os.utime(staging, ns=(NORMALIZED_MTIME_NS, NORMALIZED_MTIME_NS))
        _clear_xattrs(staging)
        materialize_sources._fsync_workspace_directories(staging)  # noqa: SLF001

        profile_after = preflight(
            profile_path,
            source_manifest_path,
            toolchain_manifest_path,
            source_cache,
            toolchain_cache,
            source_workspace,
            apt_root,
            sdk_root,
            composition_receipt,
            announce=False,
        )
        inputs_after = _snapshot_preparation_inputs(
            profile_after,
            source_workspace,
            composition_receipt,
        )
        _assert_same_preparation_inputs(inputs_before, inputs_after)
        _receipt, receipt_raw = _verify_prepared_workspace(staging, inputs_after)

        _assert_pinned_output_parent(trusted_parent, parent_fd, parent_identity)
        _assert_absent_at(
            parent_fd,
            build_workspace.name,
            "native build workspace",
        )
        assert staging_identity is not None
        _assert_staging_identity_at(parent_fd, staging.name, staging_identity)
        _rename_workspace_no_replace_at(
            parent_fd,
            staging.name,
            build_workspace.name,
        )
        staging = None
        try:
            os.fsync(parent_fd)
            _assert_pinned_output_parent(trusted_parent, parent_fd, parent_identity)
        except (OSError, source_tool.SourceToolError) as error:
            raise source_tool.SourceToolError(
                f"native build workspace was published at {build_workspace}, but "
                "output-parent durability could not be confirmed; do not retry blindly. "
                "Verify that exact workspace, then deliberately keep or remove it: "
                f"{error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
        try:
            _verify_prepared_workspace(build_workspace, inputs_after)
        except source_tool.SourceToolError as error:
            raise source_tool.SourceToolError(
                f"native build workspace was published at {build_workspace}, but its "
                "post-publication verification failed; retain it for inspection and "
                f"deliberately verify or remove that exact tree: {error}",
                error.exit_code,
            ) from error
        print(
            "native build workspace prepared (build not executed): "
            f"workspace={build_workspace}; receipt={hashlib.sha256(receipt_raw).hexdigest()}"
        )
    except BaseException as error:
        cleanup_error = (
            _remove_preparation_staging(staging, staging_identity)
            if staging is not None
            else None
        )
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                "native build preparation failed and staging cleanup failed: "
                f"{cleanup_error}",
                source_tool.EXIT_INTERNAL,
            ) from error
        if isinstance(error, source_tool.SourceToolError):
            raise
        if isinstance(error, KeyboardInterrupt):
            raise
        if isinstance(error, Exception):
            raise source_tool.SourceToolError(
                f"native build preparation failed: {error}",
                source_tool.EXIT_INTEGRITY,
            ) from error
        raise
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def validate(
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
) -> LoadedProfile:
    profile = load_profile(profile_path, source_manifest_path, toolchain_manifest_path)
    print(
        f"native build profile valid: {profile.project['profile']}; "
        f"{len(profile.abis)} ABI(s), {len(profile.overlays)} overlay(s)"
    )
    return profile


def preflight(
    profile_path: Path,
    source_manifest_path: Path,
    toolchain_manifest_path: Path,
    source_cache: Path,
    toolchain_cache: Path,
    source_workspace: Path,
    apt_root: Path,
    sdk_root: Path,
    composition_receipt: Path,
    *,
    announce: bool = True,
) -> LoadedProfile:
    _require_linux_root("preflight")
    profile = load_profile(profile_path, source_manifest_path, toolchain_manifest_path)
    materialize_sources.verify_materialized_workspace(
        source_manifest_path,
        source_cache,
        source_workspace,
        require_preserve=True,
    )
    rootfs_tool._require_ext4(source_workspace)  # noqa: SLF001 - shared canonical FS gate
    _verify_overlay_origins(profile, source_workspace)
    _verify_runtime_libraries(profile, sdk_root)
    composition_tool.verify_composition(
        toolchain_manifest_path,
        source_manifest_path,
        toolchain_cache,
        apt_root,
        sdk_root,
        composition_receipt,
        announce=False,
    )
    if announce:
        print(
            f"native build input preflight complete: profile={profile.sha256}; "
            f"source={source_workspace}"
        )
    return profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--toolchain-manifest", type=Path, default=DEFAULT_TOOLCHAIN_MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate the locked native build profile")
    for name, help_text in (
        (
            "preflight",
            "verify the profile, preserve-mode source, and composed toolchain inputs",
        ),
        (
            "prepare",
            "publish a fresh writable source/output/HOME/tmp build workspace",
        ),
        (
            "verify-preparation",
            "verify an unmodified prepared workspace before a native build",
        ),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--source-cache", type=Path, default=DEFAULT_SOURCE_CACHE)
        command.add_argument("--toolchain-cache", type=Path, default=DEFAULT_TOOLCHAIN_CACHE)
        command.add_argument("--source-workspace", type=Path, default=DEFAULT_SOURCE_WORKSPACE)
        command.add_argument("--apt-root", type=Path, default=DEFAULT_APT_ROOT)
        command.add_argument("--sdk-root", type=Path, default=DEFAULT_SDK_ROOT)
        command.add_argument(
            "--composition-receipt",
            type=Path,
            default=DEFAULT_COMPOSITION_RECEIPT,
        )
        if name != "preflight":
            command.add_argument(
                "--build-workspace",
                type=Path,
                default=DEFAULT_BUILD_WORKSPACE,
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        profile_path = arguments.profile.resolve()
        source_manifest_path = arguments.source_manifest.resolve()
        toolchain_manifest_path = arguments.toolchain_manifest.resolve()
        if arguments.command == "validate":
            validate(profile_path, source_manifest_path, toolchain_manifest_path)
        elif arguments.command == "preflight":
            preflight(
                profile_path,
                source_manifest_path,
                toolchain_manifest_path,
                arguments.source_cache.resolve(),
                arguments.toolchain_cache.resolve(),
                Path(os.path.abspath(arguments.source_workspace)),
                Path(os.path.abspath(arguments.apt_root)),
                Path(os.path.abspath(arguments.sdk_root)),
                Path(os.path.abspath(arguments.composition_receipt)),
            )
        elif arguments.command == "prepare":
            prepare(
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
        elif arguments.command == "verify-preparation":
            verify_preparation(
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
        print(f"error: internal native build tool failure: {error}", file=sys.stderr)
        return source_tool.EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
