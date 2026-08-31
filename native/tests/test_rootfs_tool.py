# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gzip
import hashlib
import io
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import rootfs_tool  # noqa: E402
import source_tool  # noqa: E402


def layer_payload(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for info, payload in members:
            archive.addfile(info, io.BytesIO(payload) if payload is not None else None)
    return gzip.compress(stream.getvalue(), mtime=0)


def directory(name: str, *, mode: int = 0o755) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.uid = 0
    info.gid = 0
    return info, None


def regular(
    name: str,
    payload: bytes,
    *,
    mode: int = 0o644,
) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.size = len(payload)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    return info, payload


def link(
    name: str,
    target: str,
    *,
    hard: bool,
    mode: int = 0o644,
) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE if hard else tarfile.SYMTYPE
    info.linkname = target
    info.mode = mode
    info.uid = 0
    info.gid = 0
    return info, None


class RootfsToolTest(unittest.TestCase):
    def plan(
        self,
        members: list[tuple[tarfile.TarInfo, bytes | None]],
    ) -> list[rootfs_tool.LayerEntry]:
        payload = layer_payload(members)
        with tempfile.TemporaryDirectory() as directory_name:
            cache = Path(directory_name)
            archive = cache / "rootfs.tar.gz"
            archive.write_bytes(payload)
            entry: dict[str, object] = {
                "id": "test-layer",
                "archive": archive.name,
                "size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            }
            return rootfs_tool._plan_layer(entry, cache)

    def test_plan_accepts_root_relative_symlink_and_hardlink(self) -> None:
        entries = self.plan(
            [
                directory("bin"),
                directory("etc"),
                regular("etc/config", b"locked\n"),
                link("etc/config-hard", "etc/config", hard=True),
                link("bin/tool", "/etc/config", hard=False, mode=0o777),
            ]
        )
        by_path = {entry.path.as_posix(): entry for entry in entries}
        self.assertEqual("/etc/config", by_path["bin/tool"].link_text)
        self.assertEqual("etc/config", by_path["etc/config-hard"].link_text)
        self.assertEqual(
            hashlib.sha256(b"locked\n").hexdigest(),
            by_path["etc/config"].sha256,
        )
        tree = rootfs_tool._tree_record(entries)
        self.assertEqual(5, tree["entries"])
        self.assertEqual(1, tree["files"])
        self.assertEqual(1, tree["hardlinks"])
        self.assertEqual(1, tree["symlinks"])

    def test_plan_rejects_traversal_whiteout_and_special_entries(self) -> None:
        cases: list[tuple[str, list[tuple[tarfile.TarInfo, bytes | None]], str]] = []
        cases.append(("traversal", [regular("../escape", b"x")], "non-canonical"))
        cases.append(("whiteout", [regular("etc/.wh.shadow", b"")], "whiteout"))
        fifo = tarfile.TarInfo("run/fifo")
        fifo.type = tarfile.FIFOTYPE
        fifo.mode = 0o600
        fifo.uid = 0
        fifo.gid = 0
        cases.append(("fifo", [directory("run"), (fifo, None)], "unsupported"))
        for label, members, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                source_tool.SourceToolError,
                message,
            ):
                self.plan(members)

    def test_plan_rejects_escaping_symlink_and_missing_hardlink(self) -> None:
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "escapes the rootfs",
        ):
            self.plan(
                [
                    directory("bin"),
                    link("bin/tool", "../../outside", hard=False, mode=0o777),
                ]
            )
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "non-canonical symbolic-link target",
        ):
            self.plan(
                [
                    directory("bin"),
                    link("bin/tool", "/etc//config", hard=False, mode=0o777),
                ]
            )
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "must target a file",
        ):
            self.plan(
                [
                    directory("etc"),
                    link("etc/config-hard", "etc/missing", hard=True),
                ]
            )

    def test_plan_rejects_case_and_unicode_collisions(self) -> None:
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "case/Unicode path collision",
        ):
            self.plan(
                [
                    directory("etc"),
                    regular("etc/Config", b"a"),
                    regular("etc/config", b"b"),
                ]
            )
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "case/Unicode prefix collision",
        ):
            self.plan(
                [
                    regular("Tree/one", b"a"),
                    regular("tree/two", b"b"),
                ]
            )
        decomposed = "etc/e\u0301"
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "non-canonical rootfs path",
        ):
            self.plan([directory("etc"), regular(decomposed, b"x")])

    def test_plan_rejects_receipt_name_collision(self) -> None:
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "canonical base receipt",
        ):
            self.plan([regular("ZIV-TOOLCHAIN-BASE.JSON", b"spoof")])
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "canonical base receipt",
        ):
            self.plan([regular("ziv-toolchain-base.json/payload", b"spoof")])

    def test_path_and_mode_limits_are_enforced_before_extraction(self) -> None:
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "non-canonical rootfs path",
        ):
            rootfs_tool._safe_member_path(
                "a" * (rootfs_tool.MAX_COMPONENT_BYTES + 1),
                "test member",
                directory=False,
            )
        path_max_member = "/".join(["a" * 255] * 16)
        self.assertEqual(4095, len(path_max_member.encode("utf-8")))
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "non-canonical rootfs path",
        ):
            rootfs_tool._safe_member_path(
                path_max_member,
                "test member",
                directory=False,
            )

        invalid_mode = tarfile.TarInfo("etc")
        invalid_mode.type = tarfile.DIRTYPE
        invalid_mode.mode = 0o10000
        invalid_mode.uid = 0
        invalid_mode.gid = 0
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "invalid permission mode",
        ):
            rootfs_tool._layer_entry(invalid_mode, 0, None)  # type: ignore[arg-type]
        invalid_mode.mode = -1
        with self.assertRaisesRegex(
            source_tool.SourceToolError,
            "invalid permission mode",
        ):
            rootfs_tool._layer_entry(invalid_mode, 0, None)  # type: ignore[arg-type]

    def test_destination_prefix_is_included_in_path_limit(self) -> None:
        entries = self.plan([regular("a" * 64, b"x")])
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            destination = root / ("a" * 64)
            with patch.object(
                rootfs_tool.os,
                "pathconf",
                return_value=len(os.fsencode(destination)),
                create=True,
            ), self.assertRaisesRegex(
                source_tool.SourceToolError,
                "filesystem path limit",
            ):
                rootfs_tool._require_destination_paths_fit(root, entries)

    def test_tar_trailing_padding_is_bounded(self) -> None:
        with patch.object(
            rootfs_tool,
            "MAX_TAR_TRAILING_BYTES",
            tarfile.BLOCKSIZE - 1,
        ), self.assertRaisesRegex(
            source_tool.SourceToolError,
            "trailing-padding limit",
        ):
            self.plan([regular("file", b"x")])

    def test_ext4_probe_requires_exact_canonical_result(self) -> None:
        file_info = SimpleNamespace(st_mode=stat.S_IFREG | 0o755)
        with (
            patch.object(rootfs_tool.Path, "lstat", return_value=file_info),
            patch.object(rootfs_tool.Path, "is_symlink", return_value=False),
            patch.object(
                rootfs_tool.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    ["findmnt"],
                    0,
                    stdout=b"ext4\n",
                    stderr=b"",
                ),
            ) as run,
        ):
            rootfs_tool._require_ext4(Path(os.sep))
        self.assertEqual("/usr/bin:/bin", run.call_args.kwargs["env"]["PATH"])

        with (
            patch.object(rootfs_tool.Path, "lstat", return_value=file_info),
            patch.object(rootfs_tool.Path, "is_symlink", return_value=False),
            patch.object(
                rootfs_tool.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    ["findmnt"],
                    0,
                    stdout=b"overlay\n",
                    stderr=b"",
                ),
            ),
            self.assertRaisesRegex(source_tool.SourceToolError, "must be on ext4"),
        ):
            rootfs_tool._require_ext4(Path(os.sep))

    def test_unique_prefixes_share_the_global_planned_entry_cap(self) -> None:
        with patch.object(rootfs_tool, "MAX_LAYER_ENTRIES", 2), self.assertRaisesRegex(
            source_tool.SourceToolError,
            "planned-entry limit",
        ):
            self.plan([regular("one/two/file", b"x")])

    def test_manifest_snapshot_digest_binds_the_parsed_bytes(self) -> None:
        _data, _artifacts, _oci, _source, digest = (
            rootfs_tool.toolchain_tool.load_manifest_snapshot(
                rootfs_tool.DEFAULT_MANIFEST,
                source_manifest_path=rootfs_tool.DEFAULT_SOURCE_MANIFEST,
            )
        )
        self.assertEqual(
            hashlib.sha256(rootfs_tool.DEFAULT_MANIFEST.read_bytes()).hexdigest(),
            digest,
        )


if __name__ == "__main__":
    unittest.main()
