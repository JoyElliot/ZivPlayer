# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import environment_tool  # noqa: E402
import source_tool  # noqa: E402


BASE_ROWS = [("base", "amd64", "1.0")]
PACKAGE_ROWS: list[dict[str, object]] = [
    {
        "package": "tool",
        "architecture": "amd64",
        "version": "2.0",
        "archive": "tool.deb",
        "size": 1,
        "sha256": "0" * 64,
    }
]


def synthetic_jks(timestamp_millis: int, *, tag: int = 2) -> bytes:
    alias = b"test-root"
    certificate_type = b"X.509"
    certificate = b"\x30\x03\x02\x01\x01"
    body = bytearray()
    body.extend(struct.pack(">Iii", 0xFEEDFEED, 2, 1))
    body.extend(struct.pack(">iH", tag, len(alias)))
    body.extend(alias)
    body.extend(struct.pack(">qH", timestamp_millis, len(certificate_type)))
    body.extend(certificate_type)
    body.extend(struct.pack(">i", len(certificate)))
    body.extend(certificate)
    digest = hashlib.sha1(
        "changeit".encode("utf-16-be") + b"Mighty Aphrodite" + body,
        usedforsecurity=False,
    ).digest()
    return bytes(body) + digest


def status_record(
    package: str,
    architecture: str,
    version: str,
    *,
    status: str = "install ok installed",
    extra: str = "",
) -> bytes:
    suffix = f"{extra}\n" if extra else ""
    return (
        f"Package: {package}\n"
        f"Status: {status}\n"
        f"Architecture: {architecture}\n"
        f"Version: {version}\n"
        f"{suffix}\n"
    ).encode("ascii")


