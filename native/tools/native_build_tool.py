#!/usr/bin/env python3
"""Validate the locked source-built libmpv execution profile and its inputs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import platform
import re
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

import composition_tool
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
DEFAULT_APT_ROOT = composition_tool.DEFAULT_APT_ROOT
DEFAULT_SDK_ROOT = composition_tool.DEFAULT_SDK_ROOT
DEFAULT_COMPOSITION_RECEIPT = composition_tool.DEFAULT_RECEIPT

MAX_PROFILE_BYTES = 1024 * 1024
MAX_RUNTIME_LIBRARY_BYTES = 32 * 1024 * 1024
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
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
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
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
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
    preflight_parser = commands.add_parser(
        "preflight",
        help="verify the profile, preserve-mode source, and composed toolchain inputs",
    )
    preflight_parser.add_argument("--source-cache", type=Path, default=DEFAULT_SOURCE_CACHE)
    preflight_parser.add_argument("--toolchain-cache", type=Path, default=DEFAULT_TOOLCHAIN_CACHE)
    preflight_parser.add_argument("--source-workspace", type=Path, default=DEFAULT_SOURCE_WORKSPACE)
    preflight_parser.add_argument("--apt-root", type=Path, default=DEFAULT_APT_ROOT)
    preflight_parser.add_argument("--sdk-root", type=Path, default=DEFAULT_SDK_ROOT)
    preflight_parser.add_argument(
        "--composition-receipt",
        type=Path,
        default=DEFAULT_COMPOSITION_RECEIPT,
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
