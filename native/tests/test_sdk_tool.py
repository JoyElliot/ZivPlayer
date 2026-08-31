# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import base64
import hashlib
import io
import os
import stat
import struct
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import sdk_tool  # noqa: E402
import source_tool  # noqa: E402


def zip_member(
    name: str,
    payload: bytes = b"",
    *,
    mode: int = 0o644,
    file_type: int = stat.S_IFREG,
    compress_type: int = zipfile.ZIP_STORED,
) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (file_type | mode) << 16
    info.compress_type = compress_type
    return info, payload


def zip_payload(members: list[tuple[zipfile.ZipInfo, bytes]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, mode="w", allowZip64=False) as archive:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for info, payload in members:
                archive.writestr(info, payload)
    return stream.getvalue()


def locked_artifact(
    payload: bytes,
    *,
    artifact_id: str,
    kind: str,
    version: str,
    install_path: str,
    archive: str = "fixture.zip",
) -> dict[str, object]:
    return {
        "id": artifact_id,
        "kind": kind,
        "version": version,
        "archive": archive,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "installPath": install_path,
    }


def build_tools_archive(
    extra: list[tuple[zipfile.ZipInfo, bytes]] | None = None,
) -> tuple[bytes, dict[str, object]]:
    properties = b"Pkg.UserSrc=false\nPkg.Revision=36.0.0\n# no final newline"
    members = [zip_member("android-16/source.properties", properties)]
    members.extend(extra or [])
    payload = zip_payload(members)
    artifact = locked_artifact(
        payload,
        artifact_id="android-build-tools",
        kind="android-archive",
        version="36.0.0",
        install_path="android-sdk/build-tools/36.0.0",
    )
    return payload, artifact


def wheel_files() -> dict[str, bytes]:
    return {
        "mesonbuild/__init__.py": b"",
        "mesonbuild/mesonmain.py": b"def main():\n    return 0\n",
        "meson-1.11.0.data/data/share/man/man1/meson.1": b"manual\n",
        "meson-1.11.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: meson\nVersion: 1.11.0\n\n"
        ),
        "meson-1.11.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
        ),
        "meson-1.11.0.dist-info/entry_points.txt": (
            b"[console_scripts]\nmeson = mesonbuild.mesonmain:main\n"
        ),
        "meson-1.11.0.dist-info/licenses/COPYING": b"license\n",
    }


def wheel_payload(
    *,
    files: dict[str, bytes] | None = None,
    record_override: bytes | None = None,
) -> tuple[bytes, dict[str, object]]:
    contents = dict(files or wheel_files())
    record_name = "meson-1.11.0.dist-info/RECORD"
    if record_override is None:
        rows = []
        for name in sorted(contents):
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(contents[name]).digest()
            ).rstrip(b"=").decode("ascii")
            rows.append(f"{name},sha256={digest},{len(contents[name])}")
        rows.append(f"{record_name},,")
        record_override = ("\n".join(rows) + "\n").encode("utf-8")
    contents[record_name] = record_override
    payload = zip_payload(
        [zip_member(name, value, mode=0o664) for name, value in contents.items()]
    )
    artifact = locked_artifact(
        payload,
        artifact_id="meson-wheel",
        kind="python-wheel",
        version="1.11.0",
        install_path="python-wheels/meson-1.11.0-py3-none-any.whl",
        archive="meson.whl",
    )
    return payload, artifact


def artifact_plan(
    artifact_id: str,
    entries: list[sdk_tool.PlannedEntry],
) -> sdk_tool.ArtifactPlan:
    return sdk_tool.ArtifactPlan(
        artifact_id=artifact_id,
        kind="fixture",
        version="1",
        archive=f"{artifact_id}.zip",
        archive_size=1,
        archive_sha256="0" * 64,
        install_path="fixture",
        archive_prefix=None,
        archive_members=len(entries),
        uncompressed_bytes=sum(entry.size for entry in entries),
        source_properties_sha256=None,
        metadata_sha256=None,
        record_sha256=None,
        entries=tuple(entries),
        plan_sha256="1" * 64,
    )


