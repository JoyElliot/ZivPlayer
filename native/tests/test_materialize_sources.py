# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import bz2
import gzip
import io
import json
import lzma
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import materialize_sources  # noqa: E402
import source_tool  # noqa: E402


def add_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    archive.addfile(info)


def add_file(archive: tarfile.TarFile, name: str, content: bytes = b"content\n") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(content))


def add_symlink(archive: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    info.mode = 0o777
    archive.addfile(info)


def add_hardlink(archive: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    info.mode = 0o644
    archive.addfile(info)


def write_archive(path: Path, callback: object) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        callback(archive)


def source(
    source_id: str,
    archive: str,
    *,
    license_file: str = "LICENSE",
    parent: str | None = None,
    destination: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": source_id,
        "archive": archive,
        "licenseFiles": [license_file],
        "revision": f"revision-{source_id}",
        "size": 1024,
        "sha256": hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
    }
    if parent is not None:
        result["parent"] = parent
        result["destination"] = destination
    return result


class MaterializeSourcesTest(unittest.TestCase):
    def test_tree_permission_mode_rejects_special_bits(self) -> None:
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "unsupported special permission bits",
        ) as raised:
            materialize_sources._checked_permission_mode(
                stat.S_IFREG | 0o4755,
                "test-file",
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_tar_metadata_reads_are_bounded_before_member_planning(self) -> None:
        compressors = {
            "raw": lambda payload: payload,
            "gzip": gzip.compress,
            "bzip2": bz2.compress,
            "xz": lzma.compress,
        }
        for entry_type in (tarfile.GNUTYPE_LONGNAME, tarfile.XHDTYPE):
            for compression, compress in compressors.items():
                with self.subTest(
                    entry_type=entry_type,
                    compression=compression,
                ), tempfile.TemporaryDirectory() as directory:
                    archive_path = Path(directory) / "metadata-bomb.tar"
                    info = tarfile.TarInfo("././@Metadata")
                    info.type = entry_type
                    info.size = materialize_sources.MAX_TAR_READ_BYTES + 1
                    raw_tar = info.tobuf(format=tarfile.GNU_FORMAT) + (b"\0" * 1024)
                    archive_path.write_bytes(compress(raw_tar))

                    with archive_path.open("rb") as archive_stream, self.assertRaisesRegex(
                        source_tool.SourceToolError,
                        "metadata record limit",
                    ) as raised:
                        with materialize_sources._open_bounded_tar(
                            archive_stream,
                            "metadata-bomb",
                            archive_path.stat().st_size,
                        ) as archive:
                            materialize_sources._plan_tar(archive, "metadata-bomb")
                    self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_bounded_tar_accepts_supported_compressions(self) -> None:
        raw_stream = io.BytesIO()
        with tarfile.open(fileobj=raw_stream, mode="w") as archive:
            add_file(archive, "root/LICENSE", b"license\n")
        raw_tar = raw_stream.getvalue()
        payloads = {
            "raw": raw_tar,
            "gzip": gzip.compress(raw_tar),
            "bzip2": bz2.compress(raw_tar),
            "xz": lzma.compress(raw_tar),
        }
        for compression, payload in payloads.items():
            with self.subTest(compression=compression), tempfile.TemporaryDirectory() as directory:
                with materialize_sources._open_bounded_tar(
                    io.BytesIO(payload),
                    compression,
                    len(payload),
                ) as archive:
                    root, planned = materialize_sources._plan_tar(archive, compression)
                    proofs = materialize_sources._archive_regular_file_proofs(
                        archive,
                        planned,
                        compression,
                    )
                self.assertEqual("root", root)
                self.assertEqual(
                    [PurePosixPath("LICENSE")],
                    [member.path for member in planned],
                )
                proof = proofs[(compression, PurePosixPath("LICENSE"))]
                self.assertEqual(len(b"license\n"), proof.size)
                self.assertEqual(hashlib.sha256(b"license\n").hexdigest(), proof.sha256)

                destination = Path(directory) / "output"
                destination.mkdir()
                materialize_sources._extract_tar(
                    {"id": compression, "size": len(payload)},
                    io.BytesIO(payload),
                    destination,
                    link_mode="portable-copy",
                )
                self.assertEqual(b"license\n", (destination / "LICENSE").read_bytes())

    def test_tar_metadata_budget_is_cumulative(self) -> None:
        class FakeArchive:
            pass

        first = materialize_sources._BoundedTarInfo("pax-1")
        first.type = tarfile.XHDTYPE
        first.size = 513
        second = materialize_sources._BoundedTarInfo("pax-2")
        second.type = tarfile.XHDTYPE
        second.size = 513
        archive = FakeArchive()
        with (
            patch.object(materialize_sources, "MAX_TAR_METADATA_BUDGET_BYTES", 1536),
            patch.object(tarfile.TarInfo, "_proc_member", return_value=tarfile.TarInfo()),
        ):
            first._proc_member(archive)
            with self.assertRaisesRegex(
                source_tool.SourceToolError,
                "metadata budget",
            ) as raised:
                second._proc_member(archive)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_tar_extension_chain_depth_is_bounded(self) -> None:
        extension = tarfile.TarInfo("././@PaxHeader")
        extension.type = tarfile.XHDTYPE
        extension.size = 0
        regular = tarfile.TarInfo("root/LICENSE")
        regular.size = 0
        raw_tar = (
            extension.tobuf(format=tarfile.PAX_FORMAT)
            * (materialize_sources.MAX_TAR_EXTENSION_CHAIN_DEPTH + 1)
            + regular.tobuf(format=tarfile.PAX_FORMAT)
            + (b"\0" * (2 * tarfile.BLOCKSIZE))
        )

        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "extension chain",
        ) as raised:
            with materialize_sources._open_bounded_tar(
                io.BytesIO(raw_tar),
                "extension-chain",
                len(raw_tar),
            ) as archive:
                materialize_sources._plan_tar(archive, "extension-chain")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_gnu_sparse_is_rejected_before_extended_header_processing(self) -> None:
        sparse = tarfile.TarInfo("root/sparse")
        sparse.type = tarfile.GNUTYPE_SPARSE
        sparse.size = 0
        raw_tar = sparse.tobuf(format=tarfile.GNU_FORMAT) + (
            b"\0" * (2 * tarfile.BLOCKSIZE)
        )
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "unsupported GNU sparse",
        ) as raised:
            with materialize_sources._open_bounded_tar(
                io.BytesIO(raw_tar),
                "gnu-sparse",
                len(raw_tar),
            ) as archive:
                materialize_sources._plan_tar(archive, "gnu-sparse")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_pax_gnu_sparse_variants_are_rejected_before_sparse_data_reads(self) -> None:
        def pax_record(key: str, value: str) -> bytes:
            suffix = f" {key}={value}\n".encode("ascii")
            length = len(suffix) + 1
            while True:
                record = str(length).encode("ascii") + suffix
                if len(record) == length:
                    return record
                length = len(record)

        sparse_variants = {
            "0.0": {"GNU.sparse.size": "1"},
            "0.1": {"GNU.sparse.map": "0,1"},
            "1.0": {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"},
        }
        for version, headers in sparse_variants.items():
            with self.subTest(version=version):
                body = b"".join(
                    pax_record(key, value) for key, value in headers.items()
                )
                extension = tarfile.TarInfo("././@PaxHeader")
                extension.type = tarfile.XHDTYPE
                extension.size = len(body)
                regular = tarfile.TarInfo("root/sparse")
                regular.size = 0
                raw_tar = (
                    extension.tobuf(format=tarfile.PAX_FORMAT)
                    + body
                    + (
                        b"\0"
                        * (
                            (tarfile.BLOCKSIZE - len(body) % tarfile.BLOCKSIZE)
                            % tarfile.BLOCKSIZE
                        )
                    )
                    + regular.tobuf(format=tarfile.PAX_FORMAT)
                    + (b"\0" * (2 * tarfile.BLOCKSIZE))
                )

                with self.assertRaisesRegex(
                    source_tool.SourceToolError,
                    "unsupported GNU sparse",
                ) as raised:
                    with materialize_sources._open_bounded_tar(
                        io.BytesIO(raw_tar),
                        f"pax-sparse-{version}",
                        len(raw_tar),
                    ) as archive:
                        materialize_sources._plan_tar(
                            archive,
                            f"pax-sparse-{version}",
                        )
                self.assertEqual(
                    source_tool.EXIT_INTEGRITY,
                    raised.exception.exit_code,
                )

    def test_malformed_compressed_tar_is_an_integrity_error(self) -> None:
        malformed_streams = (
            b"\x1f\x8bBAD-GZIP",
            b"BZh91AY&SYBAD-BZIP2",
            b"\xfd7zXZ\x00BAD-XZ",
            b"BAD-RAW-TAR",
        )
        for payload in malformed_streams:
            with self.subTest(payload=payload[:6]), self.assertRaises(
                source_tool.SourceToolError
            ) as raised:
                with materialize_sources._open_bounded_tar(
                    io.BytesIO(payload),
                    "malformed",
                    len(payload),
                ):
                    pass
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_malformed_pax_value_is_an_integrity_error(self) -> None:
        def pax_record(key: str, value: str) -> bytes:
            suffix = f" {key}={value}\n".encode("ascii")
            length = len(suffix) + 1
            while True:
                record = str(length).encode("ascii") + suffix
                if len(record) == length:
                    return record
                length = len(record)

        body = pax_record("GNU.sparse.map", "bad")
        pax = tarfile.TarInfo("././@PaxHeader")
        pax.type = tarfile.XHDTYPE
        pax.size = len(body)
        regular = tarfile.TarInfo("root/file")
        regular.size = 0
        padded_body = body + (
            b"\0" * ((tarfile.BLOCKSIZE - len(body) % tarfile.BLOCKSIZE) % tarfile.BLOCKSIZE)
        )
        raw_tar = (
            pax.tobuf(format=tarfile.PAX_FORMAT)
            + padded_body
            + regular.tobuf(format=tarfile.PAX_FORMAT)
            + (b"\0" * (2 * tarfile.BLOCKSIZE))
        )
        with self.assertRaises(source_tool.SourceToolError) as raised:
            with materialize_sources._open_bounded_tar(
                io.BytesIO(raw_tar),
                "malformed-pax",
                len(raw_tar),
            ) as archive:
                materialize_sources._plan_tar(archive, "malformed-pax")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_raw_tar_name_starting_with_bzh_is_not_misclassified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "raw.tar"
            with tarfile.open(archive_path, mode="w") as archive:
                add_file(archive, "BZhX1AY&SY-root/LICENSE")
            with archive_path.open("rb") as archive_stream:
                with materialize_sources._open_bounded_tar(
                    archive_stream,
                    "raw-bzh",
                    archive_path.stat().st_size,
                ) as archive:
                    root, planned = materialize_sources._plan_tar(archive, "raw-bzh")
            self.assertEqual("BZhX1AY&SY-root", root)
            self.assertEqual([PurePosixPath("LICENSE")], [member.path for member in planned])

    def test_tar_rejects_nonzero_trailing_payload(self) -> None:
        raw_stream = io.BytesIO()
        with tarfile.open(fileobj=raw_stream, mode="w") as archive:
            add_file(archive, "root/file")
        payload = raw_stream.getvalue() + b"HIDDEN-TRAILING-PAYLOAD"
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "non-zero data after.*end marker",
        ) as raised:
            with materialize_sources._open_bounded_tar(
                io.BytesIO(payload),
                "trailing",
                len(payload),
            ) as archive:
                materialize_sources._plan_tar(archive, "trailing")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_accepts_single_root_without_explicit_root_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.tar.gz"
            write_archive(
                archive_path,
                lambda archive: add_file(archive, "root/LICENSE"),
            )
            with tarfile.open(archive_path, mode="r:*") as archive:
                root, members = materialize_sources._plan_tar(archive, "example")
        self.assertEqual("root", root)
        self.assertEqual([PurePosixPath("LICENSE")], [member.path for member in members])

    def test_plan_rejects_unsafe_member_paths(self) -> None:
        for member_name in (
            "root/../escape",
            "/absolute",
            "root/C:/ads",
            "C:\\absolute",
            "\\\\server\\share",
            "root/a\\b",
            "root/a//b",
            "root/a/./b",
            "root/NUL",
            "root/COM1.txt",
            "root/file:stream",
            "root/control\x01name",
            "root/trailing.",
            "root/trailing ",
        ):
            with self.subTest(member_name=member_name), tempfile.TemporaryDirectory() as directory:
                archive_path = Path(directory) / "source.tar.gz"

                def populate(archive: tarfile.TarFile) -> None:
                    add_directory(archive, "root")
                    add_file(archive, member_name)

                write_archive(archive_path, populate)
                with tarfile.open(archive_path, mode="r:*") as archive:
                    with self.assertRaises(source_tool.SourceToolError) as raised:
                        materialize_sources._plan_tar(archive, "example")
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_excessive_path_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "deep.tar.gz"
            deep_path = "/".join(
                ["root"] + ["d"] * materialize_sources.MAX_ARCHIVE_PATH_DEPTH + ["file"]
            )

            def populate(archive: tarfile.TarFile) -> None:
                add_file(archive, deep_path)

            write_archive(archive_path, populate)
            with tarfile.open(archive_path, mode="r:*") as archive, self.assertRaisesRegex(
                source_tool.SourceToolError,
                "component archive path limit",
            ) as raised:
                materialize_sources._plan_tar(archive, "deep")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_exact_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/file")
                add_file(archive, "root/file")

            write_archive(archive_path, populate)
            with tarfile.open(archive_path, mode="r:*") as archive:
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "repeats path"
                ) as raised:
                    materialize_sources._plan_tar(archive, "example")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_case_and_unicode_collisions(self) -> None:
        for first, second in (("A", "a"), ("é", "e\u0301")):
            with self.subTest(first=first, second=second), tempfile.TemporaryDirectory() as directory:
                archive_path = Path(directory) / "source.tar.gz"

                def populate(archive: tarfile.TarFile) -> None:
                    add_directory(archive, "root")
                    add_file(archive, f"root/{first}")
                    add_file(archive, f"root/{second}")

                write_archive(archive_path, populate)
                with tarfile.open(archive_path, mode="r:*") as archive:
                    with self.assertRaisesRegex(
                        source_tool.SourceToolError, "collision"
                    ) as raised:
                        materialize_sources._plan_tar(archive, "example")
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_file_parent_and_special_entry(self) -> None:
        for special in (False, True):
            with self.subTest(special=special), tempfile.TemporaryDirectory() as directory:
                archive_path = Path(directory) / "source.tar.gz"

                def populate(archive: tarfile.TarFile) -> None:
                    add_directory(archive, "root")
                    if special:
                        info = tarfile.TarInfo("root/pipe")
                        info.type = tarfile.FIFOTYPE
                        archive.addfile(info)
                    else:
                        add_file(archive, "root/file")
                        add_file(archive, "root/file/child")

                write_archive(archive_path, populate)
                with tarfile.open(archive_path, mode="r:*") as archive:
                    with self.assertRaises(source_tool.SourceToolError) as raised:
                        materialize_sources._plan_tar(archive, "example")
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_special_permission_bits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "special-mode.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                info = tarfile.TarInfo("root/tool")
                info.mode = 0o4755
                info.size = 0
                archive.addfile(info, io.BytesIO())

            write_archive(archive_path, populate)
            with tarfile.open(archive_path, mode="r:*") as archive, self.assertRaisesRegex(
                source_tool.SourceToolError,
                "special permission bits",
            ) as raised:
                materialize_sources._plan_tar(archive, "special-mode")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_directory_spelling_for_regular_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/file/")

            write_archive(archive_path, populate)
            with tarfile.open(archive_path, mode="r:*") as archive, self.assertRaisesRegex(
                source_tool.SourceToolError, "directory spelling"
            ) as raised:
                materialize_sources._plan_tar(archive, "example")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_accepts_safe_and_dangling_directory_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target")
                add_symlink(archive, "root/file-link", "target")
                add_symlink(archive, "root/directory-link", "generated/")

            write_archive(archive_path, populate)
            with tarfile.open(archive_path, mode="r:*") as archive:
                _, members = materialize_sources._plan_tar(archive, "example")
        links = {member.path: member.link_target for member in members if member.kind == "symlink"}
        self.assertEqual(PurePosixPath("target"), links[PurePosixPath("file-link")])
        self.assertEqual(PurePosixPath("generated"), links[PurePosixPath("directory-link")])

    def test_plan_rejects_escaping_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_symlink(archive, "root/link", "../outside")

            write_archive(archive_path, populate)
            with tarfile.open(archive_path, mode="r:*") as archive:
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "escapes"
                ) as raised:
                    materialize_sources._plan_tar(archive, "example")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_unsafe_and_ambiguous_symlink_targets(self) -> None:
        for target in (
            "/absolute",
            "C:/absolute",
            "a\\b",
            "../../outside",
            "a//b",
            "./target",
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                archive_path = Path(directory) / "source.tar.gz"

                def populate(archive: tarfile.TarFile) -> None:
                    add_directory(archive, "root")
                    add_directory(archive, "root/dir")
                    add_symlink(archive, "root/dir/link", target)

                write_archive(archive_path, populate)
                with tarfile.open(archive_path, mode="r:*") as archive:
                    with self.assertRaises(source_tool.SourceToolError) as raised:
                        materialize_sources._plan_tar(archive, "example")
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.tar.gz"

            def ambiguous(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/FOO")
                add_symlink(archive, "root/link", "foo")

            write_archive(archive_path, ambiguous)
            with tarfile.open(archive_path, mode="r:*") as archive:
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "ambiguous case/Unicode target"
                ) as raised:
                    materialize_sources._plan_tar(archive, "example")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_plan_rejects_directory_spelling_for_file_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "source.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target")
                add_symlink(archive, "root/link", "target/")

            write_archive(archive_path, populate)
            with tarfile.open(archive_path, mode="r:*") as archive, self.assertRaisesRegex(
                source_tool.SourceToolError, "directory target spelling"
            ) as raised:
                materialize_sources._plan_tar(archive, "example")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_hardlink_requires_full_rooted_regular_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.tar.gz"

            def valid_members(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target")
                add_hardlink(archive, "root/link", "root/target")

            write_archive(valid, valid_members)
            with tarfile.open(valid, mode="r:*") as archive:
                _, members = materialize_sources._plan_tar(archive, "example")
            hardlinks = [member for member in members if member.kind == "hardlink"]
            self.assertEqual(PurePosixPath("target"), hardlinks[0].link_target)

            invalid = root / "invalid.tar.gz"

            def invalid_members(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target")
                add_hardlink(archive, "root/link", "target")

            write_archive(invalid, invalid_members)
            with tarfile.open(invalid, mode="r:*") as archive:
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "hard link escapes"
                ) as raised:
                    materialize_sources._plan_tar(archive, "example")
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_portable_link_mode_copies_file_and_materializes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "source.tar.gz"
            destination = root / "output"
            destination.mkdir()

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target", b"locked\n")
                add_symlink(archive, "root/file-link", "target")
                add_symlink(archive, "root/directory-link", "generated/")

            write_archive(archive_path, populate)
            with archive_path.open("rb") as archive:
                materialize_sources._extract_tar(
                    {"id": "example", "size": archive_path.stat().st_size},
                    archive,
                    destination,
                    link_mode="portable-copy",
                )
            self.assertEqual(b"locked\n", (destination / "file-link").read_bytes())
            self.assertTrue((destination / "directory-link").is_dir())
            self.assertFalse((destination / "file-link").is_symlink())

    def test_extracted_metadata_uses_reproducible_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            output.write_bytes(b"content")
            for archive_mtime in (float("nan"), float("inf"), -1.0, 4_102_444_800.0):
                with self.subTest(archive_mtime=archive_mtime):
                    info = tarfile.TarInfo("root/output")
                    info.mode = 0o644
                    info.mtime = archive_mtime
                    materialize_sources._set_mode_and_time(output, info)
                    self.assertEqual(
                        materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                        output.stat().st_mtime_ns,
                    )

    def test_plan_enforces_member_and_uncompressed_size_caps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "source.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/a", b"a")
                add_file(archive, "root/b", b"b")

            write_archive(archive_path, populate)
            with (
                tarfile.open(archive_path, mode="r:*") as archive,
                patch.object(materialize_sources, "MAX_ARCHIVE_MEMBERS", 2),
                self.assertRaisesRegex(source_tool.SourceToolError, "exceeds 2 entries"),
            ):
                materialize_sources._plan_tar(archive, "example")

            with (
                tarfile.open(archive_path, mode="r:*") as archive,
                patch.object(materialize_sources, "MAX_ARCHIVE_BYTES", 1),
                self.assertRaisesRegex(
                    source_tool.SourceToolError, "exceeds the extraction size limit"
                ),
            ):
                materialize_sources._plan_tar(archive, "example")

    def test_preflight_counts_link_expansion_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.mkdir()
            archive_path = cache / "root.tar.gz"

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target", b"ab")
                add_hardlink(archive, "root/link", "root/target")

            write_archive(archive_path, populate)
            sources = [source("mpv-android", archive_path.name)]
            with (
                patch.object(materialize_sources, "MAX_ARCHIVE_BYTES", 2),
                patch("materialize_sources.source_tool.open_verified_archive") as open_archive,
                self.assertRaisesRegex(
                    source_tool.SourceToolError, "expanded link-copy byte limit"
                ) as raised,
            ):
                open_archive.side_effect = lambda source, path: path.open("rb")
                materialize_sources._scan_locked_archives(
                    sources,
                    cache,
                    {"mpv-android": PurePosixPath(".")},
                )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_preserve_link_mode_uses_relative_archive_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "source.tar.gz"
            destination = root / "output"
            destination.mkdir()

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target")
                add_symlink(archive, "root/link", "target")

            write_archive(archive_path, populate)
            with (
                archive_path.open("rb") as archive,
                patch("materialize_sources.os.symlink") as symlink,
                patch("materialize_sources._set_symlink_time") as set_symlink_time,
            ):
                materialize_sources._extract_tar(
                    {"id": "example", "size": archive_path.stat().st_size},
                    archive,
                    destination,
                    link_mode="preserve",
                )
            symlink.assert_called_once_with(
                "target", destination / "link", target_is_directory=False
            )
            set_symlink_time.assert_called_once_with(destination / "link")

    def test_portable_link_mode_copies_hardlink_without_mutating_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "source.tar.gz"
            destination = root / "output"
            destination.mkdir()

            def populate(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/target", b"locked\n")
                add_hardlink(archive, "root/link", "root/target")

            write_archive(archive_path, populate)
            with archive_path.open("rb") as archive, patch(
                "materialize_sources.os.link"
            ) as create_hardlink:
                materialize_sources._extract_tar(
                    {"id": "example", "size": archive_path.stat().st_size},
                    archive,
                    destination,
                    link_mode="portable-copy",
                )
            create_hardlink.assert_not_called()
            self.assertEqual(b"locked\n", (destination / "link").read_bytes())
            self.assertNotEqual(
                (destination / "target").stat().st_ino,
                (destination / "link").stat().st_ino,
            )

    def test_destination_rejects_unrelated_nested_source(self) -> None:
        sources = [
            source("mpv-android", "root.tar.gz"),
            source("parent", "parent.tar.gz"),
            source("parent/nested", "nested.tar.gz"),
        ]
        with self.assertRaisesRegex(
            source_tool.SourceToolError, "nested below unrelated source"
        ) as raised:
            materialize_sources.plan_destinations(sources)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_committed_destination_plan_matches_upstream_workspace(self) -> None:
        _, sources = source_tool.load_manifest(REPOSITORY_ROOT / "native" / "source-manifest.toml")
        destinations = materialize_sources.plan_destinations(sources)
        self.assertEqual(PurePosixPath("."), destinations["mpv-android"])
        self.assertEqual(
            PurePosixPath("buildscripts/deps/freetype2"), destinations["freetype"]
        )
        self.assertEqual(
            PurePosixPath("buildscripts/deps/unibreak"), destinations["libunibreak"]
        )
        self.assertEqual(
            PurePosixPath("buildscripts/sdk/bin/gas-preprocessor.pl"),
            destinations["gas-preprocessor"],
        )
        self.assertEqual(
            PurePosixPath("buildscripts/deps/libplacebo/3rdparty/fast_float"),
            destinations["fast-float"],
        )

    def test_destination_rejects_case_collision_from_parent_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "BuildScripts").mkdir()
            with self.assertRaisesRegex(
                source_tool.SourceToolError, "case/Unicode collision"
            ) as raised:
                materialize_sources._ensure_no_symlink_ancestor(
                    workspace,
                    workspace / "buildscripts" / "deps" / "example",
                )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_destination_rejects_static_symlink_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "link"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"host cannot create test symlink: {error}")
            with self.assertRaisesRegex(
                source_tool.SourceToolError, "traverses symbolic link"
            ) as raised:
                materialize_sources._ensure_no_symlink_ancestor(
                    root,
                    link / "nested" / "source",
                )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_workspace_parent_must_resolve_to_literal_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "materialize_sources._same_path", return_value=False
        ):
            workspace = Path(directory) / "workspace"
            with self.assertRaisesRegex(
                source_tool.SourceToolError, "real directory"
            ) as raised:
                materialize_sources._require_real_workspace_parent(workspace)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_publish_refuses_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "staging"
            destination = root / "workspace"
            source_path.mkdir()
            destination.mkdir()
            (source_path / "source-marker").write_bytes(b"source")
            (destination / "destination-marker").write_bytes(b"destination")
            with self.assertRaisesRegex(
                source_tool.SourceToolError, "appeared during materialization"
            ) as raised:
                materialize_sources._rename_no_replace(source_path, destination)
            self.assertTrue((source_path / "source-marker").is_file())
            self.assertTrue((destination / "destination-marker").is_file())
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_materialize_refuses_existing_workspace_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.toml"
            manifest.write_text("locked\n", encoding="utf-8")
            cache = root / "cache"
            cache.mkdir()
            workspace = root / "workspace"
            workspace.mkdir()
            marker = workspace / "marker"
            marker.write_bytes(b"keep")
            with patch("materialize_sources.source_tool.load_manifest") as load:
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "refusing to overwrite"
                ) as raised:
                    materialize_sources.materialize(manifest, cache, workspace)
            load.assert_not_called()
            self.assertEqual(b"keep", marker.read_bytes())
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_portable_copy_refuses_canonical_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.toml"
            manifest.write_bytes(b"locked manifest\n")
            cache = root / "cache"
            cache.mkdir()
            workspace = root / "workspace"
            with (
                patch.object(materialize_sources, "DEFAULT_WORKSPACE", workspace),
                patch("materialize_sources.source_tool.load_manifest") as load_manifest,
                self.assertRaisesRegex(
                    source_tool.SourceToolError,
                    "inspection-only.*non-canonical",
                ) as raised,
            ):
                materialize_sources.materialize(
                    manifest,
                    cache,
                    workspace,
                    link_mode="portable-copy",
                )
            load_manifest.assert_not_called()
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_materialize_parent_children_and_receipt_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.toml"
            manifest.write_bytes(b"locked manifest\n")
            cache = root / "cache"
            cache.mkdir()
            workspace = root / "workspace"

            def root_archive(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/LICENSE")
                add_symlink(archive, "root/LICENSE-LINK", "LICENSE")

            def parent_archive(archive: tarfile.TarFile) -> None:
                add_directory(archive, "parent")
                add_file(archive, "parent/LICENSE")
                add_directory(archive, "parent/subprojects")
                add_directory(archive, "parent/subprojects/dlg")

            def child_archive(archive: tarfile.TarFile) -> None:
                add_directory(archive, "child")
                add_file(archive, "child/LICENSE")
                add_file(archive, "child/source.c")

            write_archive(cache / "root.tar.gz", root_archive)
            write_archive(cache / "parent.tar.gz", parent_archive)
            write_archive(cache / "child.tar.gz", child_archive)
            (cache / "gas.pl").write_bytes(b"#!/usr/bin/env perl\n")

            gas_source = source(
                "gas-preprocessor",
                "gas.pl",
                license_file="gas-preprocessor.pl",
            )
            gas_source["size"] = (cache / "gas.pl").stat().st_size
            gas_source["sha256"] = hashlib.sha256(
                (cache / "gas.pl").read_bytes()
            ).hexdigest()
            sources = [
                source("dlg", "child.tar.gz", parent="freetype", destination="subprojects/dlg"),
                source("freetype", "parent.tar.gz"),
                gas_source,
                source("mpv-android", "root.tar.gz"),
            ]
            data = {
                "project": {
                    "upstreamRelease": "test",
                    "upstreamRevision": "a" * 40,
                    "nativeApi": 26,
                    "abis": ["arm64-v8a", "x86_64"],
                    "pageSizeBytes": 16384,
                }
            }
            with (
                patch(
                    "materialize_sources.source_tool.load_manifest",
                    return_value=(data, sources),
                ),
                patch("materialize_sources.source_tool.verify_cache"),
                patch("materialize_sources.source_tool.open_verified_archive") as open_archive,
            ):
                open_archive.side_effect = lambda source, path: path.open("rb")
                materialize_sources.materialize(
                    manifest,
                    cache,
                    workspace,
                    link_mode="portable-copy",
                )

            child = workspace / "buildscripts" / "deps" / "freetype2" / "subprojects" / "dlg"
            self.assertEqual(b"content\n", (child / "source.c").read_bytes())
            self.assertTrue(
                (workspace / "buildscripts" / "sdk" / "bin" / "gas-preprocessor.pl").is_file()
            )
            receipt = json.loads(
                (workspace / materialize_sources.RECEIPT_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual("portable-copy", receipt["linkMode"])
            self.assertEqual(4, len(receipt["sources"]))
            self.assertEqual(materialize_sources.TREE_DIGEST_FORMAT, receipt["tree"]["format"])
            self.assertEqual(64, len(receipt["tree"]["sha256"]))
            self.assertEqual(
                materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                (workspace / materialize_sources.RECEIPT_NAME).stat().st_mtime_ns,
            )
            self.assertEqual(
                materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                workspace.stat().st_mtime_ns,
            )
            self.assertEqual([], list(root.glob(".workspace.*.part")))
            with (
                patch(
                    "materialize_sources.source_tool.load_manifest",
                    return_value=(data, sources),
                ),
                patch("materialize_sources.source_tool.verify_cache"),
                patch("materialize_sources.source_tool.open_verified_archive") as verify_archive,
            ):
                verify_archive.side_effect = lambda source, path: path.open("rb")
                materialize_sources.verify_materialized_workspace(
                    manifest,
                    cache,
                    workspace,
                    require_preserve=False,
                )
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "require a preserve-mode"
                ):
                    materialize_sources.verify_materialized_workspace(
                        manifest,
                        cache,
                        workspace,
                    )
                receipt_path = workspace / materialize_sources.RECEIPT_NAME
                forged_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                forged_receipt["linkMode"] = "preserve"
                receipt_path.write_text(
                    json.dumps(forged_receipt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                materialize_sources._normalize_receipt_metadata(receipt_path)
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "entry type mismatch"
                ):
                    materialize_sources.verify_materialized_workspace(
                        manifest,
                        cache,
                        workspace,
                    )
                forged_receipt["linkMode"] = "portable-copy"

                def forge_current_tree() -> None:
                    forged_receipt["tree"] = materialize_sources._tree_digest(workspace)
                    receipt_path.write_text(
                        json.dumps(forged_receipt, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    materialize_sources._normalize_receipt_metadata(receipt_path)

                source_path = child / "source.c"
                source_path.write_bytes(b"changed\n")
                source_path.chmod(0o644)
                os.utime(
                    source_path,
                    ns=(
                        materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                        materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                    ),
                )
                forge_current_tree()
                with self.assertRaisesRegex(
                    source_tool.SourceToolError,
                    "file content mismatch",
                ) as raised:
                    materialize_sources.verify_materialized_workspace(
                        manifest,
                        cache,
                        workspace,
                        require_preserve=False,
                    )

                source_path.write_bytes(b"content\n")
                extra_path = child / "extra.c"
                extra_path.write_bytes(b"extra\n")
                extra_path.chmod(0o644)
                materialize_sources._normalize_workspace_directories(workspace)
                os.utime(
                    source_path,
                    ns=(
                        materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                        materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                    ),
                )
                os.utime(
                    extra_path,
                    ns=(
                        materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                        materialize_sources.NORMALIZED_GENERATED_MTIME_NS,
                    ),
                )
                forge_current_tree()
                with self.assertRaisesRegex(
                    source_tool.SourceToolError,
                    "unlocked entry",
                ):
                    materialize_sources.verify_materialized_workspace(
                        manifest,
                        cache,
                        workspace,
                        require_preserve=False,
                    )

                extra_path.unlink()
                source_path.unlink()
                materialize_sources._normalize_workspace_directories(workspace)
                forge_current_tree()
                with self.assertRaisesRegex(
                    source_tool.SourceToolError,
                    "missing locked entry",
                ):
                    materialize_sources.verify_materialized_workspace(
                        manifest,
                        cache,
                        workspace,
                        require_preserve=False,
                    )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_materialize_failure_removes_staging_and_leaves_final_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.toml"
            manifest.write_bytes(b"locked manifest\n")
            cache = root / "cache"
            cache.mkdir()
            workspace = root / "workspace"
            archive_path = cache / "root.tar.gz"

            def unsafe_archive(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/../escape")

            write_archive(archive_path, unsafe_archive)
            sources = [source("mpv-android", archive_path.name)]
            data = {
                "project": {
                    "upstreamRelease": "test",
                    "upstreamRevision": "a" * 40,
                    "nativeApi": 26,
                    "abis": ["arm64-v8a", "x86_64"],
                    "pageSizeBytes": 16384,
                }
            }
            with (
                patch(
                    "materialize_sources.source_tool.load_manifest",
                    return_value=(data, sources),
                ),
                patch("materialize_sources.source_tool.verify_cache"),
                patch("materialize_sources.source_tool.open_verified_archive") as open_archive,
            ):
                open_archive.side_effect = lambda source, path: path.open("rb")
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    materialize_sources.materialize(manifest, cache, workspace)
            self.assertFalse(workspace.exists())
            self.assertEqual([], list(root.glob(".workspace.*.part")))
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_parent_fsync_failure_reports_published_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.toml"
            manifest.write_bytes(b"locked manifest\n")
            cache = root / "cache"
            cache.mkdir()
            workspace = root / "workspace"
            archive_path = cache / "root.tar.gz"

            def root_archive(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/LICENSE")

            write_archive(archive_path, root_archive)
            sources = [source("mpv-android", archive_path.name)]
            data = {
                "project": {
                    "upstreamRelease": "test",
                    "upstreamRevision": "a" * 40,
                    "nativeApi": 26,
                    "abis": ["arm64-v8a", "x86_64"],
                    "pageSizeBytes": 16384,
                }
            }
            durability_error = source_tool.SourceToolError(
                "simulated parent fsync failure",
                source_tool.EXIT_INTEGRITY,
            )
            with (
                patch(
                    "materialize_sources.source_tool.load_manifest",
                    return_value=(data, sources),
                ),
                patch("materialize_sources.source_tool.verify_cache"),
                patch("materialize_sources.source_tool.open_verified_archive") as open_archive,
                patch("materialize_sources._fsync_workspace_directories"),
                patch("materialize_sources._fsync_directory", side_effect=durability_error),
            ):
                open_archive.side_effect = lambda source, path: path.open("rb")
                with self.assertRaisesRegex(
                    source_tool.SourceToolError,
                    "workspace was published.*durability could not be confirmed",
                ) as raised:
                    materialize_sources.materialize(
                        manifest,
                        cache,
                        workspace,
                        link_mode="portable-copy",
                    )

            self.assertTrue(workspace.is_dir())
            self.assertTrue((workspace / "LICENSE").is_file())
            self.assertEqual([], list(root.glob(".workspace.*.part")))
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_materialize_rejects_case_variant_of_receipt_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.toml"
            manifest.write_bytes(b"locked manifest\n")
            cache = root / "cache"
            cache.mkdir()
            workspace = root / "workspace"
            archive_path = cache / "root.tar.gz"

            def colliding_archive(archive: tarfile.TarFile) -> None:
                add_directory(archive, "root")
                add_file(archive, "root/LICENSE")
                add_file(archive, "root/ZIV-NATIVE-MATERIALIZATION.JSON")

            write_archive(archive_path, colliding_archive)
            sources = [source("mpv-android", archive_path.name)]
            data = {
                "project": {
                    "upstreamRelease": "test",
                    "upstreamRevision": "a" * 40,
                    "nativeApi": 26,
                    "abis": ["arm64-v8a", "x86_64"],
                    "pageSizeBytes": 16384,
                }
            }
            with (
                patch(
                    "materialize_sources.source_tool.load_manifest",
                    return_value=(data, sources),
                ),
                patch("materialize_sources.source_tool.verify_cache"),
                patch("materialize_sources.source_tool.open_verified_archive") as open_archive,
            ):
                open_archive.side_effect = lambda source, path: path.open("rb")
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "collides with the materialization receipt"
                ) as raised:
                    materialize_sources.materialize(manifest, cache, workspace)
            self.assertFalse(workspace.exists())
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_real_locked_archives_cover_known_link_and_root_shapes(self) -> None:
        cache = REPOSITORY_ROOT / "native" / "cache" / "sources"
        with tarfile.open(cache / "mbedtls-3.6.7.tar.bz2", mode="r:*") as archive:
            root, _ = materialize_sources._plan_tar(archive, "mbedtls")
            self.assertEqual("mbedtls-3.6.7", root)

        expected = {
            "harfbuzz-14.2.1.tar.xz": (PurePosixPath("CLAUDE.md"), "AGENTS.md"),
            "mpv-android-ad98fc97ff1d25e217389e7238a1abda8c13a6c4.tar.gz": (
                PurePosixPath("app/src/main/jniLibs"),
                "libs/",
            ),
        }
        for archive_name, (expected_path, expected_target) in expected.items():
            with self.subTest(archive=archive_name), tarfile.open(
                cache / archive_name, mode="r:*"
            ) as archive:
                _, members = materialize_sources._plan_tar(archive, archive_name)
                links = [member for member in members if member.kind == "symlink"]
                self.assertEqual(1, len(links))
                self.assertEqual(expected_path, links[0].path)
                self.assertEqual(expected_target, links[0].info.linkname)


if __name__ == "__main__":
    unittest.main()
