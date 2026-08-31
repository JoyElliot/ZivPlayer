# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate and prepare ZivPlayer's locked native toolchain root inputs."""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unicodedata
import urllib.parse
import urllib.request
import zlib
from collections import deque
from pathlib import Path
from typing import BinaryIO, NoReturn

if sys.version_info < (3, 11):
    raise SystemExit("toolchain_tool.py requires Python 3.11 or newer")

import source_tool


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NATIVE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = NATIVE_DIR / "toolchain-manifest.toml"
DEFAULT_SOURCE_MANIFEST = NATIVE_DIR / "source-manifest.toml"
DEFAULT_CACHE = NATIVE_DIR / "cache" / "toolchain"

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOKEN_BYTES = 1024 * 1024
MAX_APT_INDEX_BYTES = 256 * 1024 * 1024
MAX_APT_LOCK_BYTES = 2 * 1024 * 1024
MAX_ROOTFS_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024

TOP_LEVEL_KEYS = {
    "schemaVersion",
    "project",
    "policy",
    "baseImage",
    "apt",
    "compliance",
    "artifact",
    "ociObject",
}
PROJECT_KEYS = {
    "name",
    "sourceManifest",
    "sourceManifestSha256",
    "hostPlatform",
    "nativeApi",
    "abis",
    "pageSizeBytes",
    "environmentStatus",
}
POLICY_KEYS = {
    "networkAtBuild",
    "allowFloatingBuildReferences",
    "allowSdkManagerAtBuild",
    "allowAptRepositoriesAtBuild",
    "allowPipIndexAtBuild",
    "allowNonfree",
}
BASE_IMAGE_KEYS = {
    "reference",
    "buildReference",
    "repository",
    "platform",
    "indexDigest",
    "manifestDigest",
    "objectClosureStatus",
}
APT_KEYS = {
    "snapshot",
    "architecture",
    "suites",
    "components",
    "installMode",
    "noRecommends",
    "preparationAptVersion",
    "preparationDpkgVersion",
    "preparationGpgvVersion",
    "rootsFile",
    "rootsCount",
    "rootsFileSize",
    "rootsFileSha256",
    "sourcesTemplate",
    "sourcesTemplateSize",
    "sourcesTemplateSha256",
    "baseStatusMember",
    "baseStatusSize",
    "baseStatusSha256",
    "archiveKeyringMember",
    "archiveKeyringSize",
    "archiveKeyringSha256",
    "archiveSignerFingerprint",
    "baseDpkgLock",
    "baseDpkgPackageCount",
    "baseDpkgLockSize",
    "baseDpkgLockSha256",
    "indexLock",
    "indexCount",
    "indexLockSize",
    "indexLockSha256",
    "packageLock",
    "packageCount",
    "packageLockSize",
    "packageLockSha256",
    "installOrder",
    "installOrderCount",
    "installOrderSize",
    "installOrderSha256",
    "simulationSize",
    "simulationSha256",
    "closureStatus",
    "baseDpkgSnapshotStatus",
}
COMPLIANCE_KEYS = {
    "androidLicenseFilesStatus",
    "systemNoticesStatus",
    "retentionBundleStatus",
}
ARTIFACT_REQUIRED_KEYS = {
    "id",
    "kind",
    "version",
    "url",
    "archive",
    "size",
    "sha256",
    "installPath",
}
ARTIFACT_OPTIONAL_KEYS = {"publishedSha1"}
OCI_OBJECT_KEYS = {"id", "kind", "mediaType", "digest", "size", "archive"}

CANONICAL_ARTIFACT_IDS = (
    "android-build-tools",
    "android-cmdline-tools",
    "android-ndk",
    "android-platform",
    "meson-wheel",
)
CANONICAL_OCI_IDS = (
    "ubuntu-config-amd64",
    "ubuntu-index",
    "ubuntu-layer-amd64",
    "ubuntu-manifest-amd64",
)
EXPECTED_ARTIFACT_KINDS = {
    "android-build-tools": "android-archive",
    "android-cmdline-tools": "android-archive",
    "android-ndk": "android-archive",
    "android-platform": "android-archive",
    "meson-wheel": "python-wheel",
}
EXPECTED_OCI_KINDS = {
    "ubuntu-config-amd64": "config",
    "ubuntu-index": "index",
    "ubuntu-layer-amd64": "layer",
    "ubuntu-manifest-amd64": "manifest",
}
OCI_MEDIA_TYPES = {
    "config": "application/vnd.oci.image.config.v1+json",
    "index": "application/vnd.oci.image.index.v1+json",
    "layer": "application/vnd.oci.image.layer.v1.tar+gzip",
    "manifest": "application/vnd.oci.image.manifest.v1+json",
}
TOOLCHAIN_ENVIRONMENT_STATUSES = {
    "roots-locked-closure-pending",
    "roots-and-apt-locked-container-pending",
    "release-inputs-locked",
}
COMPLETENESS_STATUSES = {"pending", "complete"}
APT_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
APT_ARCHIVE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9%+._~-]*\.deb$")
DEBIAN_VERSION_RE = re.compile(r"^[0-9A-Za-z.+:~-]+$")
DEBIAN_UPSTREAM_RE = re.compile(r"^[0-9][0-9A-Za-z.+:~-]*$")
DEBIAN_REVISION_RE = re.compile(r"^[0-9A-Za-z+.~]+$")
APT_BASE_HEADER = ("package", "architecture", "version")
APT_INDEX_HEADER = (
    "archive",
    "role",
    "suite",
    "component",
    "size",
    "sha256",
    "publishedSha256",
    "signerFingerprint",
)
APT_PACKAGE_HEADER = (
    "package",
    "architecture",
    "version",
    "archive",
    "size",
    "sha256",
    "suites",
    "components",
    "repositoryFilename",
    "publishedSha256",
)
APT_INSTALL_ORDER_HEADER = (
    "sequence",
    "package",
    "architecture",
    "version",
    "archive",
    "sha256",
)


def _schema(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_SCHEMA)


def _expect_table(value: object, location: str) -> dict[str, object]:
    return source_tool._expect_table(value, location)


def _expect_string(value: object, location: str) -> str:
    result = source_tool._expect_string(value, location)
    if result != result.strip() or any(ord(character) < 0x20 for character in result):
        _schema(f"{location} must not contain surrounding whitespace or control characters")
    return result


def _expect_int(value: object, location: str, *, minimum: int = 0) -> int:
    return source_tool._expect_int(value, location, minimum=minimum)


def _expect_bool(value: object, location: str) -> bool:
    return source_tool._expect_bool(value, location)


def _expect_string_list(
    value: object,
    location: str,
    *,
    allow_empty: bool,
) -> list[str]:
    result = source_tool._expect_string_list(
        value,
        location,
        allow_empty=allow_empty,
    )
    for index, item in enumerate(result):
        _expect_string(item, f"{location}[{index}]")
    return result