class SdkToolTest(unittest.TestCase):
    def test_android_plan_accepts_no_root_entry_no_final_lf_and_link_chain(self) -> None:
        payload, artifact = build_tools_archive(
            [
                zip_member("android-16/lib/target", b"locked\n"),
                zip_member(
                    "android-16/bin/tool",
                    b"../lib/target",
                    mode=0o777,
                    file_type=stat.S_IFLNK,
                ),
                zip_member(
                    "android-16/bin/alias",
                    b"tool",
                    mode=0o777,
                    file_type=stat.S_IFLNK,
                ),
            ]
        )
        plan = sdk_tool._plan_android_archive(artifact, io.BytesIO(payload))
        by_path = {entry.path.as_posix(): entry for entry in plan.entries}
        self.assertEqual("android-16", plan.archive_prefix)
        self.assertEqual(
            "../lib/target",
            by_path["android-sdk/build-tools/36.0.0/bin/tool"].link,
        )
        self.assertEqual(
            "tool",
            by_path["android-sdk/build-tools/36.0.0/bin/alias"].link,
        )
        self.assertEqual(
            hashlib.sha256(
                b"Pkg.UserSrc=false\nPkg.Revision=36.0.0\n# no final newline"
            ).hexdigest(),
            plan.source_properties_sha256,
        )

    def test_android_plan_rejects_escaping_missing_oversized_and_cyclic_links(self) -> None:
        cases = [
            (
                "escape",
                [
                    zip_member(
                        "android-16/bin/tool",
                        b"../../../outside",
                        mode=0o777,
                        file_type=stat.S_IFLNK,
                    )
                ],
                "escapes",
            ),
            (
                "missing",
                [
                    zip_member(
                        "android-16/bin/tool",
                        b"missing",
                        mode=0o777,
                        file_type=stat.S_IFLNK,
                    )
                ],
                "targets missing",
            ),
            (
                "oversized",
                [
                    zip_member(
                        "android-16/bin/tool",
                        b"a" * (sdk_tool.MAX_SYMLINK_BYTES + 1),
                        mode=0o777,
                        file_type=stat.S_IFLNK,
                    )
                ],
                "oversized symbolic-link",
            ),
            (
                "cycle",
                [
                    zip_member(
                        "android-16/bin/a",
                        b"b",
                        mode=0o777,
                        file_type=stat.S_IFLNK,
                    ),
                    zip_member(
                        "android-16/bin/b",
                        b"a",
                        mode=0o777,
                        file_type=stat.S_IFLNK,
                    ),
                ],
                "symbolic-link cycle",
            ),
            (
                "directory-spelling",
                [
                    zip_member("android-16/lib/target", b"file"),
                    zip_member(
                        "android-16/bin/tool",
                        b"../lib/target/",
                        mode=0o777,
                        file_type=stat.S_IFLNK,
                    ),
                ],
                "directory spelling",
            ),
        ]
        for label, extra, message in cases:
            payload, artifact = build_tools_archive(extra)
            with self.subTest(label=label), self.assertRaisesRegex(
                source_tool.SourceToolError,
                message,
            ) as raised:
                sdk_tool._plan_android_archive(artifact, io.BytesIO(payload))
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_safe_paths_and_properties_reject_ambiguous_input(self) -> None:
        invalid = (
            "../escape",
            "/absolute",
            "a\\b",
            "a//b",
            "a/./b",
            "a\x00b",
            "café",
            "CON/file",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(source_tool.SourceToolError):
                sdk_tool._safe_parts(value, "fixture", directory_spelling=False)
        for raw in (b"a=1\rb=2", b"a=1\x0bb=2", b"a=1\na=2\n", b"invalid\n"):
            with self.subTest(raw=raw), self.assertRaises(source_tool.SourceToolError):
                sdk_tool._properties(raw, "fixture")
        self.assertEqual({"a": "1"}, sdk_tool._properties(b"a=1", "fixture"))

    def test_zip_metadata_and_eocd_preflight_are_bounded(self) -> None:
        info, _payload = zip_member("file", b"x")
        self.assertEqual(
            ("file", 0o644),
            sdk_tool._zip_kind_and_mode(
                info,
                "fixture",
                allow_symlink=False,
                wheel=False,
            ),
        )
        bad = zipfile.ZipInfo("file")
        bad.create_system = 0
        bad.external_attr = (stat.S_IFREG | 0o644) << 16
        with self.assertRaisesRegex(source_tool.SourceToolError, "Unix"):
            sdk_tool._zip_kind_and_mode(
                bad,
                "fixture",
                allow_symlink=False,
                wheel=False,
            )

        payload, artifact = build_tools_archive()
        stream = io.BytesIO(payload)
        sdk_tool._preflight_zip_directory(stream, artifact)
        self.assertEqual(0, stream.tell())
        corrupted = bytearray(payload)
        offset = corrupted.rfind(sdk_tool.EOCD_SIGNATURE)
        fields = list(sdk_tool.EOCD_STRUCT.unpack_from(corrupted, offset))
        fields[1] = 1
        sdk_tool.EOCD_STRUCT.pack_into(corrupted, offset, *fields)
        with self.assertRaisesRegex(source_tool.SourceToolError, "unsupported"):
            sdk_tool._preflight_zip_directory(io.BytesIO(corrupted), artifact)
        with self.assertRaisesRegex(source_tool.SourceToolError, "size"):
            sdk_tool._preflight_zip_directory(
                io.BytesIO(payload + b"x"),
                artifact,
            )

    def test_wheel_plan_maps_resources_and_preserves_locked_wheel(self) -> None:
        payload, artifact = wheel_payload()
        plan = sdk_tool._plan_wheel(artifact, io.BytesIO(payload))
        by_path = {entry.path.as_posix(): entry for entry in plan.entries}
        self.assertIn("python/site-packages/mesonbuild/mesonmain.py", by_path)
        self.assertIn("share/man/man1/meson.1", by_path)
        self.assertTrue(
            by_path["python-wheels/meson-1.11.0-py3-none-any.whl"].opaque_archive
        )
        self.assertEqual(0o755, by_path["bin/meson"].mode)
        self.assertEqual(sdk_tool.MESON_LAUNCHER_BYTES, by_path["bin/meson"].payload)
        self.assertEqual(
            hashlib.sha256(wheel_files()["meson-1.11.0.dist-info/METADATA"]).hexdigest(),
            plan.metadata_sha256,
        )
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "meson.whl").write_bytes(payload)
            hashes = sdk_tool._artifact_file_hashes(
                artifact,
                plan,
                cache,
                destination=None,
            )
        self.assertEqual(
            artifact["sha256"],
            hashes[PurePosixPath("python-wheels/meson-1.11.0-py3-none-any.whl")],
        )
        self.assertEqual(
            hashlib.sha256(sdk_tool.MESON_BOOTSTRAP_BYTES).hexdigest(),
            hashes[sdk_tool.MESON_BOOTSTRAP],
        )

    def test_wheel_record_identity_and_roots_are_exact(self) -> None:
        files = wheel_files()
        good_payload, _artifact = wheel_payload(files=files)
        with zipfile.ZipFile(io.BytesIO(good_payload)) as archive:
            record = archive.read("meson-1.11.0.dist-info/RECORD")
        tampered_files = dict(files)
        tampered_files["mesonbuild/mesonmain.py"] = tampered_files[
            "mesonbuild/mesonmain.py"
        ].replace(b"return 0", b"return 1")
        cases = [
            ("hash", tampered_files, record, "RECORD hash differs"),
            (
                "root",
                {**files, "unsupported/data.txt": b"x"},
                None,
                "outside the supported",
            ),
            (
                "header",
                {
                    **files,
                    "meson-1.11.0.dist-info/METADATA": (
                        files["meson-1.11.0.dist-info/METADATA"].replace(
                            b"\n\n",
                            b"\nName: duplicate\n\n",
                        )
                    ),
                },
                None,
                "METADATA disagrees",
            ),
        ]
        for label, contents, override, message in cases:
            payload, artifact = wheel_payload(
                files=contents,
                record_override=override,
            )
            with self.subTest(label=label), self.assertRaisesRegex(
                source_tool.SourceToolError,
                message,
            ):
                sdk_tool._plan_wheel(artifact, io.BytesIO(payload))

    def test_wheel_record_rejects_size_set_self_and_text_drift(self) -> None:
        payload, _artifact = wheel_payload()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            raw = archive.read("meson-1.11.0.dist-info/RECORD")
        lines = raw.decode("utf-8").splitlines()
        data_index = next(
            index for index, line in enumerate(lines) if line.startswith("mesonbuild/")
        )
        name, digest, size = lines[data_index].split(",")
        self_name = "meson-1.11.0.dist-info/RECORD"
        valid_empty = base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).rstrip(
            b"="
        ).decode("ascii")
        variants = {
            "size": (
                lines[:data_index]
                + [f"{name},{digest},{int(size) + 1}"]
                + lines[data_index + 1 :]
            ),
            "missing": lines[:data_index] + lines[data_index + 1 :],
            "duplicate": lines[:data_index] + [lines[data_index]] + lines[data_index:],
            "self": [
                (
                    f"{self_name},sha256={valid_empty},0"
                    if line == f"{self_name},,"
                    else line
                )
                for line in lines
            ],
            "digest": (
                lines[:data_index]
                + [f"{name},sha256={'A' * 43},{size}"]
                + lines[data_index + 1 :]
            ),
        }
        messages = {
            "size": "RECORD size differs",
            "missing": "member set differs",
            "duplicate": "not canonical",
            "self": "leave its own",
            "digest": "RECORD hash differs",
        }
        for label, variant in variants.items():
            record = ("\n".join(variant) + "\n").encode("utf-8")
            candidate, artifact = wheel_payload(record_override=record)
            with self.subTest(label=label), self.assertRaisesRegex(
                source_tool.SourceToolError,
                messages[label],
            ):
                sdk_tool._plan_wheel(artifact, io.BytesIO(candidate))

        candidate, artifact = wheel_payload(record_override=raw.replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(source_tool.SourceToolError, "LF-terminated"):
            sdk_tool._plan_wheel(artifact, io.BytesIO(candidate))

    def test_zip_entry_metadata_and_android_structure_are_strict(self) -> None:
        cases: list[tuple[str, zipfile.ZipInfo, str]] = []
        encrypted, _ = zip_member("encrypted", b"x")
        encrypted.flag_bits = 1
        cases.append(("encrypted", encrypted, "flags"))
        compression, _ = zip_member("compression", b"x")
        compression.compress_type = 99
        cases.append(("compression", compression, "compression"))
        special, _ = zip_member("special", b"x", mode=0o4644)
        cases.append(("special", special, "special permission"))
        symlink, _ = zip_member(
            "link",
            b"target",
            mode=0o777,
            file_type=stat.S_IFLNK,
        )
        cases.append(("symlink", symlink, "unsupported symbolic link"))
        for label, info, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                source_tool.SourceToolError,
                message,
            ):
                sdk_tool._zip_kind_and_mode(
                    info,
                    "fixture",
                    allow_symlink=False,
                    wheel=False,
                )

        structural = [
            (
                "prefix",
                [zip_member("other/file", b"x")],
                "one top-level prefix",
            ),
            (
                "ancestor",
                [
                    zip_member("android-16/tree", b"file"),
                    zip_member("android-16/tree/child", b"child"),
                ],
                "below non-directory",
            ),
            (
                "duplicate",
                [
                    zip_member("android-16/repeated", b"one"),
                    zip_member("android-16/repeated", b"two"),
                ],
                "repeats",
            ),
        ]
        for label, extra, message in structural:
            payload, artifact = build_tools_archive(extra)
            with self.subTest(label=label), self.assertRaisesRegex(
                source_tool.SourceToolError,
                message,
            ):
                sdk_tool._plan_android_archive(artifact, io.BytesIO(payload))

    def test_android_identity_is_exact_for_all_locked_archive_kinds(self) -> None:
        cases = [
            (
                "android-cmdline-tools",
                "12.0",
                "android-sdk/cmdline-tools/12.0",
                "cmdline-tools",
                b"Pkg.Revision=12.0\nPkg.Path=cmdline-tools;12.0\n",
            ),
            (
                "android-ndk",
                "29.0.14206865",
                "android-sdk/ndk/29.0.14206865",
                "android-ndk-r29",
                (
                    b"Pkg.Revision=29.0.14206865\n"
                    b"Pkg.BaseRevision=29.0.14206865\n"
                    b"Pkg.ReleaseName=r29\n"
                ),
            ),
            (
                "android-platform",
                "36-r02",
                "android-sdk/platforms/android-36",
                "android-36",
                (
                    b"AndroidVersion.ApiLevel=36\n"
                    b"Pkg.Revision=2\n"
                    b"AndroidVersion.IsBaseSdk=true\n"
                ),
            ),
        ]
        for artifact_id, version, install, prefix, properties in cases:
            payload = zip_payload(
                [zip_member(f"{prefix}/source.properties", properties)]
            )
            artifact = locked_artifact(
                payload,
                artifact_id=artifact_id,
                kind="android-archive",
                version=version,
                install_path=install,
            )
            with self.subTest(artifact_id=artifact_id):
                plan = sdk_tool._plan_android_archive(artifact, io.BytesIO(payload))
                self.assertEqual(
                    hashlib.sha256(properties).hexdigest(),
                    plan.source_properties_sha256,
                )
                changed = (
                    properties.replace(b"true", b"false")
                    .replace(b"r29", b"r28")
                    .replace(b"12.0", b"11.0", 1)
                )
                bad_payload = zip_payload(
                    [zip_member(f"{prefix}/source.properties", changed)]
                )
                bad_artifact = dict(artifact)
                bad_artifact["size"] = len(bad_payload)
                bad_artifact["sha256"] = hashlib.sha256(bad_payload).hexdigest()
                with self.assertRaisesRegex(source_tool.SourceToolError, "disagrees"):
                    sdk_tool._plan_android_archive(
                        bad_artifact,
                        io.BytesIO(bad_payload),
                    )

    def test_projection_entries_synthesize_parents_and_control_collisions(self) -> None:
        file_entry = sdk_tool.PlannedEntry(
            "one",
            PurePosixPath("a/b/file"),
            "file",
            0o644,
            1,
            payload=b"x",
        )
        entries = sdk_tool._projection_entries((artifact_plan("one", [file_entry]),))
        by_path = {entry.path.as_posix(): entry for entry in entries}
        self.assertEqual("directory", by_path["a"].kind)
        self.assertEqual("directory", by_path["a/b"].kind)

        cases = [
            (
                "duplicate",
                (
                    artifact_plan("one", [file_entry]),
                    artifact_plan("two", [file_entry]),
                ),
                "repeats output path",
            ),
            (
                "case",
                (
                    artifact_plan(
                        "one",
                        [
                            sdk_tool.PlannedEntry(
                                "one",
                                PurePosixPath("bin/Meson"),
                                "file",
                                0o644,
                                0,
                                payload=b"",
                            )
                        ],
                    ),
                    artifact_plan(
                        "two",
                        [
                            sdk_tool.PlannedEntry(
                                "two",
                                PurePosixPath("bin/meson"),
                                "file",
                                0o644,
                                0,
                                payload=b"",
                            )
                        ],
                    ),
                ),
                "cross-artifact case",
            ),
            (
                "receipt",
                (
                    artifact_plan(
                        "one",
                        [
                            sdk_tool.PlannedEntry(
                                "one",
                                PurePosixPath(sdk_tool.RECEIPT_NAME.upper()),
                                "file",
                                0o644,
                                0,
                                payload=b"",
                            )
                        ],
                    ),
                ),
                "receipt",
            ),
            (
                "receipt-parent",
                (
                    artifact_plan(
                        "one",
                        [
                            sdk_tool.PlannedEntry(
                                "one",
                                PurePosixPath(sdk_tool.RECEIPT_NAME.upper()) / "child",
                                "file",
                                0o644,
                                0,
                                payload=b"",
                            )
                        ],
                    ),
                ),
                "receipt",
            ),
            (
                "parent",
                (
                    artifact_plan(
                        "one",
                        [
                            sdk_tool.PlannedEntry(
                                "one",
                                PurePosixPath("a"),
                                "file",
                                0o644,
                                0,
                                payload=b"",
                            ),
                            file_entry,
                        ],
                    ),
                ),
                "below non-directory",
            ),
        ]
        for label, plans, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                source_tool.SourceToolError,
                message,
            ):
                sdk_tool._projection_entries(plans)

        synthesized_parent_collision = (
            artifact_plan(
                "one",
                [
                    sdk_tool.PlannedEntry(
                        "one",
                        PurePosixPath("a"),
                        "file",
                        0o644,
                        0,
                        payload=b"",
                    )
                ],
            ),
            artifact_plan(
                "two",
                [
                    sdk_tool.PlannedEntry(
                        "two",
                        PurePosixPath("A/child"),
                        "file",
                        0o644,
                        0,
                        payload=b"",
                    )
                ],
            ),
        )
        with self.assertRaisesRegex(source_tool.SourceToolError, "case path collision"):
            sdk_tool._projection_entries(synthesized_parent_collision)

        ndk_pair = sorted(next(iter(sdk_tool.ALLOWED_NDK_CASE_PAIRS)))
        ndk_aliases = artifact_plan(
            "android-ndk",
            [
                sdk_tool.PlannedEntry(
                    "android-ndk",
                    PurePosixPath(path),
                    "file",
                    0o644,
                    0,
                    payload=b"",
                )
                for path in ndk_pair
            ],
        )
        projected_aliases = sdk_tool._projection_entries((ndk_aliases,))
        self.assertTrue(
            set(ndk_pair)
            <= {entry.path.as_posix() for entry in projected_aliases}
        )

    def test_consume_exact_rejects_short_and_extra_streams(self) -> None:
        self.assertEqual(
            hashlib.sha256(b"abc").hexdigest(),
            sdk_tool._consume_exact(io.BytesIO(b"abc"), 3, "fixture"),
        )
        with self.assertRaisesRegex(source_tool.SourceToolError, "ended"):
            sdk_tool._consume_exact(io.BytesIO(b"ab"), 3, "fixture")
        with self.assertRaisesRegex(source_tool.SourceToolError, "exceeds"):
            sdk_tool._consume_exact(io.BytesIO(b"abcd"), 3, "fixture")

    def test_case_probe_preserves_preexisting_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lower = root / ".ziv-sdk-case-probe"
            lower.write_bytes(b"user-data")
            with self.assertRaises(source_tool.SourceToolError):
                sdk_tool._require_case_sensitive_filesystem(root)
            self.assertEqual(b"user-data", lower.read_bytes())

    def test_projection_and_expected_tree_records_are_deterministic(self) -> None:
        entries = (
            sdk_tool.PlannedEntry(
                "generated",
                PurePosixPath("bin"),
                "directory",
                0o755,
                0,
            ),
            sdk_tool.PlannedEntry(
                "meson-wheel",
                PurePosixPath("bin/meson"),
                "file",
                0o755,
                3,
                payload=b"abc",
            ),
            sdk_tool.PlannedEntry(
                "android-ndk",
                PurePosixPath("bin/link"),
                "symlink",
                0o777,
                5,
                link="meson",
            ),
        )
        records = sdk_tool._projection_records(
            entries,
            {PurePosixPath("bin/meson"): hashlib.sha256(b"abc").hexdigest()},
        )
        projection = sdk_tool._projection_record_data(records)
        tree = sdk_tool._expected_tree(records)
        self.assertEqual(3, projection["entries"])
        self.assertEqual(1, projection["files"])
        self.assertEqual(1, projection["symlinks"])
        self.assertEqual(3, tree["fileBytes"])
        self.assertEqual(1, tree["directories"])
        self.assertEqual([], sdk_tool._legal_inventory(records))

        legal = (
            sdk_tool.ProjectionRecord(
                "android-ndk",
                "android-sdk/ndk/NOTICE.toolchain",
                "file",
                0o644,
                1,
                hashlib.sha256(b"x").hexdigest(),
                None,
            ),
            sdk_tool.ProjectionRecord(
                "source",
                "licenses/LICENSE-APACHE",
                "file",
                0o644,
                0,
                hashlib.sha256(b"").hexdigest(),
                None,
            ),
            sdk_tool.ProjectionRecord(
                "source",
                "licenses/LICENCE",
                "file",
                0o644,
                0,
                hashlib.sha256(b"").hexdigest(),
                None,
            ),
        )
        self.assertEqual(3, len(sdk_tool._legal_inventory(legal)))

    def test_receipt_keeps_compliance_pending_and_binds_helpers(self) -> None:
        meson_plan = artifact_plan(
            "meson-wheel",
            [
                sdk_tool.PlannedEntry(
                    "meson-wheel",
                    PurePosixPath("bin/meson"),
                    "file",
                    0o755,
                    3,
                    payload=b"abc",
                )
            ],
        )
        state = sdk_tool.LockedSdkState(
            data={
                "project": {
                    "name": "ZivPlayer",
                    "sourceManifestSha256": "a" * 64,
                    "hostPlatform": "linux/amd64",
                    "nativeApi": 26,
                    "abis": ["arm64-v8a", "x86_64"],
                    "pageSizeBytes": 16384,
                },
                "compliance": {
                    "androidLicenseFilesStatus": "pending",
                    "systemNoticesStatus": "pending",
                    "retentionBundleStatus": "pending",
                },
            },
            artifacts=(),
            source_data={
                "project": {
                    "upstreamRelease": "2026-08-11",
                    "upstreamRevision": "b" * 40,
                }
            },
            manifest_sha256="c" * 64,
            plans=(meson_plan,),
        )
        records = (
            sdk_tool.ProjectionRecord(
                "meson-wheel",
                "bin/meson",
                "file",
                0o755,
                3,
                hashlib.sha256(b"abc").hexdigest(),
                None,
            ),
        )
        receipt = sdk_tool._receipt_data(state, records, sdk_tool._expected_tree(records))
        self.assertFalse(receipt["ready"])
        self.assertEqual(sdk_tool.MOUNT_PATH, receipt["mountPath"])
        self.assertEqual("pending", receipt["compliance"]["androidLicenseFilesStatus"])
        self.assertEqual(len(sdk_tool.HELPER_PATHS), len(receipt["helpers"]))
        self.assertTrue(
            any(
                "source-build Python module closure" in blocker
                for blocker in receipt["releaseBlockers"]
            )
        )

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "atomic materialization guard requires Linux root",
    )
    def test_materialize_refuses_existing_output_before_loading_state(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            parent = Path(directory)
            output = parent / "projection"
            output.mkdir()
            with (
                patch.object(sdk_tool.rootfs_tool, "_require_ext4"),
                patch.object(
                    sdk_tool.environment_tool,
                    "_trusted_output_parent_identity",
                    return_value=(
                        parent.lstat().st_dev,
                        parent.lstat().st_ino,
                    ),
                ),
                patch.object(sdk_tool, "_assert_outside_cache"),
                patch.object(sdk_tool, "_load_locked_state") as loader,
                self.assertRaisesRegex(source_tool.SourceToolError, "refusing to replace"),
            ):
                sdk_tool.materialize_sdk_projection(
                    Path("manifest"),
                    Path("source"),
                    Path("cache"),
                    output,
                )
            loader.assert_not_called()

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "atomic materialization tests require Linux root",
    )
    def test_materialize_publishes_only_after_verification(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            parent = Path(directory)
            output = parent / "projection"
            parent_identity = (parent.lstat().st_dev, parent.lstat().st_ino)

            def write_receipt(root: Path, _receipt: dict[str, object]) -> None:
                (root / sdk_tool.RECEIPT_NAME).write_bytes(b"receipt")

            with (
                patch.object(sdk_tool.rootfs_tool, "_require_ext4"),
                patch.object(
                    sdk_tool.environment_tool,
                    "_trusted_output_parent_identity",
                    return_value=parent_identity,
                ),
                patch.object(sdk_tool, "_assert_outside_cache"),
                patch.object(sdk_tool, "_load_locked_state", return_value=object()),
                patch.object(sdk_tool, "_materialize_content", return_value=((), {})),
                patch.object(sdk_tool, "_receipt_data", return_value={"ready": False}),
                patch.object(sdk_tool, "_write_receipt", side_effect=write_receipt),
                patch.object(sdk_tool, "verify_sdk_projection") as verify,
                patch.object(sdk_tool.environment_tool, "_sync_filesystem"),
                patch.object(sdk_tool.materialize_sources, "_fsync_workspace_directories"),
                patch.object(
                    sdk_tool.materialize_sources,
                    "_rename_no_replace",
                    side_effect=lambda source, destination: os.rename(source, destination),
                ),
                patch.object(sdk_tool.materialize_sources, "_fsync_directory"),
            ):
                sdk_tool.materialize_sdk_projection(
                    Path("manifest"),
                    Path("source"),
                    Path("cache"),
                    output,
                )
            self.assertEqual(b"receipt", (output / sdk_tool.RECEIPT_NAME).read_bytes())
            self.assertEqual(1, verify.call_count)
            verified_root = verify.call_args.args[-1]
            self.assertTrue(verified_root.name.startswith(".projection."))
            self.assertFalse(verified_root.exists())
            self.assertEqual([], list(parent.glob(".projection.*.part")))

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "atomic materialization tests require Linux root",
    )
    def test_materialize_cleans_private_staging_on_prepublication_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            parent = Path(directory)
            output = parent / "projection"
            parent_identity = (parent.lstat().st_dev, parent.lstat().st_ino)
            failure = source_tool.SourceToolError("injected", source_tool.EXIT_INTEGRITY)
            with (
                patch.object(sdk_tool.rootfs_tool, "_require_ext4"),
                patch.object(
                    sdk_tool.environment_tool,
                    "_trusted_output_parent_identity",
                    return_value=parent_identity,
                ),
                patch.object(sdk_tool, "_assert_outside_cache"),
                patch.object(sdk_tool, "_load_locked_state", return_value=object()),
                patch.object(sdk_tool, "_materialize_content", side_effect=failure),
                self.assertRaisesRegex(source_tool.SourceToolError, "injected"),
            ):
                sdk_tool.materialize_sdk_projection(
                    Path("manifest"),
                    Path("source"),
                    Path("cache"),
                    output,
                )
            self.assertFalse(output.exists())
            self.assertEqual([], list(parent.glob(".projection.*.part")))


if __name__ == "__main__":
    unittest.main()