class EnvironmentToolTest(unittest.TestCase):
    def test_tzdata_transcript_clock_is_narrowly_canonicalized(self) -> None:
        first = (
            b"Current default time zone: 'Etc/UTC'\n"
            b"Local time is now:      Mon Aug 31 02:21:30 UTC 2026.\n"
            b"Universal Time is now:  Mon Aug 31 02:21:30 UTC 2026.\n"
        )
        second = first.replace(b"02:21:30", b"02:26:23")
        self.assertEqual(
            environment_tool._canonicalize_installer_stderr(first),
            environment_tool._canonicalize_installer_stderr(second),
        )
        with self.assertRaises(source_tool.SourceToolError):
            environment_tool._canonicalize_installer_stderr(
                b"Local time is now:      Mon Aug 31 02:21:30 UTC 2026.\n"
            )

    def test_preflight_tree_limits_entry_count_before_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(3):
                (root / str(index)).write_bytes(b"")
            with patch("environment_tool.MAX_TREE_ENTRIES", 2):
                with self.assertRaises(source_tool.SourceToolError):
                    environment_tool._preflight_tree_limits(root)

    def test_jks_normalization_is_deterministic_and_preserves_payload(self) -> None:
        first, first_record = environment_tool._jks_generated_file_record(
            synthetic_jks(1_788_142_897_637),
            normalize=True,
        )
        second, second_record = environment_tool._jks_generated_file_record(
            synthetic_jks(1_788_143_190_502),
            normalize=True,
        )
        self.assertEqual(first, second)
        self.assertEqual(first_record, second_record)
        self.assertEqual(1, first_record["entries"])
        self.assertEqual(
            environment_tool.NORMALIZED_TIMESTAMP_MILLIS,
            first_record["timestampMillis"],
        )
        verified, verified_record = environment_tool._jks_generated_file_record(
            first,
            normalize=False,
        )
        self.assertEqual(first, verified)
        self.assertEqual(first_record, verified_record)

    def test_jks_normalization_rejects_bad_integrity_shape_and_timestamp(self) -> None:
        original = synthetic_jks(1_788_142_897_637)
        cases = (
            original[:-1] + bytes([original[-1] ^ 1]),
            synthetic_jks(1_788_142_897_637, tag=1),
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(source_tool.SourceToolError):
                environment_tool._jks_generated_file_record(raw, normalize=True)
        with self.assertRaises(source_tool.SourceToolError) as raised:
            environment_tool._jks_generated_file_record(original, normalize=False)
        self.assertIn("timestamps", str(raised.exception))

    def test_projection_is_exact_sorted_union(self) -> None:
        raw = status_record("tool", "amd64", "2.0") + status_record(
            "base", "amd64", "1.0"
        )
        projection = environment_tool._installed_projection(
            raw,
            BASE_ROWS,
            PACKAGE_ROWS,
        )
        self.assertEqual(
            b"package\tarchitecture\tversion\n"
            b"base\tamd64\t1.0\n"
            b"tool\tamd64\t2.0\n",
            projection,
        )

    def test_projection_rejects_missing_extra_version_and_status_drift(self) -> None:
        cases = (
            status_record("base", "amd64", "1.0"),
            status_record("base", "amd64", "1.0")
            + status_record("tool", "amd64", "2.1"),
            status_record("base", "amd64", "1.0")
            + status_record("tool", "amd64", "2.0")
            + status_record("extra", "amd64", "1"),
            status_record("base", "amd64", "1.0")
            + status_record("tool", "amd64", "2.0", status="install ok unpacked"),
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(source_tool.SourceToolError) as raised:
                environment_tool._installed_projection(raw, BASE_ROWS, PACKAGE_ROWS)
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_projection_rejects_duplicate_and_pending_trigger(self) -> None:
        duplicate = (
            status_record("base", "amd64", "1.0")
            + status_record("base", "amd64", "1.0")
            + status_record("tool", "amd64", "2.0")
        )
        pending = status_record("base", "amd64", "1.0") + status_record(
            "tool",
            "amd64",
            "2.0",
            extra="Triggers-Pending: ldconfig",
        )
        for raw in (duplicate, pending):
            with self.assertRaises(source_tool.SourceToolError):
                environment_tool._installed_projection(raw, BASE_ROWS, PACKAGE_ROWS)

    def test_projection_rejects_base_and_selected_overlap(self) -> None:
        rows = [dict(PACKAGE_ROWS[0], package="base", version="1.0")]
        with self.assertRaises(source_tool.SourceToolError) as raised:
            environment_tool._expected_projection(BASE_ROWS, rows)
        self.assertIn("overlaps", str(raised.exception))

    def test_canonical_json_rejects_duplicate_and_noncanonical_bytes(self) -> None:
        canonical = environment_tool._canonical_json({"b": 2, "a": 1})
        self.assertEqual({"a": 1, "b": 2}, environment_tool._read_json_bytes(canonical, "test"))
        for raw in (b'{"a":1,"a":2}\n', b'{"a":1}\n'):
            with self.assertRaises(source_tool.SourceToolError):
                environment_tool._read_json_bytes(raw, "test")

    def test_evidence_path_rejects_traversal_and_controls(self) -> None:
        for value in ("../escape", "/absolute", "a\\b", "a/./b", "bad\nname"):
            with self.subTest(value=value), self.assertRaises(source_tool.SourceToolError):
                environment_tool._safe_evidence_name(value)
        self.assertEqual(
            "helpers/native/tool.py",
            environment_tool._safe_evidence_name("helpers/native/tool.py").as_posix(),
        )

    def test_mount_inventory_filters_plain_directory_descendants(self) -> None:
        payload = {
            "filesystems": [
                {"target": "/", "fstype": "ext4", "options": "rw"},
                {
                    "target": "/var/tmp/root/child",
                    "fstype": "tmpfs",
                    "options": "rw,nosuid",
                },
            ]
        }
        result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(payload).encode("utf-8"),
                "stderr": b"",
            },
        )()
        with patch("environment_tool.subprocess.run", return_value=result):
            mounts = environment_tool._mounts_below(Path("/var/tmp/root"))
        self.assertIn(b"/var/tmp/root/child", mounts)
        with patch("environment_tool.subprocess.run", return_value=result):
            with self.assertRaises(source_tool.SourceToolError):
                environment_tool._assert_no_nested_mounts(Path("/var/tmp/root"))

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "beneath-path tests require Linux root",
    )
    def test_generated_file_mutations_reject_symlink_ancestors(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            parent = Path(directory)
            root = parent / "root"
            outside = parent / "outside"
            root.mkdir(mode=0o700)
            outside.mkdir(mode=0o700)
            cacerts = outside / "cacerts"
            original = synthetic_jks(1_788_142_897_637)
            cacerts.write_bytes(original)
            cacerts.chmod(0o644)
            os.symlink(outside, root / "etc")
            with self.assertRaises(source_tool.SourceToolError):
                environment_tool._rewrite_cacerts(root)
            self.assertEqual(original, cacerts.read_bytes())

            (root / "etc").unlink()
            aux_parent = outside / "cache" / "ldconfig"
            aux_parent.mkdir(parents=True)
            aux_cache = aux_parent / "aux-cache"
            aux_cache.write_bytes(b"host-data")
            aux_cache.chmod(0o600)
            os.symlink(outside, root / "var")
            with self.assertRaises(source_tool.SourceToolError):
                environment_tool._remove_ldconfig_aux_cache(root)
            self.assertEqual(b"host-data", aux_cache.read_bytes())

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "cgroup lifecycle test requires Linux root",
    )
    def test_installer_cgroup_is_applied_and_removed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory)
            rootfs = root / "rootfs"
            rootfs.mkdir(mode=0o700)
            wrapper = root / "wrapper.sh"
            wrapper.write_text("#!/bin/sh\nprintf 'sandbox-ok\\n'\n", encoding="ascii")
            wrapper.chmod(0o700)
            before = {
                path.name
                for path in environment_tool.CGROUP_ROOT.iterdir()
                if environment_tool.CGROUP_NAME_PATTERN.fullmatch(path.name)
            }
            stdout, stderr, policy = environment_tool._run_installer(
                rootfs,
                wrapper,
                root / "stdout",
                root / "stderr",
                60,
            )
            after = {
                path.name
                for path in environment_tool.CGROUP_ROOT.iterdir()
                if environment_tool.CGROUP_NAME_PATTERN.fullmatch(path.name)
            }
            self.assertEqual(b"sandbox-ok\n", stdout)
            self.assertEqual(b"", stderr)
            self.assertEqual(environment_tool._sandbox_policy_record(), policy)
            self.assertEqual(before, after)

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "installer signal-race test requires Linux root",
    )
    def test_installer_signal_after_spawn_preserves_interrupt(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory)
            rootfs = root / "rootfs"
            rootfs.mkdir(mode=0o700)
            wrapper = root / "wrapper.sh"
            wrapper.write_text("#!/bin/sh\nexec /bin/sleep 30\n", encoding="ascii")
            wrapper.chmod(0o700)
            before = {
                path.name
                for path in environment_tool.CGROUP_ROOT.iterdir()
                if environment_tool.CGROUP_NAME_PATTERN.fullmatch(path.name)
            }
            parent_pid = os.getpid()
            original_sigmask = environment_tool.signal.pthread_sigmask
            interrupted = False

            def interrupt_first_parent_unmask(how, mask):
                nonlocal interrupted
                if (
                    os.getpid() == parent_pid
                    and how == environment_tool.signal.SIG_SETMASK
                    and not interrupted
                ):
                    interrupted = True
                    raise KeyboardInterrupt
                return original_sigmask(how, mask)

            with patch(
                "environment_tool.signal.pthread_sigmask",
                side_effect=interrupt_first_parent_unmask,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    environment_tool._run_installer(
                        rootfs,
                        wrapper,
                        root / "stdout",
                        root / "stderr",
                        60,
                    )
            after = {
                path.name
                for path in environment_tool.CGROUP_ROOT.iterdir()
                if environment_tool.CGROUP_NAME_PATTERN.fullmatch(path.name)
            }
            self.assertTrue(interrupted)
            self.assertEqual(before, after)

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "cgroup orphan test requires Linux root",
    )
    def test_installer_cgroup_kills_detached_descendant(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory)
            rootfs = root / "rootfs"
            rootfs.mkdir(mode=0o700)
            wrapper = root / "wrapper.sh"
            wrapper.write_text(
                "#!/bin/sh\n"
                "(/usr/bin/setsid /bin/sh -c 'exec >/dev/null 2>&1; exec /bin/sleep 30') &\n"
                "exit 0\n",
                encoding="ascii",
            )
            wrapper.chmod(0o700)
            before = {
                path.name
                for path in environment_tool.CGROUP_ROOT.iterdir()
                if environment_tool.CGROUP_NAME_PATTERN.fullmatch(path.name)
            }
            with self.assertRaises(source_tool.SourceToolError) as raised:
                environment_tool._run_installer(
                    rootfs,
                    wrapper,
                    root / "stdout",
                    root / "stderr",
                    60,
                )
            after = {
                path.name
                for path in environment_tool.CGROUP_ROOT.iterdir()
                if environment_tool.CGROUP_NAME_PATTERN.fullmatch(path.name)
            }
            self.assertIn("descendant", str(raised.exception))
            self.assertEqual(before, after)

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "mount leak test requires Linux root",
    )
    def test_mount_inventory_detects_real_nested_mount_below_plain_root(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "root"
            child = root / "child"
            child.mkdir(parents=True)
            mounted = False
            try:
                subprocess.run(
                    [
                        "/usr/bin/mount",
                        "-t",
                        "tmpfs",
                        "tmpfs",
                        str(child),
                        "-o",
                        "nosuid,nodev,noexec,size=1m",
                    ],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                mounted = True
                with self.assertRaises(source_tool.SourceToolError):
                    environment_tool._assert_no_nested_mounts(root)
            finally:
                if mounted:
                    subprocess.run(
                        ["/usr/bin/umount", str(child)],
                        check=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "installed-tree metadata tests require Linux root",
    )
    def test_tree_digest_distinguishes_hardlink_from_copy(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            parent = Path(directory)
            roots = []
            for name, hardlink in (("hard", True), ("copy", False)):
                root = parent / name
                root.mkdir(mode=0o700)
                (root / "a").write_bytes(b"same")
                if hardlink:
                    os.link(root / "a", root / "b")
                else:
                    (root / "b").write_bytes(b"same")
                environment_tool._normalize_tree(root)
                roots.append(environment_tool._scan_tree(root))
            self.assertNotEqual(roots[0]["sha256"], roots[1]["sha256"])
            self.assertEqual(1, roots[0]["hardlinks"])
            self.assertEqual(0, roots[1]["hardlinks"])

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "installed-tree metadata tests require Linux root",
    )
    def test_tree_digest_excludes_only_canonical_receipt(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "root"
            root.mkdir(mode=0o700)
            (root / "file").write_bytes(b"payload")
            environment_tool._normalize_tree(root)
            before = environment_tool._scan_tree(root)
            receipt = root / environment_tool.RECEIPT_NAME
            receipt.write_bytes(b"receipt")
            os.chown(receipt, 0, 0)
            receipt.chmod(0o644)
            os.utime(
                receipt,
                ns=(
                    environment_tool.NORMALIZED_MTIME_NS,
                    environment_tool.NORMALIZED_MTIME_NS,
                ),
            )
            os.utime(
                root,
                ns=(
                    environment_tool.NORMALIZED_MTIME_NS,
                    environment_tool.NORMALIZED_MTIME_NS,
                ),
            )
            self.assertEqual(before, environment_tool._scan_tree(root))
            receipt.unlink()
            collision = root / environment_tool.RECEIPT_NAME.upper()
            collision.write_bytes(b"collision")
            environment_tool._normalize_tree(root)
            with self.assertRaises(source_tool.SourceToolError):
                environment_tool._scan_tree(root)

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "installed-tree metadata tests require Linux root",
    )
    def test_tree_digest_supports_a_distinct_canonical_receipt_name(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "root"
            root.mkdir(mode=0o700)
            (root / "file").write_bytes(b"payload")
            environment_tool._normalize_tree(root)
            receipt_name = "ziv-sdk-projection.json"
            before = environment_tool._scan_tree(root, receipt_name=receipt_name)
            receipt = root / receipt_name
            receipt.write_bytes(b"receipt")
            os.chown(receipt, 0, 0)
            receipt.chmod(0o644)
            environment_tool._normalize_tree(root)
            self.assertEqual(
                before,
                environment_tool._scan_tree(root, receipt_name=receipt_name),
            )
            receipt.unlink()
            collision = root / receipt_name.upper()
            collision.write_bytes(b"collision")
            environment_tool._normalize_tree(root)
            with self.assertRaisesRegex(source_tool.SourceToolError, "receipt-name"):
                environment_tool._scan_tree(root, receipt_name=receipt_name)

    def test_tree_digest_rejects_unsafe_custom_receipt_names(self) -> None:
        for name in ("", "a/b", "a\\b", ".", "line\nfeed"):
            with self.subTest(name=name), self.assertRaises(source_tool.SourceToolError) as raised:
                environment_tool._scan_tree(Path("missing"), receipt_name=name)
            self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "installed-tree metadata tests require Linux root",
    )
    def test_tree_digest_rejects_relative_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "root"
            root.mkdir(mode=0o700)
            os.symlink("../../outside", root / "escape")
            environment_tool._normalize_tree(root)
            with self.assertRaises(source_tool.SourceToolError) as raised:
                environment_tool._scan_tree(root)
            self.assertIn("escapes", str(raised.exception))

    @unittest.skipUnless(
        sys.platform == "linux"
        and hasattr(os, "setxattr")
        and hasattr(os, "geteuid")
        and os.geteuid() == 0,
        "xattr test requires Linux root",
    )
    def test_tree_digest_binds_extended_attribute_value(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "root"
            root.mkdir(mode=0o700)
            path = root / "file"
            path.write_bytes(b"payload")
            try:
                os.setxattr(path, "user.ziv", b"one")
            except OSError as error:
                self.skipTest(f"xattrs unavailable: {error}")
            environment_tool._normalize_tree(root)
            first = environment_tool._scan_tree(root)["sha256"]
            os.setxattr(path, "user.ziv", b"two")
            second = environment_tool._scan_tree(root)["sha256"]
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