def _check_exact_keys(
    table: dict[str, object],
    location: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    source_tool._check_exact_keys(table, location, required, optional)


def _check_safe_path(value: str, location: str) -> None:
    if unicodedata.normalize("NFKC", value) != value:
        _schema(f"{location} must already be in NFKC form")
    source_tool._check_safe_relative_path(value, location)


def _check_portable_archive_name(value: str, location: str) -> None:
    if (
        len(value) > 200
        or not value
        or not value[0].isalnum()
        or not value.isascii()
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-" for character in value)
        or value.endswith((".", " "))
        or value.split(".", 1)[0].upper() in source_tool.WINDOWS_RESERVED_NAMES
    ):
        _schema(f"{location} must be a portable ASCII cache basename")


def _normalized_path_key(value: str) -> str:
    return "/".join(
        unicodedata.normalize("NFKC", part).casefold()
        for part in value.split("/")
    )


def _check_unique_paths(values: list[str], location: str) -> None:
    normalized = [_normalized_path_key(value) for value in values]
    if len(normalized) != len(set(normalized)):
        _schema(f"{location} must be unique after NFKC and case folding")
    sorted_values = sorted(values, key=_normalized_path_key)
    for index, left in enumerate(sorted_values):
        left_key = _normalized_path_key(left)
        for right in sorted_values[index + 1 :]:
            right_key = _normalized_path_key(right)
            if right_key.startswith(left_key + "/"):
                _schema(f"{location} must not contain parent/child output collisions")


def _check_sha256(value: str, location: str) -> str:
    if not source_tool.SHA256_RE.fullmatch(value):
        _schema(f"{location} must be a lowercase 64-hex SHA-256 digest")
    return value


def _check_oci_digest(value: str, location: str) -> str:
    if not value.startswith("sha256:"):
        _schema(f"{location} must use a sha256: digest")
    _check_sha256(value.removeprefix("sha256:"), location)
    return value


def _validate_project(value: object) -> dict[str, object]:
    project = _expect_table(value, "project")
    _check_exact_keys(project, "project", PROJECT_KEYS)
    if _expect_string(project["name"], "project.name") != "ZivPlayer":
        _schema("project.name must be ZivPlayer")
    if (
        _expect_string(project["sourceManifest"], "project.sourceManifest")
        != "native/source-manifest.toml"
    ):
        _schema("project.sourceManifest must be native/source-manifest.toml")
    _check_sha256(
        _expect_string(project["sourceManifestSha256"], "project.sourceManifestSha256"),
        "project.sourceManifestSha256",
    )
    if _expect_string(project["hostPlatform"], "project.hostPlatform") != "linux/amd64":
        _schema("project.hostPlatform must be linux/amd64")
    if _expect_int(project["nativeApi"], "project.nativeApi", minimum=1) != 26:
        _schema("project.nativeApi must be 26")
    if _expect_string_list(project["abis"], "project.abis", allow_empty=False) != [
        "arm64-v8a",
        "x86_64",
    ]:
        _schema("project.abis must be ordered as arm64-v8a, x86_64")
    if _expect_int(project["pageSizeBytes"], "project.pageSizeBytes", minimum=1) != 16384:
        _schema("project.pageSizeBytes must be 16384")
    status = _expect_string(project["environmentStatus"], "project.environmentStatus")
    if status not in TOOLCHAIN_ENVIRONMENT_STATUSES:
        _schema("project.environmentStatus is not a recognized fail-closed state")
    return project


def _validate_policy(value: object) -> dict[str, object]:
    policy = _expect_table(value, "policy")
    _check_exact_keys(policy, "policy", POLICY_KEYS)
    if _expect_string(policy["networkAtBuild"], "policy.networkAtBuild") != "none":
        _schema("policy.networkAtBuild must be none")
    for key in (
        "allowFloatingBuildReferences",
        "allowSdkManagerAtBuild",
        "allowAptRepositoriesAtBuild",
        "allowPipIndexAtBuild",
        "allowNonfree",
    ):
        if _expect_bool(policy[key], f"policy.{key}"):
            _schema(f"policy.{key} must be false")
    return policy


def _validate_base_image(value: object) -> dict[str, object]:
    base = _expect_table(value, "baseImage")
    _check_exact_keys(base, "baseImage", BASE_IMAGE_KEYS)
    reference = _expect_string(base["reference"], "baseImage.reference")
    if reference != "docker.io/library/ubuntu:noble-20260810":
        _schema("baseImage.reference must identify the selected descriptive Ubuntu tag")
    if _expect_string(base["repository"], "baseImage.repository") != "library/ubuntu":
        _schema("baseImage.repository must be library/ubuntu")
    if _expect_string(base["platform"], "baseImage.platform") != "linux/amd64":
        _schema("baseImage.platform must be linux/amd64")
    _check_oci_digest(
        _expect_string(base["indexDigest"], "baseImage.indexDigest"),
        "baseImage.indexDigest",
    )
    _check_oci_digest(
        _expect_string(base["manifestDigest"], "baseImage.manifestDigest"),
        "baseImage.manifestDigest",
    )
    expected_build_reference = (
        "docker.io/library/ubuntu@" + str(base["manifestDigest"])
    )
    if (
        _expect_string(base["buildReference"], "baseImage.buildReference")
        != expected_build_reference
    ):
        _schema("baseImage.buildReference must be digest-qualified with manifestDigest")
    closure_status = _expect_string(
        base["objectClosureStatus"],
        "baseImage.objectClosureStatus",
    )
    if closure_status not in COMPLETENESS_STATUSES:
        _schema("baseImage.objectClosureStatus must be pending or complete")
    return base


def _validate_apt(value: object) -> dict[str, object]:
    apt = _expect_table(value, "apt")
    _check_exact_keys(apt, "apt", APT_KEYS)
    snapshot = _expect_string(apt["snapshot"], "apt.snapshot")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", snapshot):
        _schema("apt.snapshot must use YYYYMMDDTHHMMSSZ")
    try:
        parsed_snapshot = datetime.datetime.strptime(snapshot, "%Y%m%dT%H%M%SZ")
    except ValueError as error:
        _schema(f"apt.snapshot is not a real UTC timestamp: {error}")
    if parsed_snapshot.strftime("%Y%m%dT%H%M%SZ") != snapshot:
        _schema("apt.snapshot must use canonical zero-padded UTC fields")
    if snapshot != "20260811T000000Z":
        _schema("apt.snapshot must be the locked 20260811T000000Z snapshot")
    if _expect_string(apt["architecture"], "apt.architecture") != "amd64":
        _schema("apt.architecture must be amd64")
    if _expect_string_list(apt["suites"], "apt.suites", allow_empty=False) != [
        "noble",
        "noble-updates",
        "noble-security",
    ]:
        _schema("apt.suites must lock noble, noble-updates, noble-security")
    if _expect_string_list(apt["components"], "apt.components", allow_empty=False) != [
        "main",
        "universe",
    ]:
        _schema("apt.components must lock main and universe")
    if _expect_string(apt["installMode"], "apt.installMode") != "offline-deb":
        _schema("apt.installMode must be offline-deb")
    if not _expect_bool(apt["noRecommends"], "apt.noRecommends"):
        _schema("apt.noRecommends must be true")
    expected_versions = {
        "preparationAptVersion": "2.8.3",
        "preparationDpkgVersion": "1.22.6ubuntu6.6",
        "preparationGpgvVersion": "2.4.4-2ubuntu17.4",
    }
    for key, expected in expected_versions.items():
        if _expect_string(apt[key], f"apt.{key}") != expected:
            _schema(f"apt.{key} must be {expected}")
    expected_paths = {
        "rootsFile": "native/toolchain/apt-roots.txt",
        "sourcesTemplate": "native/toolchain/ubuntu.sources.in",
        "baseDpkgLock": "native/toolchain/base-dpkg.tsv",
        "indexLock": "native/toolchain/apt-indices.tsv",
        "packageLock": "native/toolchain/apt-packages.tsv",
        "installOrder": "native/toolchain/apt-install-order.tsv",
    }
    for key, expected in expected_paths.items():
        path = _expect_string(apt[key], f"apt.{key}")
        _check_safe_path(path, f"apt.{key}")
        if path != expected:
            _schema(f"apt.{key} must be {expected}")
    expected_members = {
        "baseStatusMember": "var/lib/dpkg/status",
        "archiveKeyringMember": "usr/share/keyrings/ubuntu-archive-keyring.gpg",
    }
    for key, expected in expected_members.items():
        member = _expect_string(apt[key], f"apt.{key}")
        _check_safe_path(member, f"apt.{key}")
        if member != expected:
            _schema(f"apt.{key} must be {expected}")
    for key in (
        "rootsFile",
        "sourcesTemplate",
        "baseStatus",
        "archiveKeyring",
        "baseDpkgLock",
        "indexLock",
        "packageLock",
        "installOrder",
    ):
        size = _expect_int(apt[f"{key}Size"], f"apt.{key}Size", minimum=1)
        if size > MAX_APT_LOCK_BYTES and key not in {"baseStatus"}:
            _schema(f"apt.{key}Size exceeds the {MAX_APT_LOCK_BYTES}-byte lock limit")
        _check_sha256(
            _expect_string(apt[f"{key}Sha256"], f"apt.{key}Sha256"),
            f"apt.{key}Sha256",
        )
    expected_counts = {
        "rootsCount": 23,
        "baseDpkgPackageCount": 92,
        "indexCount": 9,
        "packageCount": 102,
        "installOrderCount": 102,
    }
    for key, expected in expected_counts.items():
        if _expect_int(apt[key], f"apt.{key}", minimum=1) != expected:
            _schema(f"apt.{key} must be {expected}")
    if apt["installOrderCount"] != apt["packageCount"]:
        _schema("apt.installOrderCount must match apt.packageCount")
    simulation_size = _expect_int(
        apt["simulationSize"],
        "apt.simulationSize",
        minimum=1,
    )
    if simulation_size > MAX_APT_LOCK_BYTES:
        _schema(f"apt.simulationSize exceeds the {MAX_APT_LOCK_BYTES}-byte lock limit")
    _check_sha256(
        _expect_string(apt["simulationSha256"], "apt.simulationSha256"),
        "apt.simulationSha256",
    )
    signer = _expect_string(
        apt["archiveSignerFingerprint"],
        "apt.archiveSignerFingerprint",
    )
    if not re.fullmatch(r"[0-9a-f]{40}", signer):
        _schema("apt.archiveSignerFingerprint must be lowercase 40-hex")
    for key in ("closureStatus", "baseDpkgSnapshotStatus"):
        status = _expect_string(apt[key], f"apt.{key}")
        if status not in COMPLETENESS_STATUSES:
            _schema(f"apt.{key} must be pending or complete")
    return apt


def _validate_compliance(value: object) -> dict[str, object]:
    compliance = _expect_table(value, "compliance")
    _check_exact_keys(compliance, "compliance", COMPLIANCE_KEYS)
    for key in sorted(COMPLIANCE_KEYS):
        status = _expect_string(compliance[key], f"compliance.{key}")
        if status not in COMPLETENESS_STATUSES:
            _schema(f"compliance.{key} must be pending or complete")
    return compliance


def _validate_artifact(value: object, index: int) -> dict[str, object]:
    location = f"artifact[{index}]"
    artifact = _expect_table(value, location)
    _check_exact_keys(
        artifact,
        location,
        ARTIFACT_REQUIRED_KEYS,
        ARTIFACT_OPTIONAL_KEYS,
    )
    artifact_id = _expect_string(artifact["id"], f"{location}.id")
    source_tool._check_portable_name(artifact_id, f"{location}.id")
    kind = _expect_string(artifact["kind"], f"{location}.kind")
    if EXPECTED_ARTIFACT_KINDS.get(artifact_id) != kind:
        _schema(f"{location}.kind does not match the canonical artifact role")
    version = _expect_string(artifact["version"], f"{location}.version")
    if not source_tool.VERSION_RE.fullmatch(version):
        _schema(f"{location}.version must be an immutable ASCII version label")
    url = _expect_string(artifact["url"], f"{location}.url")
    source_tool._validate_https_url(
        url,
        f"{location}.url",
        allow_query=False,
        exit_code=source_tool.EXIT_SCHEMA,
    )
    archive = _expect_string(artifact["archive"], f"{location}.archive")
    _check_portable_archive_name(archive, f"{location}.archive")
    size = _expect_int(artifact["size"], f"{location}.size", minimum=1)
    if size > MAX_INPUT_BYTES:
        _schema(f"{location}.size exceeds the {MAX_INPUT_BYTES}-byte input limit")
    _check_sha256(
        _expect_string(artifact["sha256"], f"{location}.sha256"),
        f"{location}.sha256",
    )
    if "publishedSha1" in artifact:
        published_sha1 = _expect_string(
            artifact["publishedSha1"],
            f"{location}.publishedSha1",
        )
        if len(published_sha1) != 40 or any(
            character not in "0123456789abcdef" for character in published_sha1
        ):
            _schema(f"{location}.publishedSha1 must be lowercase 40-hex")
    elif kind == "android-archive":
        _schema(f"{location}.publishedSha1 is required for Android archives")
    install_path = _expect_string(artifact["installPath"], f"{location}.installPath")
    _check_safe_path(install_path, f"{location}.installPath")
    expected_root = "android-sdk/" if kind == "android-archive" else "python-wheels/"
    if not install_path.startswith(expected_root):
        _schema(f"{location}.installPath must stay under {expected_root.rstrip('/')}")
    return artifact


def _validate_oci_object(value: object, index: int) -> dict[str, object]:
    location = f"ociObject[{index}]"
    entry = _expect_table(value, location)
    _check_exact_keys(entry, location, OCI_OBJECT_KEYS)
    object_id = _expect_string(entry["id"], f"{location}.id")
    source_tool._check_portable_name(object_id, f"{location}.id")
    kind = _expect_string(entry["kind"], f"{location}.kind")
    if EXPECTED_OCI_KINDS.get(object_id) != kind:
        _schema(f"{location}.kind does not match the canonical OCI object role")
    if _expect_string(entry["mediaType"], f"{location}.mediaType") != OCI_MEDIA_TYPES[kind]:
        _schema(f"{location}.mediaType does not match {kind}")
    _check_oci_digest(
        _expect_string(entry["digest"], f"{location}.digest"),
        f"{location}.digest",
    )
    size = _expect_int(entry["size"], f"{location}.size", minimum=1)
    if size > MAX_INPUT_BYTES:
        _schema(f"{location}.size exceeds the {MAX_INPUT_BYTES}-byte input limit")
    archive = _expect_string(entry["archive"], f"{location}.archive")
    _check_portable_archive_name(archive, f"{location}.archive")
    return entry


def validate_manifest_data(
    data: object,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    manifest = _expect_table(data, "manifest")
    _check_exact_keys(manifest, "manifest", TOP_LEVEL_KEYS)
    if _expect_int(manifest["schemaVersion"], "schemaVersion", minimum=1) != 1:
        _schema("schemaVersion must be 1")
    project = _validate_project(manifest["project"])
    _validate_policy(manifest["policy"])
    base = _validate_base_image(manifest["baseImage"])
    apt = _validate_apt(manifest["apt"])
    _validate_compliance(manifest["compliance"])
    apt_complete = (
        apt["closureStatus"] == "complete"
        and apt["baseDpkgSnapshotStatus"] == "complete"
    )
    project_claims_apt = project["environmentStatus"] in {
        "roots-and-apt-locked-container-pending",
        "release-inputs-locked",
    }
    if apt_complete != project_claims_apt:
        _schema("project.environmentStatus and APT completeness statuses must agree")

    raw_artifacts = manifest["artifact"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        _schema("artifact must be a non-empty array of tables")
    artifacts = [
        _validate_artifact(value, index)
        for index, value in enumerate(raw_artifacts)
    ]
    artifact_ids = [str(artifact["id"]) for artifact in artifacts]
    if tuple(artifact_ids) != CANONICAL_ARTIFACT_IDS:
        _schema(
            "artifact entries must be the canonical ordered roots: "
            + ", ".join(CANONICAL_ARTIFACT_IDS)
        )

    raw_oci_objects = manifest["ociObject"]
    if not isinstance(raw_oci_objects, list) or not raw_oci_objects:
        _schema("ociObject must be a non-empty array of tables")
    oci_objects = [
        _validate_oci_object(value, index)
        for index, value in enumerate(raw_oci_objects)
    ]
    oci_ids = [str(entry["id"]) for entry in oci_objects]
    if tuple(oci_ids) != CANONICAL_OCI_IDS:
        _schema(
            "ociObject entries must be the canonical ordered closure: "
            + ", ".join(CANONICAL_OCI_IDS)
        )

    archives = [str(entry["archive"]) for entry in artifacts + oci_objects]
    _check_unique_paths(archives, "artifact and OCI cache archive names")
    install_paths = [str(artifact["installPath"]) for artifact in artifacts]
    _check_unique_paths(install_paths, "artifact install paths")

    oci_by_id = {str(entry["id"]): entry for entry in oci_objects}
    if base["indexDigest"] != oci_by_id["ubuntu-index"]["digest"]:
        _schema("baseImage.indexDigest must match the locked OCI index object")
    if base["manifestDigest"] != oci_by_id["ubuntu-manifest-amd64"]["digest"]:
        _schema("baseImage.manifestDigest must match the locked amd64 manifest object")
    return project, artifacts, oci_objects


def _is_reparse(stat_result: os.stat_result) -> bool:
    return source_tool._is_windows_reparse(stat_result)


def _read_stable_bytes(
    path: Path,
    *,
    maximum: int,
    label: str,
    missing_exit: int,
) -> bytes:
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        path_stat = os.lstat(path)
        if not stat.S_ISREG(path_stat.st_mode) or _is_reparse(path_stat):
            raise source_tool.SourceToolError(
                f"{label} must be a regular non-symlink file: {path}",
                source_tool.EXIT_INTEGRITY,
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        current_stat = os.lstat(path)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or _is_reparse(opened_stat)
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (current_stat.st_dev, current_stat.st_ino)
            or _is_reparse(current_stat)
        ):
            raise source_tool.SourceToolError(
                f"{label} changed while it was being opened: {path}",
                source_tool.EXIT_INTEGRITY,
            )
        if opened_stat.st_size > maximum:
            raise source_tool.SourceToolError(
                f"{label} exceeds {maximum} bytes: {path}",
                source_tool.EXIT_INTEGRITY,
            )
        stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise source_tool.SourceToolError(
                f"{label} exceeds {maximum} bytes: {path}",
                source_tool.EXIT_INTEGRITY,
            )
        final_fd_stat = os.fstat(stream.fileno())
        final_path_stat = os.lstat(path)
        if (
            len(raw) != opened_stat.st_size
            or (final_fd_stat.st_dev, final_fd_stat.st_ino, final_fd_stat.st_size)
            != (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_size)
            or (final_path_stat.st_dev, final_path_stat.st_ino, final_path_stat.st_size)
            != (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_size)
            or _is_reparse(final_path_stat)
        ):
            raise source_tool.SourceToolError(
                f"{label} changed while it was being read: {path}",
                source_tool.EXIT_INTEGRITY,
            )
        return raw
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"{label} not found: {path}",
            missing_exit,
        ) from error
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        raise source_tool.SourceToolError(
            f"cannot read {label} {path}: {error}",
            source_tool.EXIT_INTEGRITY,
        ) from error
    finally:
        if stream is not None:
            stream.close()
        if descriptor >= 0:
            os.close(descriptor)


def _integrity(message: str) -> NoReturn:
    raise source_tool.SourceToolError(message, source_tool.EXIT_INTEGRITY)


def _read_locked_repository_file(
    apt: dict[str, object],
    key: str,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> bytes:
    relative = str(apt[key])
    path = repository_root / relative
    size = int(apt[f"{key}Size"])
    raw = _read_stable_bytes(
        path,
        maximum=size,
        label=f"APT {key}",
        missing_exit=source_tool.EXIT_MISSING,
    )
    if len(raw) != size:
        _integrity(f"APT {key} size mismatch: expected {size}, got {len(raw)}")
    actual = hashlib.sha256(raw).hexdigest()
    expected = str(apt[f"{key}Sha256"])
    if actual != expected:
        _integrity(f"APT {key} SHA-256 mismatch: expected {expected}, got {actual}")
    return raw


def _parse_tsv(
    raw: bytes,
    expected_header: tuple[str, ...],
    label: str,
) -> list[tuple[str, ...]]:
    if b"\r" in raw or not raw.endswith(b"\n") or b"\x00" in raw:
        _integrity(f"{label} must use canonical LF-terminated UTF-8 TSV bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        _integrity(f"cannot decode {label} as UTF-8: {error}")
    lines = text[:-1].split("\n")
    if not lines or tuple(lines[0].split("\t")) != expected_header:
        _integrity(f"{label} has an unexpected TSV header")
    rows: list[tuple[str, ...]] = []
    for line_number, line in enumerate(lines[1:], start=2):
        values = tuple(line.split("\t"))
        if len(values) != len(expected_header) or any(
            not value or any(ord(character) < 0x20 for character in value)
            for value in values
        ):
            _integrity(f"{label}:{line_number} is not a canonical TSV row")
        rows.append(values)
    return rows


def _parse_ascii_decimal(value: str, location: str, *, maximum_digits: int = 20) -> int:
    if (
        not value
        or not value.isascii()
        or any(not _is_ascii_digit(character) for character in value)
        or len(value) > maximum_digits
    ):
        _integrity(f"{location} is not a bounded ASCII decimal")
    try:
        return int(value)
    except ValueError as error:
        _integrity(f"cannot parse {location}: {error}")


def _parse_debian_control(raw: bytes, label: str) -> list[dict[str, str]]:
    if b"\r" in raw or b"\x00" in raw:
        _integrity(f"{label} must use LF-only Debian control bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        _integrity(f"cannot decode {label} as UTF-8: {error}")
    records: list[dict[str, str]] = []
    fields: dict[str, str] = {}
    current: str | None = None
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line:
            if fields:
                records.append(fields)
                fields = {}
            current = None
            continue
        if line.startswith((" ", "\t")):
            if current is None:
                _integrity(f"{label}:{line_number} has an orphan continuation line")
            separator = "" if not fields[current] else "\n"
            fields[current] += separator + line[1:]
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]*):(.*)", line)
        if match is None:
            _integrity(f"{label}:{line_number} has an invalid field line")
        name = match.group(1)
        if name in fields:
            _integrity(f"{label}:{line_number} repeats field {name}")
        value = match.group(2)
        if value.startswith(" "):
            value = value[1:]
        elif value.startswith("\t"):
            value = value[1:]
        fields[name] = value
        current = name
    if fields:
        records.append(fields)
    return records


def _validate_debian_identity(
    package: str,
    architecture: str,
    version: str,
    location: str,
) -> None:
    if not APT_PACKAGE_RE.fullmatch(package):
        _integrity(f"{location} has an invalid Debian package name: {package}")
    if architecture not in {"all", "amd64"}:
        _integrity(f"{location} has an unexpected architecture: {architecture}")
    try:
        _debian_version_parts(version)
    except source_tool.SourceToolError:
        _integrity(f"{location} has an invalid Debian version: {version}")


def _canonical_base_dpkg(records: list[dict[str, str]]) -> bytes:
    rows: list[tuple[str, str, str]] = []
    for index, record in enumerate(records):
        required = ("Package", "Architecture", "Version", "Status")
        if any(key not in record for key in required):
            _integrity(f"base dpkg status record {index} is missing identity fields")
        if record["Status"] != "install ok installed":
            _integrity(
                f"base dpkg status contains a non-installed record: {record['Package']}"
            )
        row = (record["Package"], record["Architecture"], record["Version"])
        _validate_debian_identity(*row, f"base dpkg status record {index}")
        rows.append(row)
    rows.sort()
    if len(rows) != len(set(rows)):
        _integrity("base dpkg status contains duplicate package identities")
    text = "\t".join(APT_BASE_HEADER) + "\n"
    text += "".join("\t".join(row) + "\n" for row in rows)
    return text.encode("utf-8")


def _debian_version_parts(version: str) -> tuple[int, str, str]:
    if not version or not version.isascii() or not DEBIAN_VERSION_RE.fullmatch(version):
        _integrity(f"invalid Debian version in dependency metadata: {version}")
    if ":" in version:
        if version.count(":") != 1:
            _integrity(f"invalid Debian version epoch: {version}")
        epoch_text, remainder = version.split(":", 1)
        if (
            not epoch_text
            or len(epoch_text) > 18
            or any(not _is_ascii_digit(value) for value in epoch_text)
            or not remainder
        ):
            _integrity(f"invalid Debian version epoch: {version}")
        try:
            epoch = int(epoch_text)
        except ValueError as error:
            _integrity(f"invalid Debian version epoch: {version}: {error}")
    else:
        epoch = 0
        remainder = version
    if "-" in remainder:
        upstream, revision = remainder.rsplit("-", 1)
        if not DEBIAN_UPSTREAM_RE.fullmatch(upstream) or not DEBIAN_REVISION_RE.fullmatch(
            revision
        ):
            _integrity(f"invalid Debian version revision: {version}")
    else:
        upstream = remainder
        if not DEBIAN_UPSTREAM_RE.fullmatch(upstream) or ":" in upstream or "-" in upstream:
            _integrity(f"invalid Debian upstream version: {version}")
        revision = "0"
    return epoch, upstream, revision


def _is_ascii_digit(character: str) -> bool:
    return "0" <= character <= "9"


def _is_ascii_letter(character: str) -> bool:
    return "A" <= character <= "Z" or "a" <= character <= "z"


def _debian_order(character: str) -> int:
    if character == "~":
        return -1
    if not character:
        return 0
    if _is_ascii_letter(character):
        return ord(character)
    return ord(character) + 256


def _compare_debian_part(left: str, right: str) -> int:
    left_index = 0
    right_index = 0
    while left_index < len(left) or right_index < len(right):
        while (
            (left_index < len(left) and not _is_ascii_digit(left[left_index]))
            or (right_index < len(right) and not _is_ascii_digit(right[right_index]))
        ):
            left_character = (
                left[left_index]
                if left_index < len(left) and not _is_ascii_digit(left[left_index])
                else ""
            )
            right_character = (
                right[right_index]
                if right_index < len(right) and not _is_ascii_digit(right[right_index])
                else ""
            )
            left_order = _debian_order(left_character)
            right_order = _debian_order(right_character)
            if left_order != right_order:
                return -1 if left_order < right_order else 1
            if left_character:
                left_index += 1
            if right_character:
                right_index += 1
        left_zero = left_index
        while left_zero < len(left) and left[left_zero] == "0":
            left_zero += 1
        right_zero = right_index
        while right_zero < len(right) and right[right_zero] == "0":
            right_zero += 1
        left_end = left_zero
        while left_end < len(left) and _is_ascii_digit(left[left_end]):
            left_end += 1
        right_end = right_zero
        while right_end < len(right) and _is_ascii_digit(right[right_end]):
            right_end += 1
        left_digits = left[left_zero:left_end]
        right_digits = right[right_zero:right_end]
        if len(left_digits) != len(right_digits):
            return -1 if len(left_digits) < len(right_digits) else 1
        if left_digits != right_digits:
            return -1 if left_digits < right_digits else 1
        left_index = left_end
        right_index = right_end
    return 0


def _compare_debian_versions(left: str, right: str) -> int:
    left_epoch, left_upstream, left_revision = _debian_version_parts(left)
    right_epoch, right_upstream, right_revision = _debian_version_parts(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    upstream = _compare_debian_part(left_upstream, right_upstream)
    if upstream:
        return upstream
    return _compare_debian_part(left_revision, right_revision)


def _version_satisfies(actual: str | None, operator: str | None, expected: str | None) -> bool:
    if operator is None:
        return True
    if actual is None or expected is None:
        return False
    comparison = _compare_debian_versions(actual, expected)
    return {
        "<<": comparison < 0,
        "<=": comparison <= 0,
        "=": comparison == 0,
        ">=": comparison >= 0,
        ">>": comparison > 0,
    }[operator]


DEPENDENCY_ATOM_RE = re.compile(
    r"^([a-z0-9][a-z0-9+.-]*)(?::(any|native|amd64))?"
    r"(?:\s*\((<<|<=|=|>=|>>)\s+([^()\s]+)\))?$"
)


def _parse_relationships(
    value: str,
    location: str,
) -> list[list[tuple[str, str | None, str | None, str | None]]]:
    if not value:
        return []
    result: list[list[tuple[str, str | None, str | None, str | None]]] = []
    for clause_index, clause in enumerate(value.split(",")):
        alternatives: list[tuple[str, str | None, str | None, str | None]] = []
        for atom_index, atom_text in enumerate(clause.split("|")):
            atom = atom_text.strip()
            match = DEPENDENCY_ATOM_RE.fullmatch(atom)
            if match is None:
                _integrity(
                    f"{location} has unsupported dependency syntax at "
                    f"clause {clause_index}, atom {atom_index}: {atom}"
                )
            name, qualifier, operator, version = match.groups()
            if version is not None:
                _debian_version_parts(version)
            alternatives.append((name, qualifier, operator, version))
        if not alternatives:
            _integrity(f"{location} contains an empty dependency clause")
        result.append(alternatives)
    return result


def _parse_provides(
    value: str,
    location: str,
) -> list[tuple[str, str | None]]:
    provided: list[tuple[str, str | None]] = []
    for clause in _parse_relationships(value, location):
        if len(clause) != 1:
            _integrity(f"{location} must not contain alternative Provides entries")
        name, qualifier, operator, version = clause[0]
        if qualifier is not None or operator not in {None, "="}:
            _integrity(f"{location} has unsupported Provides syntax")
        provided.append((name, version))
    return provided


def _parse_toml(raw: bytes, path: Path) -> dict[str, object]:
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        raise source_tool.SourceToolError(
            f"cannot parse TOML manifest {path}: {error}",
            source_tool.EXIT_SCHEMA,
        ) from error
    return _expect_table(data, "manifest")


def _validate_bound_source_manifest(raw: bytes, expected_digest: str) -> dict[str, object]:
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != expected_digest:
        raise source_tool.SourceToolError(
            "source manifest SHA-256 does not match toolchain binding: "
            f"expected {expected_digest}, got {actual_digest}",
            source_tool.EXIT_INTEGRITY,
        )
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        raise source_tool.SourceToolError(
            f"cannot parse bound source manifest: {error}",
            source_tool.EXIT_INTEGRITY,
        ) from error
    sources = source_tool.validate_manifest_data(data)
    source_ids = tuple(str(source["id"]) for source in sources)
    if source_ids != source_tool.CANONICAL_SOURCE_IDS:
        raise source_tool.SourceToolError(
            "bound source manifest does not contain the canonical 23-input closure",
            source_tool.EXIT_INTEGRITY,
        )
    return data


def _validate_source_binding_tuple(
    project: dict[str, object],
    artifacts: list[dict[str, object]],
    source_data: dict[str, object],
) -> None:
    source_project = _expect_table(source_data["project"], "bound source project")
    source_toolchain = _expect_table(source_data["toolchain"], "bound source toolchain")
    expected_project = (
        int(project["nativeApi"]),
        list(project["abis"]),
        int(project["pageSizeBytes"]),
    )
    actual_project = (
        source_project.get("nativeApi"),
        source_project.get("abis"),
        source_project.get("pageSizeBytes"),
    )
    if actual_project != expected_project:
        raise source_tool.SourceToolError(
            "toolchain and source manifests disagree on native API, ABIs, or page size",
            source_tool.EXIT_INTEGRITY,
        )
    artifact_versions = {
        str(artifact["id"]): str(artifact["version"])
        for artifact in artifacts
    }
    expected_toolchain = (
        "linux",
        artifact_versions["android-ndk"],
        36,
        artifact_versions["android-build-tools"],
        17,
    )
    actual_toolchain = (
        source_toolchain.get("hostOs"),
        source_toolchain.get("ndkVersion"),
        source_toolchain.get("sdkPlatform"),
        source_toolchain.get("sdkBuildTools"),
        source_toolchain.get("jdkMajor"),
    )
    if actual_toolchain != expected_toolchain:
        raise source_tool.SourceToolError(
            "toolchain roots disagree with the bound source-manifest toolchain tuple",
            source_tool.EXIT_INTEGRITY,
        )


def load_manifest_snapshot(
    path: Path,
    *,
    source_manifest_path: Path | None = None,
) -> tuple[
    dict[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
    str,
]:
    raw = _read_stable_bytes(
        path,
        maximum=MAX_MANIFEST_BYTES,
        label="toolchain manifest",
        missing_exit=source_tool.EXIT_SCHEMA,
    )
    data = _parse_toml(raw, path)
    project, artifacts, oci_objects = validate_manifest_data(data)
    source_path = source_manifest_path or DEFAULT_SOURCE_MANIFEST
    source_raw = _read_stable_bytes(
        source_path,
        maximum=MAX_MANIFEST_BYTES,
        label="bound source manifest",
        missing_exit=source_tool.EXIT_MISSING,
    )
    source_data = _validate_bound_source_manifest(
        source_raw,
        str(project["sourceManifestSha256"]),
    )
    _validate_source_binding_tuple(project, artifacts, source_data)
    return data, artifacts, oci_objects, source_data, hashlib.sha256(raw).hexdigest()


def load_manifest(
    path: Path,
    *,
    source_manifest_path: Path | None = None,
) -> tuple[
    dict[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
]:
    data, artifacts, oci_objects, source_data, _digest = load_manifest_snapshot(
        path,
        source_manifest_path=source_manifest_path,
    )
    return data, artifacts, oci_objects, source_data


def _cache_entries(
    artifacts: list[dict[str, object]],
    oci_objects: list[dict[str, object]],
) -> list[dict[str, object]]:
    result = list(artifacts)
    for entry in oci_objects:
        result.append(
            {
                "id": entry["id"],
                "archive": entry["archive"],
                "size": entry["size"],
                "sha256": str(entry["digest"]).removeprefix("sha256:"),
            }
        )
    return result


def _validate_cache_root(cache: Path, *, create: bool) -> None:
    try:
        if create:
            cache.mkdir(parents=True, exist_ok=True)
        if cache.exists() or cache.is_symlink():
            cache_stat = cache.lstat()
            if (
                not stat.S_ISDIR(cache_stat.st_mode)
                or cache.is_symlink()
                or _is_reparse(cache_stat)
            ):
                raise source_tool.SourceToolError(
                    f"toolchain cache root must be a real directory: {cache}",
                    source_tool.EXIT_INTEGRITY,
                )
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        exit_code = source_tool.EXIT_NETWORK if create else source_tool.EXIT_INTEGRITY
        raise source_tool.SourceToolError(
            f"cannot inspect toolchain cache root {cache}: {error}",
            exit_code,
        ) from error


def _json_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_verified_json(entry: dict[str, object], path: Path) -> dict[str, object]:
    if int(entry["size"]) > MAX_JSON_BYTES:
        raise source_tool.SourceToolError(
            f"OCI JSON object exceeds {MAX_JSON_BYTES} bytes: {entry['id']}",
            source_tool.EXIT_INTEGRITY,
        )
    locked = {
        "id": entry["id"],
        "size": entry["size"],
        "sha256": str(entry["digest"]).removeprefix("sha256:"),
    }
    try:
        with source_tool.open_verified_archive(locked, path) as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("JSON object exceeds read limit")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_without_duplicate_keys,
        )
    except source_tool.SourceToolError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise source_tool.SourceToolError(
            f"cannot parse locked OCI JSON object {entry['id']}: {error}",
            source_tool.EXIT_INTEGRITY,
        ) from error
    if not isinstance(value, dict):
        raise source_tool.SourceToolError(
            f"locked OCI JSON object {entry['id']} must be an object",
            source_tool.EXIT_INTEGRITY,
        )
    return value


def _expect_descriptor(
    value: object,
    expected: dict[str, object],
    location: str,
) -> None:
    if not isinstance(value, dict):
        raise source_tool.SourceToolError(
            f"{location} must be an OCI descriptor",
            source_tool.EXIT_INTEGRITY,
        )
    if (
        value.get("mediaType") != expected["mediaType"]
        or value.get("digest") != expected["digest"]
        or value.get("size") != expected["size"]
    ):
        raise source_tool.SourceToolError(
            f"{location} does not match its locked OCI descriptor",
            source_tool.EXIT_INTEGRITY,
        )


def _hash_uncompressed_layer(
    entry: dict[str, object],
    path: Path,
) -> str:
    locked = {
        "id": entry["id"],
        "size": entry["size"],
        "sha256": str(entry["digest"]).removeprefix("sha256:"),
    }
    digest = hashlib.sha256()
    total = 0
    try:
        with source_tool.open_verified_archive(locked, path) as stream:
            with gzip.GzipFile(fileobj=stream, mode="rb") as decompressed:
                while True:
                    chunk = decompressed.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ROOTFS_UNCOMPRESSED_BYTES:
                        _integrity(
                            "OCI rootfs layer exceeds the uncompressed safety limit"
                        )
                    digest.update(chunk)
    except source_tool.SourceToolError:
        raise
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as error:
        _integrity(f"cannot decompress locked OCI rootfs layer: {error}")
    return "sha256:" + digest.hexdigest()


def _extract_locked_rootfs_members(
    entry: dict[str, object],
    path: Path,
    expected_members: dict[str, tuple[int, str]],
) -> dict[str, bytes]:
    locked = {
        "id": entry["id"],
        "size": entry["size"],
        "sha256": str(entry["digest"]).removeprefix("sha256:"),
    }
    found: dict[str, bytes] = {}
    try:
        with source_tool.open_verified_archive(locked, path) as stream:
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                for member in archive:
                    if member.name not in expected_members:
                        continue
                    if member.name in found:
                        _integrity(f"OCI rootfs repeats locked member {member.name}")
                    if not member.isfile():
                        _integrity(f"OCI rootfs member must be regular: {member.name}")
                    expected_size, expected_sha256 = expected_members[member.name]
                    if member.size != expected_size:
                        _integrity(
                            f"OCI rootfs member size mismatch for {member.name}: "
                            f"expected {expected_size}, got {member.size}"
                        )
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        _integrity(f"cannot read OCI rootfs member {member.name}")
                    raw = extracted.read(expected_size + 1)
                    if len(raw) != expected_size:
                        _integrity(f"OCI rootfs member read mismatch for {member.name}")
                    if hashlib.sha256(raw).hexdigest() != expected_sha256:
                        _integrity(f"OCI rootfs member SHA-256 mismatch for {member.name}")
                    found[member.name] = raw
    except source_tool.SourceToolError:
        raise
    except (OSError, EOFError, tarfile.TarError) as error:
        _integrity(f"cannot inspect locked OCI rootfs layer: {error}")
    missing = sorted(set(expected_members) - set(found))
    if missing:
        _integrity("OCI rootfs is missing locked member(s): " + ", ".join(missing))
    return found


def _verify_oci_graph(
    data: dict[str, object],
    oci_objects: list[dict[str, object]],
    cache: Path,
) -> None:
    by_id = {str(entry["id"]): entry for entry in oci_objects}
    index_entry = by_id["ubuntu-index"]
    manifest_entry = by_id["ubuntu-manifest-amd64"]
    config_entry = by_id["ubuntu-config-amd64"]
    layer_entry = by_id["ubuntu-layer-amd64"]
    index = _read_verified_json(index_entry, cache / str(index_entry["archive"]))
    manifest = _read_verified_json(
        manifest_entry,
        cache / str(manifest_entry["archive"]),
    )
    config = _read_verified_json(config_entry, cache / str(config_entry["archive"]))

    if index.get("schemaVersion") != 2 or index.get("mediaType") != index_entry["mediaType"]:
        raise source_tool.SourceToolError(
            "locked OCI index has an unexpected schema or media type",
            source_tool.EXIT_INTEGRITY,
        )
    descriptors = index.get("manifests")
    if not isinstance(descriptors, list):
        raise source_tool.SourceToolError(
            "locked OCI index manifests must be an array",
            source_tool.EXIT_INTEGRITY,
        )
    amd64_descriptors = [
        descriptor
        for descriptor in descriptors
        if isinstance(descriptor, dict)
        and descriptor.get("platform") == {"architecture": "amd64", "os": "linux"}
    ]
    if len(amd64_descriptors) != 1:
        raise source_tool.SourceToolError(
            "locked OCI index must contain exactly one linux/amd64 descriptor",
            source_tool.EXIT_INTEGRITY,
        )
    _expect_descriptor(amd64_descriptors[0], manifest_entry, "OCI linux/amd64 manifest")

    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") != manifest_entry["mediaType"]
    ):
        raise source_tool.SourceToolError(
            "locked OCI manifest has an unexpected schema or media type",
            source_tool.EXIT_INTEGRITY,
        )
    _expect_descriptor(manifest.get("config"), config_entry, "OCI config")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or len(layers) != 1:
        raise source_tool.SourceToolError(
            "locked OCI manifest must contain exactly one rootfs layer",
            source_tool.EXIT_INTEGRITY,
        )
    _expect_descriptor(layers[0], layer_entry, "OCI rootfs layer")
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise source_tool.SourceToolError(
            "locked OCI config must describe linux/amd64",
            source_tool.EXIT_INTEGRITY,
        )
    rootfs = config.get("rootfs")
    if not isinstance(rootfs, dict) or rootfs.get("type") != "layers":
        _integrity("locked OCI config must contain a layers rootfs descriptor")
    diff_ids = rootfs.get("diff_ids")
    if not isinstance(diff_ids, list) or len(diff_ids) != 1 or not isinstance(
        diff_ids[0], str
    ):
        _integrity("locked OCI config must contain exactly one rootfs diff_id")
    actual_diff_id = _hash_uncompressed_layer(
        layer_entry,
        cache / str(layer_entry["archive"]),
    )
    if diff_ids[0] != actual_diff_id:
        _integrity(
            f"OCI rootfs diff_id mismatch: expected {diff_ids[0]}, got {actual_diff_id}"
        )
    base = _expect_table(data["baseImage"], "baseImage")
    if (
        base["indexDigest"] != index_entry["digest"]
        or base["manifestDigest"] != manifest_entry["digest"]
    ):
        raise source_tool.SourceToolError(
            "base image digests do not match the verified OCI graph",
            source_tool.EXIT_INTEGRITY,
        )


def _verify_apt_roots_and_template(
    apt: dict[str, object],
    *,
    repository_root: Path,
) -> list[str]:
    roots_raw = _read_locked_repository_file(
        apt,
        "rootsFile",
        repository_root=repository_root,
    )
    if b"\r" in roots_raw or not roots_raw.endswith(b"\n") or b"\x00" in roots_raw:
        _integrity("APT roots file must use canonical LF-terminated bytes")
    try:
        roots = roots_raw.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        _integrity(f"APT roots file must be ASCII: {error}")
    if (
        len(roots) != int(apt["rootsCount"])
        or roots != sorted(set(roots))
        or any(APT_PACKAGE_RE.fullmatch(root) is None for root in roots)
    ):
        _integrity("APT roots must be the locked sorted unique package-name set")

    template_raw = _read_locked_repository_file(
        apt,
        "sourcesTemplate",
        repository_root=repository_root,
    )
    expected_template = (
        "Types: deb\n"
        f"URIs: https://snapshot.ubuntu.com/ubuntu/{apt['snapshot']}\n"
        "Suites: " + " ".join(str(value) for value in apt["suites"]) + "\n"
        "Components: " + " ".join(str(value) for value in apt["components"]) + "\n"
        "Architectures: amd64\n"
        "Signed-By: @UBUNTU_ARCHIVE_KEYRING@\n"
    ).encode("ascii")
    if template_raw != expected_template:
        _integrity("Ubuntu sources template does not match the locked APT tuple")
    return roots


def _verify_base_dpkg_lock(
    data: dict[str, object],
    oci_objects: list[dict[str, object]],
    cache: Path,
    *,
    repository_root: Path,
) -> tuple[list[dict[str, str]], list[str], bytes]:
    apt = _expect_table(data["apt"], "apt")
    roots = _verify_apt_roots_and_template(apt, repository_root=repository_root)
    base_lock = _read_locked_repository_file(
        apt,
        "baseDpkgLock",
        repository_root=repository_root,
    )
    rows = _parse_tsv(base_lock, APT_BASE_HEADER, "base dpkg lock")
    if len(rows) != int(apt["baseDpkgPackageCount"]) or rows != sorted(set(rows)):
        _integrity("base dpkg lock rows must be the locked sorted unique set")
    for index, row in enumerate(rows):
        _validate_debian_identity(*row, f"base dpkg lock row {index}")

    by_id = {str(entry["id"]): entry for entry in oci_objects}
    layer = by_id["ubuntu-layer-amd64"]
    expected_members = {
        str(apt["baseStatusMember"]): (
            int(apt["baseStatusSize"]),
            str(apt["baseStatusSha256"]),
        ),
        str(apt["archiveKeyringMember"]): (
            int(apt["archiveKeyringSize"]),
            str(apt["archiveKeyringSha256"]),
        ),
    }
    members = _extract_locked_rootfs_members(
        layer,
        cache / str(layer["archive"]),
        expected_members,
    )
    status_raw = members[str(apt["baseStatusMember"])]
    records = _parse_debian_control(status_raw, "base dpkg status")
    if len(records) != int(apt["baseDpkgPackageCount"]):
        _integrity("base dpkg status package count does not match the lock")
    if _canonical_base_dpkg(records) != base_lock:
        _integrity("base dpkg lock is not the canonical projection of the OCI layer")
    keyring_raw = members[str(apt["archiveKeyringMember"])]
    return records, roots, keyring_raw


def _load_apt_index_rows(
    apt: dict[str, object],
    *,
    repository_root: Path,
) -> list[dict[str, object]]:
    raw = _read_locked_repository_file(
        apt,
        "indexLock",
        repository_root=repository_root,
    )
    rows = _parse_tsv(raw, APT_INDEX_HEADER, "APT index lock")
    if len(rows) != int(apt["indexCount"]):
        _integrity("APT index lock row count does not match the manifest")
    expected_order: list[tuple[str, str, str, str]] = []
    for suite in apt["suites"]:
        expected_order.append((f"{suite}.InRelease", "inrelease", str(suite), "none"))
        for component in apt["components"]:
            expected_order.append(
                (
                    f"{suite}-{component}-amd64.Packages",
                    "packages",
                    str(suite),
                    str(component),
                )
            )
    parsed: list[dict[str, object]] = []
    for index, values in enumerate(rows):
        archive, role, suite, component, size_text, sha256, published, signer = values
        if (archive, role, suite, component) != expected_order[index]:
            _integrity(f"APT index lock row {index} is out of canonical order")
        if size_text.startswith("0"):
            _integrity(f"APT index lock row {index} has a non-canonical size")
        size = _parse_ascii_decimal(size_text, f"APT index lock row {index}.size")
        if size < 1 or size > MAX_APT_INDEX_BYTES:
            _integrity(f"APT index lock row {index} exceeds the index size limit")
        _check_sha256(sha256, f"APT index lock row {index}.sha256")
        if role == "inrelease":
            if published != "none" or signer != apt["archiveSignerFingerprint"]:
                _integrity(f"APT InRelease lock row {index} has invalid signer metadata")
        else:
            if published != sha256 or signer != "none":
                _integrity(f"APT Packages lock row {index} has invalid published metadata")
        parsed.append(
            {
                "archive": archive,
                "role": role,
                "suite": suite,
                "component": component,
                "size": size,
                "sha256": sha256,
                "publishedSha256": published,
                "signerFingerprint": signer,
            }
        )
    return parsed


def _parse_inrelease(
    raw: bytes,
    suite: str,
) -> tuple[dict[str, tuple[str, int]], dict[str, str]]:
    marker = b"\n-----BEGIN PGP SIGNATURE-----\n"
    if (
        not raw.startswith(b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n")
        or raw.count(marker) != 1
        or not raw.endswith(b"-----END PGP SIGNATURE-----\n")
    ):
        _integrity(f"{suite} InRelease is not the expected clear-signed form")
    signed, _signature = raw.split(marker, 1)
    body = signed.split(b"\n\n", 1)[1]
    lines = []
    for line in body.split(b"\n"):
        lines.append(line[2:] if line.startswith(b"- ") else line)
    records = _parse_debian_control(b"\n".join(lines), f"{suite} InRelease body")
    if len(records) != 1:
        _integrity(f"{suite} InRelease must contain one release record")
    release = records[0]
    if release.get("Origin") != "Ubuntu" or release.get("Suite") != suite:
        _integrity(f"{suite} InRelease identity does not match the lock")
    if release.get("Codename") != "noble":
        _integrity(f"{suite} InRelease codename does not match the lock")
    architectures = release.get("Architectures", "").split()
    components = release.get("Components", "").split()
    if "amd64" not in architectures or not {"main", "universe"}.issubset(components):
        _integrity(f"{suite} InRelease omits a locked architecture or component")
    sha256_lines = release.get("SHA256", "").splitlines()
    published: dict[str, tuple[str, int]] = {}
    for line in sha256_lines:
        parts = line.split()
        if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            _integrity(f"{suite} InRelease contains malformed SHA256 metadata")
        published_size = _parse_ascii_decimal(
            parts[1],
            f"{suite} InRelease published size",
        )
        if parts[2] in published:
            _integrity(f"{suite} InRelease repeats published path {parts[2]}")
        published[parts[2]] = (parts[0], published_size)
    return published, release


def _load_apt_package_rows(
    apt: dict[str, object],
    *,
    repository_root: Path,
) -> list[dict[str, object]]:
    raw = _read_locked_repository_file(
        apt,
        "packageLock",
        repository_root=repository_root,
    )
    rows = _parse_tsv(raw, APT_PACKAGE_HEADER, "APT package lock")
    if len(rows) != int(apt["packageCount"]):
        _integrity("APT package lock row count does not match the manifest")
    parsed: list[dict[str, object]] = []
    identities: set[tuple[str, str, str]] = set()
    archives: set[str] = set()
    previous_sort_key: tuple[str, str, str, str] | None = None
    allowed_suites = set(str(value) for value in apt["suites"])
    allowed_components = set(str(value) for value in apt["components"])
    for index, values in enumerate(rows):
        (
            package,
            architecture,
            version,
            archive,
            size_text,
            sha256,
            suites_text,
            components_text,
            repository_filename,
            published_sha256,
        ) = values
        _validate_debian_identity(
            package,
            architecture,
            version,
            f"APT package lock row {index}",
        )
        if not APT_ARCHIVE_RE.fullmatch(archive):
            _integrity(f"APT package lock row {index} has an unsafe archive name")
        if size_text.startswith("0"):
            _integrity(f"APT package lock row {index} has a non-canonical size")
        size = _parse_ascii_decimal(size_text, f"APT package lock row {index}.size")
        if size < 1 or size > MAX_INPUT_BYTES:
            _integrity(f"APT package lock row {index} exceeds the input size limit")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            _integrity(f"APT package lock row {index} has an invalid SHA-256")
        if published_sha256 != sha256:
            _integrity(f"APT package lock row {index} disagrees with published SHA-256")
        suites = suites_text.split(",")
        components = components_text.split(",")
        if (
            suites != sorted(set(suites))
            or not set(suites).issubset(allowed_suites)
            or components != sorted(set(components))
            or not set(components).issubset(allowed_components)
        ):
            _integrity(f"APT package lock row {index} has invalid source origins")
        if (
            repository_filename.startswith("/")
            or "\\" in repository_filename
            or not repository_filename.startswith("pool/")
            or any(part in {"", ".", ".."} for part in repository_filename.split("/"))
        ):
            _integrity(f"APT package lock row {index} has an unsafe repository filename")
        identity = (package, architecture, version)
        sort_key = (*identity, archive)
        if previous_sort_key is not None and sort_key <= previous_sort_key:
            _integrity("APT package lock rows must be strictly sorted")
        previous_sort_key = sort_key
        if identity in identities or archive in archives:
            _integrity("APT package lock repeats an identity or cache archive")
        identities.add(identity)
        archives.add(archive)
        parsed.append(
            {
                "package": package,
                "architecture": architecture,
                "version": version,
                "archive": archive,
                "size": size,
                "sha256": sha256,
                "suites": suites,
                "components": components,
                "repositoryFilename": repository_filename,
            }
        )
    return parsed


def _load_apt_install_order(
    apt: dict[str, object],
    package_rows: list[dict[str, object]],
    *,
    repository_root: Path,
) -> list[dict[str, object]]:
    raw = _read_locked_repository_file(
        apt,
        "installOrder",
        repository_root=repository_root,
    )
    rows = _parse_tsv(raw, APT_INSTALL_ORDER_HEADER, "APT install order")
    if len(rows) != int(apt["installOrderCount"]):
        _integrity("APT install order row count does not match the manifest")
    packages = {
        (
            str(row["package"]),
            str(row["architecture"]),
            str(row["version"]),
        ): row
        for row in package_rows
    }
    parsed: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, values in enumerate(rows, start=1):
        sequence_text, package, architecture, version, archive, sha256 = values
        sequence = _parse_ascii_decimal(
            sequence_text,
            f"APT install order row {index}.sequence",
        )
        if sequence != index or sequence_text != str(index):
            _integrity("APT install order sequence must be canonical and contiguous")
        _validate_debian_identity(
            package,
            architecture,
            version,
            f"APT install order row {index}",
        )
        identity = (package, architecture, version)
        locked = packages.get(identity)
        if locked is None or identity in seen:
            _integrity("APT install order repeats or invents a package identity")
        if archive != locked["archive"] or sha256 != locked["sha256"]:
            _integrity(
                f"APT install order row {index} disagrees with the package lock"
            )
        seen.add(identity)
        parsed.append(
            {
                "sequence": sequence,
                "package": package,
                "architecture": architecture,
                "version": version,
                "archive": archive,
                "sha256": sha256,
            }
        )
    if seen != set(packages):
        _integrity("APT install order does not cover the exact package lock")
    return parsed


def _validate_real_directory(path: Path, label: str) -> None:
    try:
        value = path.lstat()
    except FileNotFoundError as error:
        raise source_tool.SourceToolError(
            f"{label} not found: {path}",
            source_tool.EXIT_MISSING,
        ) from error
    except OSError as error:
        _integrity(f"cannot inspect {label} {path}: {error}")
    if not stat.S_ISDIR(value.st_mode) or path.is_symlink() or _is_reparse(value):
        _integrity(f"{label} must be a real directory: {path}")


def _read_locked_cache_file(
    path: Path,
    *,
    size: int,
    sha256: str,
    label: str,
    maximum: int,
) -> bytes:
    raw = _read_stable_bytes(
        path,
        maximum=maximum,
        label=label,
        missing_exit=source_tool.EXIT_MISSING,
    )
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha256:
        _integrity(f"{label} does not match its locked size/SHA-256")
    return raw


def _exact_regular_names(directory: Path, label: str) -> set[str]:
    names: set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    _integrity(f"{label} contains a non-regular entry: {entry.name}")
                if entry.name in names:
                    _integrity(f"{label} repeats an entry name: {entry.name}")
                names.add(entry.name)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot enumerate {label}: {error}")
    return names


def _gpgv_path() -> Path:
    canonical = Path("/usr/bin/gpgv")
    if not canonical.is_file() or canonical.is_symlink():
        raise source_tool.SourceToolError(
            "canonical /usr/bin/gpgv is required to verify the locked Ubuntu InRelease signatures",
            source_tool.EXIT_MISSING,
        )
    return canonical


def _verify_inrelease_signatures(
    apt: dict[str, object],
    keyring_raw: bytes,
    index_rows: list[dict[str, object]],
    index_bytes: dict[str, bytes],
) -> None:
    gpgv = _gpgv_path()
    expected_fingerprint = str(apt["archiveSignerFingerprint"])
    try:
        with tempfile.TemporaryDirectory(prefix="ziv-toolchain-gpgv-") as directory:
            temporary = Path(directory)
            keyring = temporary / "ubuntu-archive-keyring.gpg"
            keyring.write_bytes(keyring_raw)
            os.chmod(keyring, 0o600)
            for row in index_rows:
                if row["role"] != "inrelease":
                    continue
                release = temporary / str(row["archive"])
                release.write_bytes(index_bytes[str(row["archive"])])
                os.chmod(release, 0o600)
                result = subprocess.run(
                    [
                        str(gpgv),
                        "--homedir",
                        str(temporary),
                        "--keyring",
                        str(keyring),
                        "--status-fd=1",
                        str(release),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"},
                )
                fingerprints = re.findall(
                    rb"^\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40}) ",
                    result.stdout,
                    flags=re.MULTILINE,
                )
                normalized = [value.decode("ascii").lower() for value in fingerprints]
                if result.returncode != 0 or normalized != [expected_fingerprint]:
                    detail = result.stderr.decode("utf-8", errors="replace").strip()
                    _integrity(
                        f"{row['suite']} InRelease signature does not match the locked signer"
                        + (f": {detail}" if detail else "")
                    )
    except source_tool.SourceToolError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        _integrity(f"cannot verify locked InRelease signatures with gpgv: {error}")


def _dpkg_deb_path() -> Path:
    canonical = Path("/usr/bin/dpkg-deb")
    if not canonical.is_file() or canonical.is_symlink():
        raise source_tool.SourceToolError(
            "canonical /usr/bin/dpkg-deb is required to inspect locked Debian archives",
            source_tool.EXIT_MISSING,
        )
    if not Path("/proc/self/fd").is_dir():
        raise source_tool.SourceToolError(
            "Linux /proc/self/fd is required for stable Debian archive inspection",
            source_tool.EXIT_MISSING,
        )
    return canonical


def _verify_deb_control_identity(row: dict[str, object], path: Path) -> None:
    locked = {
        "id": row["package"],
        "size": row["size"],
        "sha256": row["sha256"],
    }
    dpkg_deb = _dpkg_deb_path()
    try:
        with source_tool.open_verified_archive(locked, path) as snapshot:
            descriptor = snapshot.fileno()
            result = subprocess.run(
                [
                    str(dpkg_deb),
                    "--field",
                    f"/proc/self/fd/{descriptor}",
                    "Package",
                    "Architecture",
                    "Version",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
                pass_fds=(descriptor,),
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"},
            )
    except source_tool.SourceToolError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        _integrity(f"cannot inspect locked Debian archive {row['archive']}: {error}")
    if result.returncode != 0 or len(result.stdout) > MAX_TOKEN_BYTES:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        _integrity(
            f"cannot read Debian control identity from {row['archive']}"
            + (f": {detail}" if detail else "")
        )
    records = _parse_debian_control(
        result.stdout,
        f"Debian control identity for {row['archive']}",
    )
    if len(records) != 1:
        _integrity(f"Debian archive {row['archive']} has an invalid control identity")
    record = records[0]
    actual = (
        record.get("Package"),
        record.get("Architecture"),
        record.get("Version"),
    )
    expected = (row["package"], row["architecture"], row["version"])
    if actual != expected:
        _integrity(
            f"Debian archive control identity mismatch for {row['archive']}: "
            f"expected {expected}, got {actual}"
        )


def _verify_apt_cache_bytes(
    apt: dict[str, object],
    apt_cache: Path,
    index_rows: list[dict[str, object]],
    package_rows: list[dict[str, object]],
    install_order: list[dict[str, object]],
    keyring_raw: bytes,
) -> list[dict[str, str]]:
    _validate_real_directory(apt_cache, "APT cache")
    root_names = set()
    try:
        with os.scandir(apt_cache) as entries:
            for entry in entries:
                if entry.name in {"debs", "indices"}:
                    if not entry.is_dir(follow_symlinks=False):
                        _integrity(f"APT cache {entry.name} must be a real directory")
                elif entry.name in {"SHA256SUMS", "simulation.txt"}:
                    if not entry.is_file(follow_symlinks=False):
                        _integrity(f"APT cache {entry.name} must be a regular file")
                else:
                    _integrity(f"APT cache contains an unexpected root entry: {entry.name}")
                root_names.add(entry.name)
    except source_tool.SourceToolError:
        raise
    except OSError as error:
        _integrity(f"cannot enumerate APT cache: {error}")
    expected_root_names = {"debs", "indices", "SHA256SUMS", "simulation.txt"}
    if root_names != expected_root_names:
        _integrity("APT cache does not contain the canonical root entry set")
    deb_directory = apt_cache / "debs"
    index_directory = apt_cache / "indices"
    _validate_real_directory(deb_directory, "APT deb cache")
    _validate_real_directory(index_directory, "APT index cache")

    expected_index_names = {str(row["archive"]) for row in index_rows}
    actual_index_names = _exact_regular_names(index_directory, "APT index cache")
    if actual_index_names != expected_index_names:
        missing = sorted(expected_index_names - actual_index_names)
        extra = sorted(actual_index_names - expected_index_names)
        _integrity(f"APT index cache exact-set mismatch; missing={missing}, extra={extra}")

    index_bytes: dict[str, bytes] = {}
    published_by_suite: dict[str, dict[str, tuple[str, int]]] = {}
    for row in index_rows:
        raw = _read_locked_cache_file(
            index_directory / str(row["archive"]),
            size=int(row["size"]),
            sha256=str(row["sha256"]),
            label=f"APT index {row['archive']}",
            maximum=MAX_APT_INDEX_BYTES,
        )
        index_bytes[str(row["archive"])] = raw
        if row["role"] == "inrelease":
            published, _release = _parse_inrelease(raw, str(row["suite"]))
            published_by_suite[str(row["suite"])] = published
    _verify_inrelease_signatures(apt, keyring_raw, index_rows, index_bytes)
    for row in index_rows:
        if row["role"] != "packages":
            continue
        published_path = f"{row['component']}/binary-amd64/Packages"
        expected = (str(row["sha256"]), int(row["size"]))
        if published_by_suite[str(row["suite"])].get(published_path) != expected:
            _integrity(
                f"{row['suite']} InRelease does not publish the locked {published_path}"
            )

    selected_hashes = {str(row["sha256"]) for row in package_rows}
    origins: dict[tuple[int, str], list[tuple[str, str, dict[str, str]]]] = {}
    for row in index_rows:
        if row["role"] != "packages":
            continue
        records = _parse_debian_control(
            index_bytes[str(row["archive"])],
            f"APT Packages {row['suite']}/{row['component']}",
        )
        seen_identities: set[tuple[str, str, str]] = set()
        for record_index, record in enumerate(records):
            sha256 = record.get("SHA256")
            if sha256 not in selected_hashes:
                continue
            required = ("Package", "Architecture", "Version", "Filename", "Size")
            if any(key not in record for key in required):
                _integrity(
                    f"selected Packages record {row['suite']}/{row['component']}/"
                    f"{record_index} omits identity metadata"
                )
            size_text = record["Size"]
            size = _parse_ascii_decimal(size_text, "selected Packages record Size")
            identity = (record["Package"], record["Architecture"], record["Version"])
            if identity in seen_identities:
                _integrity(
                    f"APT Packages {row['suite']}/{row['component']} repeats "
                    f"selected identity {identity}"
                )
            seen_identities.add(identity)
            origins.setdefault((size, sha256), []).append(
                (str(row["suite"]), str(row["component"]), record)
            )

    expected_deb_names = {str(row["archive"]) for row in package_rows}
    actual_deb_names = _exact_regular_names(deb_directory, "APT deb cache")
    if actual_deb_names != expected_deb_names:
        missing = sorted(expected_deb_names - actual_deb_names)
        extra = sorted(actual_deb_names - expected_deb_names)
        _integrity(f"APT deb cache exact-set mismatch; missing={missing}, extra={extra}")

    selected_records: list[dict[str, str]] = []
    for row in package_rows:
        _verify_deb_control_identity(row, deb_directory / str(row["archive"]))
        matches = origins.get((int(row["size"]), str(row["sha256"])), [])
        if not matches:
            _integrity(f"APT package {row['package']} is absent from locked Packages indexes")
        identities = {
            (
                record["Package"],
                record["Architecture"],
                record["Version"],
                record["Filename"],
            )
            for _suite, _component, record in matches
        }
        expected_identity = (
            row["package"],
            row["architecture"],
            row["version"],
            row["repositoryFilename"],
        )
        if identities != {expected_identity}:
            _integrity(f"APT package identity mismatch for {row['package']}")
        suites = sorted({suite for suite, _component, _record in matches})
        components = sorted({component for _suite, component, _record in matches})
        if suites != row["suites"] or components != row["components"]:
            _integrity(f"APT package source-origin mismatch for {row['package']}")
        representative = matches[0][2]
        relevant_fields = (
            "Package",
            "Architecture",
            "Version",
            "Depends",
            "Pre-Depends",
            "Provides",
            "Multi-Arch",
        )
        signature = tuple(representative.get(field, "") for field in relevant_fields)
        if any(
            tuple(record.get(field, "") for field in relevant_fields) != signature
            for _suite, _component, record in matches[1:]
        ):
            _integrity(f"APT package dependency metadata diverges across origins: {row['package']}")
        selected_records.append(representative)

    expected_sums: list[str] = []
    for row in package_rows:
        expected_sums.append(f"{row['sha256']}  debs/{row['archive']}")
    for row in index_rows:
        expected_sums.append(f"{row['sha256']}  indices/{row['archive']}")
    expected_sums.sort(key=lambda value: value.split("  ", 1)[1])
    expected_sum_bytes = ("\n".join(expected_sums) + "\n").encode("ascii")
    actual_sum_bytes = _read_stable_bytes(
        apt_cache / "SHA256SUMS",
        maximum=MAX_APT_LOCK_BYTES,
        label="APT SHA256SUMS",
        missing_exit=source_tool.EXIT_MISSING,
    )
    if actual_sum_bytes != expected_sum_bytes:
        _integrity("APT SHA256SUMS is not the canonical exact-set receipt")

    simulation = _read_stable_bytes(
        apt_cache / "simulation.txt",
        maximum=MAX_APT_LOCK_BYTES,
        label="APT solver simulation",
        missing_exit=source_tool.EXIT_MISSING,
    )
    if len(simulation) != int(apt["simulationSize"]):
        _integrity("APT solver simulation size does not match the manifest")
    if hashlib.sha256(simulation).hexdigest() != apt["simulationSha256"]:
        _integrity("APT solver simulation SHA-256 does not match the manifest")
    if b"\r" in simulation:
        _integrity("APT solver simulation must use LF-only bytes")
    try:
        simulation_text = simulation.decode("utf-8")
    except UnicodeDecodeError as error:
        _integrity(f"cannot decode APT solver simulation: {error}")
    observed: dict[str, list[tuple[str, str, str]]] = {"Inst": [], "Conf": []}
    observed_sets: dict[str, set[tuple[str, str, str]]] = {
        "Inst": set(),
        "Conf": set(),
    }
    line_pattern = re.compile(
        r"^(Inst|Conf) ([a-z0-9][a-z0-9+.-]*) \((\S+).* \[(amd64|all)\]\)$"
    )
    for line in simulation_text.splitlines():
        match = line_pattern.fullmatch(line)
        if match is not None:
            action, package, version, architecture = match.groups()
            identity = (package, architecture, version)
            if identity in observed_sets[action]:
                _integrity(f"APT solver simulation repeats {action} identity {identity}")
            observed_sets[action].add(identity)
            observed[action].append(identity)
    expected_order = [
        (str(row["package"]), str(row["architecture"]), str(row["version"]))
        for row in install_order
    ]
    if observed["Inst"] != expected_order or observed["Conf"] != expected_order:
        _integrity(
            "APT solver simulation order does not match the locked install order"
        )
    summary = f"0 upgraded, {len(package_rows)} newly installed, 0 to remove"
    if summary not in simulation_text:
        _integrity("APT solver simulation summary does not match the package lock")
    return selected_records


def _dependency_candidates(
    atom: tuple[str, str | None, str | None, str | None],
    records_by_name: dict[str, dict[str, str]],
    providers: dict[str, list[tuple[dict[str, str], str | None]]],
) -> list[dict[str, str]]:
    name, qualifier, operator, expected_version = atom
    candidates: list[tuple[int, str, dict[str, str]]] = []

    def architecture_matches(record: dict[str, str]) -> bool:
        architecture = record["Architecture"]
        if qualifier == "any":
            return architecture == "all" or record.get("Multi-Arch") == "allowed"
        return architecture in {"amd64", "all"}

    direct = records_by_name.get(name)
    if (
        direct is not None
        and architecture_matches(direct)
        and _version_satisfies(direct["Version"], operator, expected_version)
    ):
        candidates.append((0, direct["Package"], direct))
    for provider, provided_version in providers.get(name, []):
        if provider is direct or not architecture_matches(provider):
            continue
        if _version_satisfies(provided_version, operator, expected_version):
            candidates.append((1, provider["Package"], provider))
    candidates.sort(key=lambda value: (value[0], value[1]))
    return [value[2] for value in candidates]


def _verify_dependency_closure(
    base_records: list[dict[str, str]],
    selected_records: list[dict[str, str]],
    roots: list[str],
) -> int:
    records = base_records + selected_records
    records_by_name: dict[str, dict[str, str]] = {}
    selected_names = {record["Package"] for record in selected_records}
    for index, record in enumerate(records):
        required = ("Package", "Architecture", "Version")
        if any(key not in record for key in required):
            _integrity(f"final package record {index} is missing identity fields")
        _validate_debian_identity(
            record["Package"],
            record["Architecture"],
            record["Version"],
            f"final package record {index}",
        )
        if record["Package"] in records_by_name:
            _integrity(f"final package set repeats name {record['Package']}")
        multi_arch = record.get("Multi-Arch", "")
        if multi_arch not in {"", "same", "foreign", "allowed"}:
            _integrity(f"package {record['Package']} has unsupported Multi-Arch metadata")
        records_by_name[record["Package"]] = record

    providers: dict[str, list[tuple[dict[str, str], str | None]]] = {}
    for record in records:
        for provided_name, provided_version in _parse_provides(
            record.get("Provides", ""),
            f"{record['Package']}.Provides",
        ):
            providers.setdefault(provided_name, []).append((record, provided_version))

    relation_cache: dict[str, list[list[tuple[str, str | None, str | None, str | None]]]] = {}
    clause_count = 0
    for record in records:
        clauses: list[list[tuple[str, str | None, str | None, str | None]]] = []
        for field in ("Pre-Depends", "Depends"):
            clauses.extend(
                _parse_relationships(
                    record.get(field, ""),
                    f"{record['Package']}.{field}",
                )
            )
        relation_cache[record["Package"]] = clauses
        clause_count += len(clauses)
        for clause in clauses:
            if not any(
                _dependency_candidates(atom, records_by_name, providers)
                for atom in clause
            ):
                rendered = " | ".join(atom[0] for atom in clause)
                _integrity(
                    f"package {record['Package']} has an unsatisfied dependency: {rendered}"
                )

    missing_roots = sorted(set(roots) - set(records_by_name))
    if missing_roots:
        _integrity("APT root packages are absent from the final set: " + ", ".join(missing_roots))
    reachable = set(roots)
    queue = deque(roots)
    while queue:
        package = queue.popleft()
        for clause in relation_cache[package]:
            clause_candidates: dict[str, dict[str, str]] = {}
            for atom in clause:
                for candidate in _dependency_candidates(atom, records_by_name, providers):
                    clause_candidates[candidate["Package"]] = candidate
            if not clause_candidates:
                _integrity(f"reachable package {package} has no dependency candidate")
            for chosen_name in sorted(clause_candidates):
                if chosen_name not in reachable:
                    reachable.add(chosen_name)
                    queue.append(chosen_name)
    unreachable_selected = sorted(selected_names - reachable)
    if unreachable_selected:
        _integrity(
            "APT package lock contains packages unreachable from the declared roots: "
            + ", ".join(unreachable_selected)
        )
    return clause_count


def verify_apt_cache(
    data: dict[str, object],
    oci_objects: list[dict[str, object]],
    cache: Path,
    apt_cache: Path,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> None:
    _validate_cache_root(cache, create=False)
    _verify_oci_graph(data, oci_objects, cache)
    apt = _expect_table(data["apt"], "apt")
    base_records, roots, keyring_raw = _verify_base_dpkg_lock(
        data,
        oci_objects,
        cache,
        repository_root=repository_root,
    )
    index_rows = _load_apt_index_rows(apt, repository_root=repository_root)
    package_rows = _load_apt_package_rows(apt, repository_root=repository_root)
    install_order = _load_apt_install_order(
        apt,
        package_rows,
        repository_root=repository_root,
    )
    selected_records = _verify_apt_cache_bytes(
        apt,
        apt_cache,
        index_rows,
        package_rows,
        install_order,
        keyring_raw,
    )
    clauses = _verify_dependency_closure(base_records, selected_records, roots)
    print(
        f"verified APT closure: {len(base_records)} base package(s), "
        f"{len(selected_records)} cached package(s), {clauses} dependency clause(s)"
    )


def verify_roots(
    data: dict[str, object],
    artifacts: list[dict[str, object]],
    oci_objects: list[dict[str, object]],
    cache: Path,
) -> None:
    _validate_cache_root(cache, create=False)
    entries = _cache_entries(artifacts, oci_objects)
    missing: list[str] = []
    for entry in entries:
        path = cache / str(entry["archive"])
        if path.is_symlink():
            raise source_tool.SourceToolError(
                f"cached input must not be a symlink: {path}",
                source_tool.EXIT_INTEGRITY,
            )
        if not path.exists():
            missing.append(str(entry["archive"]))
        elif not path.is_file():
            raise source_tool.SourceToolError(
                f"cache path is not a regular file: {path}",
                source_tool.EXIT_INTEGRITY,
            )
    if missing:
        raise source_tool.SourceToolError(
            f"offline toolchain cache is missing {len(missing)} input(s): "
            + ", ".join(missing),
            source_tool.EXIT_MISSING,
        )
    for artifact in artifacts:
        path = cache / str(artifact["archive"])
        with source_tool.open_verified_archive(artifact, path) as stream:
            published_sha1 = artifact.get("publishedSha1")
            if published_sha1 is not None:
                digest = hashlib.sha1(usedforsecurity=False)
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                if digest.hexdigest() != published_sha1:
                    raise source_tool.SourceToolError(
                        f"published SHA-1 mismatch for {artifact['id']}",
                        source_tool.EXIT_INTEGRITY,
                    )
        print(f"verified {artifact['id']}: {path.name}")
    for entry in oci_objects:
        locked = {
            "id": entry["id"],
            "size": entry["size"],
            "sha256": str(entry["digest"]).removeprefix("sha256:"),
        }
        path = cache / str(entry["archive"])
        source_tool.verify_archive(locked, path)
        print(f"verified {entry['id']}: {path.name}")
    _verify_oci_graph(data, oci_objects, cache)
    print("verified OCI index -> linux/amd64 manifest -> config/rootfs closure")


def verify_cache(
    data: dict[str, object],
    artifacts: list[dict[str, object]],
    oci_objects: list[dict[str, object]],
    cache: Path,
    apt_cache: Path | None = None,
) -> None:
    verify_roots(data, artifacts, oci_objects, cache)
    verify_apt_cache(
        data,
        oci_objects,
        cache,
        apt_cache if apt_cache is not None else cache / "apt",
    )


def _open_https(request: urllib.request.Request, timeout: float) -> object:
    return source_tool._open_https(request, timeout)


class RegistryRedirectHandler(source_tool.HTTPSOnlyRedirectHandler):
    """Strip registry bearer credentials when redirects cross origins."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )
        if redirected is None:
            return None
        original = urllib.parse.urlsplit(request.full_url)
        destination = urllib.parse.urlsplit(redirected.full_url)
        original_origin = (original.scheme, original.hostname, original.port or 443)
        destination_origin = (destination.scheme, destination.hostname, destination.port or 443)
        if original_origin != destination_origin:
            for name in ("Authorization", "Proxy-Authorization", "Cookie", "Cookie2"):
                redirected.remove_header(name)
        return redirected


REGISTRY_HTTPS_OPENER = urllib.request.build_opener(RegistryRedirectHandler())


def _open_registry_https(request: urllib.request.Request, timeout: float) -> object:
    return REGISTRY_HTTPS_OPENER.open(request, timeout=timeout)


def _registry_token(repository: str, timeout: float) -> str:
    query = urllib.parse.urlencode(
        {
            "service": "registry.docker.io",
            "scope": f"repository:{repository}:pull",
        }
    )
    url = f"https://auth.docker.io/token?{query}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ZivPlayer-native-toolchain-tool/1"},
    )
    try:
        with _open_https(request, timeout) as response:
            source_tool._validate_https_url(
                response.geturl(),
                "final Docker registry token URL",
                allow_query=True,
                exit_code=source_tool.EXIT_NETWORK,
            )
            raw = response.read(MAX_TOKEN_BYTES + 1)
        if len(raw) > MAX_TOKEN_BYTES:
            raise ValueError("registry token response exceeds limit")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_without_duplicate_keys,
        )
        token = value.get("token") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError("registry token response has no token")
        return token
    except source_tool.SourceToolError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise source_tool.SourceToolError(
            f"cannot obtain Docker registry token: {error}",
            source_tool.EXIT_NETWORK,
        ) from error


def _download_registry_object(
    entry: dict[str, object],
    repository: str,
    token: str,
    cache: Path,
    timeout: float,
) -> None:
    locked = {
        "id": entry["id"],
        "archive": entry["archive"],
        "size": entry["size"],
        "sha256": str(entry["digest"]).removeprefix("sha256:"),
    }
    target = cache / str(entry["archive"])
    if target.is_symlink():
        raise source_tool.SourceToolError(
            f"cache object must not be a symlink: {target}",
            source_tool.EXIT_INTEGRITY,
        )
    if target.exists():
        source_tool.verify_archive(locked, target)
        print(f"cached {entry['id']}: {target.name}")
        return
    object_type = "manifests" if entry["kind"] in {"index", "manifest"} else "blobs"
    url = (
        f"https://registry-1.docker.io/v2/{repository}/{object_type}/"
        f"{entry['digest']}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": str(entry["mediaType"]),
            "User-Agent": "ZivPlayer-native-toolchain-tool/1",
        },
    )
    part: Path | None = None
    part_identity: tuple[int, int] | None = None
    try:
        with _open_registry_https(request, timeout) as response:
            source_tool._validate_https_url(
                response.geturl(),
                f"final OCI URL for {entry['id']}",
                allow_query=True,
                exit_code=source_tool.EXIT_NETWORK,
            )
            descriptor, part_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".part",
                dir=cache,
            )
            part = Path(part_name)
            descriptor_stat = os.fstat(descriptor)
            part_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
            expected_size = int(entry["size"])
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > expected_size:
                        raise source_tool.SourceToolError(
                            f"download for {entry['id']} exceeded locked size {expected_size}",
                            source_tool.EXIT_INTEGRITY,
                        )
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if size != int(entry["size"]):
            raise source_tool.SourceToolError(
                f"size mismatch for {entry['id']}: expected {entry['size']}, got {size}",
                source_tool.EXIT_INTEGRITY,
            )
        if digest.hexdigest() != locked["sha256"]:
            raise source_tool.SourceToolError(
                f"SHA-256 mismatch for downloaded {entry['id']}",
                source_tool.EXIT_INTEGRITY,
            )
        published = source_tool._publish_download_no_replace(
            part,
            target,
            part_identity,
        )
        if published:
            part = None
            source_tool.verify_archive(locked, target)
            print(f"fetched {entry['id']}: {target.name}")
        else:
            source_tool.verify_archive(locked, target)
            cleanup_error = source_tool._cleanup_partial(part, part_identity)
            if cleanup_error is not None:
                raise source_tool.SourceToolError(
                    f"cannot remove losing OCI partial: {cleanup_error}",
                    source_tool.EXIT_NETWORK,
                )
            part = None
            print(f"cached {entry['id']}: {target.name}")
    except BaseException as error:
        cleanup_error = source_tool._cleanup_partial(part, part_identity)
        if cleanup_error is not None:
            raise source_tool.SourceToolError(
                f"OCI download failed for {entry['id']} and cleanup failed: {cleanup_error}",
                source_tool.EXIT_NETWORK,
            ) from error
        if isinstance(error, source_tool.SourceToolError):
            raise
        if isinstance(error, KeyboardInterrupt):
            raise
        if isinstance(error, Exception):
            raise source_tool.SourceToolError(
                f"OCI download failed for {entry['id']}: {error}",
                source_tool.EXIT_NETWORK,
            ) from error
        raise


def fetch_cache(
    data: dict[str, object],
    artifacts: list[dict[str, object]],
    oci_objects: list[dict[str, object]],
    cache: Path,
    timeout: float,
) -> None:
    _validate_cache_root(cache, create=True)
    source_tool.fetch_sources(artifacts, cache, timeout)
    base = _expect_table(data["baseImage"], "baseImage")
    missing_oci = any(
        not (cache / str(entry["archive"])).exists() for entry in oci_objects
    )
    token = _registry_token(str(base["repository"]), timeout) if missing_oci else ""
    for entry in oci_objects:
        _download_registry_object(
            entry,
            str(base["repository"]),
            token,
            cache,
            timeout,
        )
    verify_roots(data, artifacts, oci_objects, cache)


def _lock_blockers(
    data: dict[str, object],
    source_data: dict[str, object],
) -> list[str]:
    blockers: list[str] = []
    project = _expect_table(data["project"], "project")
    base = _expect_table(data["baseImage"], "baseImage")
    apt = _expect_table(data["apt"], "apt")
    compliance = _expect_table(data["compliance"], "compliance")
    source_toolchain = _expect_table(source_data["toolchain"], "source toolchain")
    if project["environmentStatus"] != "release-inputs-locked":
        blockers.append("toolchain environment status is pending")
    if base["objectClosureStatus"] != "complete":
        blockers.append("OCI object closure is pending")
    if apt["closureStatus"] != "complete":
        blockers.append("APT transitive closure is pending")
    if apt["baseDpkgSnapshotStatus"] != "complete":
        blockers.append("base image dpkg snapshot is pending")
    for key in sorted(COMPLIANCE_KEYS):
        if compliance[key] != "complete":
            blockers.append(f"compliance.{key} is pending")
    if source_toolchain.get("environmentStatus") != "source-and-container-locked":
        blockers.append("source manifest still marks the container pending")
    return blockers


def check_lock(
    data: dict[str, object],
    artifacts: list[dict[str, object]],
    oci_objects: list[dict[str, object]],
    source_data: dict[str, object],
    cache: Path,
    apt_cache: Path | None = None,
) -> None:
    verify_cache(data, artifacts, oci_objects, cache, apt_cache)
    blockers = _lock_blockers(data, source_data)
    if blockers:
        raise source_tool.SourceToolError(
            "native toolchain lock is not release-input complete: " + "; ".join(blockers),
            source_tool.EXIT_MISSING,
        )
    print("native toolchain release-input lock complete")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"locked TOML manifest (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=DEFAULT_SOURCE_MANIFEST,
        help=f"bound source manifest (default: {DEFAULT_SOURCE_MANIFEST})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate both bound manifests without writes")
    verify_roots_parser = subparsers.add_parser(
        "verify-roots",
        help="verify artifact and OCI root bytes without network or writes",
    )
    verify_roots_parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    verify_apt_parser = subparsers.add_parser(
        "verify-apt-cache",
        help="verify the signed APT cache and transitive closure without network or writes",
    )
    verify_apt_parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    verify_apt_parser.add_argument("--apt-cache", type=Path, default=DEFAULT_CACHE / "apt")
    verify = subparsers.add_parser(
        "verify-cache",
        help="verify all root and signed APT closure bytes without network or writes",
    )
    verify.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    verify.add_argument("--apt-cache", type=Path, default=DEFAULT_CACHE / "apt")
    fetch = subparsers.add_parser(
        "fetch",
        help="explicitly fetch missing locked artifact and OCI bytes over HTTPS",
    )
    fetch.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    fetch.add_argument("--timeout", type=float, default=60.0)
    check = subparsers.add_parser(
        "check-lock",
        help="verify bytes and fail unless every M7C release-input gate is complete",
    )
    check.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    check.add_argument("--apt-cache", type=Path, default=DEFAULT_CACHE / "apt")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        data, artifacts, oci_objects, source_data = load_manifest(
            arguments.manifest,
            source_manifest_path=arguments.source_manifest,
        )
        if arguments.command == "validate":
            project = _expect_table(data["project"], "project")
            print(
                f"toolchain manifest valid: {len(artifacts)} artifact root(s), "
                f"{len(oci_objects)} OCI object(s); "
                f"status={project['environmentStatus']}"
            )
        elif arguments.command == "verify-roots":
            verify_roots(data, artifacts, oci_objects, arguments.cache)
        elif arguments.command == "verify-apt-cache":
            verify_apt_cache(
                data,
                oci_objects,
                arguments.cache,
                arguments.apt_cache,
            )
        elif arguments.command == "verify-cache":
            verify_cache(
                data,
                artifacts,
                oci_objects,
                arguments.cache,
                arguments.apt_cache,
            )
        elif arguments.command == "fetch":
            if not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
                raise source_tool.SourceToolError(
                    "--timeout must be finite and greater than zero",
                    source_tool.EXIT_SCHEMA,
                )
            fetch_cache(
                data,
                artifacts,
                oci_objects,
                arguments.cache,
                arguments.timeout,
            )
        elif arguments.command == "check-lock":
            check_lock(
                data,
                artifacts,
                oci_objects,
                source_data,
                arguments.cache,
                arguments.apt_cache,
            )
        else:
            raise source_tool.SourceToolError(
                f"unsupported command: {arguments.command}",
                source_tool.EXIT_SCHEMA,
            )
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
