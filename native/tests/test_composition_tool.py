# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import signal
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import composition_tool  # noqa: E402
import source_tool  # noqa: E402


VALID_SMOKE = (
    "python\tPython 3.12.3\n"
    "meson\t1.11.0\n"
    "meson_import\t/opt/zivplayer/toolchain/python/site-packages/mesonbuild/__init__.py\n"
    "ninja\t1.11.1\n"
    "pkg_config\t1.8.1\n"
    'java\topenjdk version "17.0.19" 2026-04-21\n'
    "javac\tjavac 17.0.19\n"
    "aapt2\tAndroid Asset Packaging Tool (aapt) 2.20-13193326\n"
    "android_packages\tbuild-tools=36.0.0;platform=36-r02;ndk=29.0.14206865\n"
    "clang_arm64\tAndroid clang version 21.0.0 (locked)\n"
    "clang_x86_64\tAndroid clang version 21.0.0 (locked)\n"
    "elf_arm64\tAArch64;load-align=0x4000\n"
    "elf_x86_64\tAdvanced Micro Devices X86-64;load-align=0x4000\n"
    "isolation\tprivate-namespaces;loopback-only;seccomp;no-caps;read-only-inputs\n"
).encode("utf-8")


def fixture_inputs(base: Path | None = None) -> composition_tool.BoundInputs:
    base = (base or Path(tempfile.gettempdir())).resolve()
    manifest_sha = "1" * 64
    source_sha = "2" * 64
    apt_receipt: dict[str, object] = {
        "schemaVersion": 1,
        "kind": "ziv-toolchain-apt",
        "ready": False,
        "manifestSha256": manifest_sha,
        "sourceManifestSha256": source_sha,
        "tree": {
            "format": "fixture-apt-tree",
            "entries": 3,
            "fileBytes": 7,
            "sha256": "3" * 64,
        },
    }
    sdk_receipt: dict[str, object] = {
        "schemaVersion": 1,
        "kind": "ziv-sdk-projection-receipt-v1",
        "ready": False,
        "toolchainManifestSha256": manifest_sha,
        "sourceManifestSha256": source_sha,
        "mountPath": composition_tool.MOUNT_PATH,
        "tree": {
            "format": "fixture-sdk-tree",
            "entries": 5,
            "fileBytes": 11,
            "sha256": "4" * 64,
        },
        "projection": {
            "format": "fixture-sdk-projection",
            "sha256": "5" * 64,
        },
        "composition": {
            "aptEnvironmentBound": False,
            "mountPath": composition_tool.MOUNT_PATH,
            "releaseInput": False,
            "type": "standalone-mountable-projection",
        },
        "compliance": {"licenses": "pending", "notices": "pending"},
    }
    return composition_tool.BoundInputs(
        apt_root=base / "ziv-fixture-apt",
        sdk_root=base / "ziv-fixture-sdk",
        apt_fd=-1,
        sdk_fd=-1,
        apt_identity=(1, 2),
        sdk_identity=(1, 3),
        apt_receipt=apt_receipt,
        apt_receipt_raw=composition_tool._canonical_json(apt_receipt),
        sdk_receipt=sdk_receipt,
        sdk_receipt_raw=composition_tool._canonical_json(sdk_receipt),
    )


def fixture_helper_records() -> list[dict[str, object]]:
    return [
        {"path": path, "size": index + 1, "sha256": f"{index + 6:064x}"}
        for index, path in enumerate(composition_tool.HELPER_PATHS)
    ]


class FakeSelector:
    def __init__(self) -> None:
        self.mapping: dict[object, object] = {}
        self.closed = False

    def register(self, stream: object, _events: object) -> None:
        self.mapping[stream] = SimpleNamespace(fileobj=stream)

    def unregister(self, stream: object) -> None:
        self.mapping.pop(stream)

    def get_map(self) -> dict[object, object]:
        return self.mapping

    def select(self, timeout: float | None = None) -> list[tuple[object, None]]:
        del timeout
        return [(key, None) for key in tuple(self.mapping.values())]

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, return_code: int) -> None:
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        os.write(stdout_write, stdout)
        os.write(stderr_write, stderr)
        os.close(stdout_write)
        os.close(stderr_write)
        self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
        self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
        self.returncode = return_code

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode


class CompositionToolTest(unittest.TestCase):
    def assert_integrity(self, callback: object) -> None:
        with self.assertRaises(source_tool.SourceToolError) as raised:
            callback()  # type: ignore[operator]
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def run_fake_smoke(
        self,
        child_stdout: bytes,
        child_stderr: bytes,
        return_code: int,
    ) -> tuple[
        tuple[bytes, bytes] | None,
        BaseException | None,
        object,
        SimpleNamespace,
    ]:
        inputs = fixture_inputs()
        inputs.apt_fd = os.open(os.devnull, os.O_RDONLY)
        inputs.sdk_fd = os.open(os.devnull, os.O_RDONLY)
        helper_fds = [os.open(os.devnull, os.O_RDONLY) for _ in range(3)]
        cgroup = SimpleNamespace(
            procs_fd=os.open(os.devnull, os.O_WRONLY),
            removed=False,
        )
        process = FakeProcess(child_stdout, child_stderr, return_code)
        helper_raws = {path: f"fixture:{path}".encode() for path in composition_tool.HELPER_PATHS}

        def remove_cgroup(handle: SimpleNamespace) -> None:
            handle.removed = True

        result: tuple[bytes, bytes] | None = None
        error: BaseException | None = None
        try:
            with (
                patch("composition_tool._open_pinned_helper", side_effect=helper_fds),
                patch(
                    "composition_tool.environment_tool._prepare_installer_cgroup",
                    return_value=cgroup,
                ),
                patch(
                    "composition_tool.environment_tool._cgroup_limit_violation",
                    return_value=None,
                ),
                patch(
                    "composition_tool.environment_tool._cgroup_is_empty",
                    return_value=True,
                ),
                patch(
                    "composition_tool.environment_tool._remove_installer_cgroup",
                    side_effect=remove_cgroup,
                ),
                patch("composition_tool.environment_tool._assert_no_nested_mounts"),
                patch(
                    "composition_tool._namespace_id",
                    side_effect=lambda name: f"{name}:[1]",
                ),
                patch("composition_tool._assert_identity"),
                patch("composition_tool.selectors.DefaultSelector", side_effect=FakeSelector),
                patch("composition_tool.os.set_blocking"),
                patch("composition_tool.subprocess.Popen", return_value=process) as popen,
                patch.object(signal, "SIG_BLOCK", 0, create=True),
                patch.object(signal, "SIG_SETMASK", 1, create=True),
                patch.object(signal, "pthread_sigmask", return_value=set(), create=True),
            ):
                try:
                    result = composition_tool._run_smoke(inputs, 30, helper_raws)
                except BaseException as caught:
                    error = caught
        finally:
            inputs.close()
        return result, error, popen, cgroup

    def test_parse_smoke_accepts_the_exact_profile(self) -> None:
        records = composition_tool._parse_smoke(VALID_SMOKE)
        self.assertEqual(list(composition_tool.SMOKE_KEYS), list(records))
        self.assertEqual("Python 3.12.3", records["python"])
        self.assertIn("aapt", records["aapt2"])

    def test_parse_smoke_rejects_noncanonical_shape(self) -> None:
        lines = VALID_SMOKE.splitlines(keepends=True)
        swapped = b"".join([lines[1], lines[0], *lines[2:]])
        duplicate = VALID_SMOKE + lines[-1]
        cases = (
            b"",
            VALID_SMOKE[:-1],
            VALID_SMOKE.replace(b"\n", b"\r\n", 1),
            VALID_SMOKE.replace(b"Python", b"Py\0thon", 1),
            swapped,
            duplicate,
            VALID_SMOKE + b"extra\tvalue\n",
        )
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                self.assert_integrity(lambda raw=raw: composition_tool._parse_smoke(raw))

    def test_parse_smoke_rejects_version_and_control_drift(self) -> None:
        cases = (
            VALID_SMOKE.replace(b"Python 3.12.3", b"Python 3.12.4"),
            VALID_SMOKE.replace(b"meson\t1.11.0", b"meson\t1.12.0"),
            VALID_SMOKE.replace(b"clang version 21.0.0", b"clang version 20.0.0", 1),
            VALID_SMOKE.replace(b"ninja\t1.11.1", b"ninja\t1.11.1\x1f"),
        )
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                self.assert_integrity(lambda raw=raw: composition_tool._parse_smoke(raw))

    def test_receipt_binding_requires_the_exact_standalone_boundary(self) -> None:
        inputs = fixture_inputs()
        composition_tool._validate_receipt_binding(
            inputs.apt_receipt,
            inputs.sdk_receipt,
        )
        mutations = (
            ("apt", "kind", "wrong-kind"),
            ("apt", "ready", True),
            ("sdk", "mountPath", "/wrong"),
            ("sdk", "sourceManifestSha256", "9" * 64),
            ("sdk", "composition", {}),
        )
        for target, key, value in mutations:
            apt = copy.deepcopy(inputs.apt_receipt)
            sdk = copy.deepcopy(inputs.sdk_receipt)
            (apt if target == "apt" else sdk)[key] = value
            with self.subTest(target=target, key=key):
                self.assert_integrity(
                    lambda apt=apt, sdk=sdk: composition_tool._validate_receipt_binding(
                        apt,
                        sdk,
                    )
                )

    def test_bound_inputs_close_attempts_both_descriptors(self) -> None:
        inputs = fixture_inputs()
        inputs.apt_fd = 101
        inputs.sdk_fd = 102
        with patch(
            "composition_tool.os.close",
            side_effect=(OSError("fixture close failure"), None),
        ) as close:
            self.assert_integrity(inputs.close)
        self.assertEqual([(101,), (102,)], [entry.args for entry in close.call_args_list])
        self.assertEqual((-1, -1), (inputs.apt_fd, inputs.sdk_fd))

    def test_input_records_require_tree_and_projection_evidence(self) -> None:
        inputs = fixture_inputs()
        records = composition_tool._input_records(inputs)
        self.assertEqual("3" * 64, records["apt"]["tree"]["sha256"])
        self.assertEqual(composition_tool.MOUNT_PATH, records["sdk"]["mountPath"])
        inputs.sdk_receipt["projection"] = "not-an-object"
        self.assert_integrity(lambda: composition_tool._input_records(inputs))

    def test_receipt_is_deterministic_and_keeps_release_boundary_closed(self) -> None:
        inputs = fixture_inputs()
        helpers = fixture_helper_records()
        with patch(
            "composition_tool.environment_tool._sandbox_policy_record",
            return_value=b'{"policy":"fixture"}\n',
        ):
            first = composition_tool._receipt_data(inputs, VALID_SMOKE, b"", helpers)
            second = composition_tool._receipt_data(inputs, VALID_SMOKE, b"", helpers)
        self.assertEqual(first, second)
        self.assertEqual(
            composition_tool._canonical_json(first),
            composition_tool._canonical_json(second),
        )
        self.assertIs(first["ready"], False)
        self.assertIs(first["releaseInput"], False)
        self.assertIs(first["composition"]["aptEnvironmentBound"], True)
        self.assertIs(first["composition"]["compositionVerified"], True)
        self.assertEqual(
            {
                "root",
                "sdk",
                "sdkMountPath",
                "opt",
                "proc",
                "procKeys",
                "dev",
                "devShm",
                "run",
                "tmp",
                "varTmp",
                "varLog",
                "propagation",
                "oldRoot",
            },
            set(first["composition"]["mountPolicy"]),
        )

    def test_receipt_changes_with_transcript_or_helper_snapshot(self) -> None:
        inputs = fixture_inputs()
        helpers = fixture_helper_records()
        changed_helpers = copy.deepcopy(helpers)
        changed_helpers[0]["sha256"] = "f" * 64
        changed_smoke = VALID_SMOKE.replace(b"17.0.19", b"17.0.20")
        with patch(
            "composition_tool.environment_tool._sandbox_policy_record",
            return_value=b'{"policy":"fixture"}\n',
        ):
            baseline = composition_tool._receipt_data(inputs, VALID_SMOKE, b"", helpers)
            helper_drift = composition_tool._receipt_data(
                inputs,
                VALID_SMOKE,
                b"",
                changed_helpers,
            )
            transcript_drift = composition_tool._receipt_data(
                inputs,
                changed_smoke,
                b"",
                helpers,
            )
        self.assertNotEqual(
            baseline["composition"]["sha256"],
            helper_drift["composition"]["sha256"],
        )
        self.assertNotEqual(
            baseline["composition"]["sha256"],
            transcript_drift["composition"]["sha256"],
        )

    def test_receipt_rejects_nonempty_stderr(self) -> None:
        with self.assertRaises(source_tool.SourceToolError):
            composition_tool._receipt_data(
                fixture_inputs(),
                VALID_SMOKE,
                b"unexpected\n",
                fixture_helper_records(),
            )

    def test_helper_snapshot_detects_order_and_content_drift(self) -> None:
        payloads = {
            path: f"helper:{index}".encode("ascii")
            for index, path in enumerate(composition_tool.HELPER_PATHS)
        }
        with patch("composition_tool._read_helper", side_effect=payloads.__getitem__):
            raw_by_path, records = composition_tool._helper_snapshot()
            composition_tool._assert_helper_snapshot(raw_by_path)
        self.assertEqual(list(composition_tool.HELPER_PATHS), [row["path"] for row in records])

        changed = dict(payloads)
        changed[composition_tool.HELPER_PATHS[-1]] += b"-changed"
        with patch("composition_tool._read_helper", side_effect=changed.__getitem__):
            self.assert_integrity(
                lambda: composition_tool._assert_helper_snapshot(raw_by_path)
            )
        reversed_snapshot = dict(reversed(tuple(raw_by_path.items())))
        self.assert_integrity(
            lambda: composition_tool._assert_helper_snapshot(reversed_snapshot)
        )

    def test_stable_file_reader_rejects_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "helper"
            alias = Path(directory) / "helper-alias"
            path.write_bytes(b"locked\n")
            self.assertEqual(
                b"locked\n",
                composition_tool._read_stable_file(
                    path,
                    maximum=32,
                    label="fixture helper",
                ),
            )
            os.link(path, alias)
            self.assert_integrity(
                lambda: composition_tool._read_stable_file(
                    path,
                    maximum=32,
                    label="fixture helper",
                )
            )

    def test_run_smoke_rejects_timeout_and_helper_set_before_launch(self) -> None:
        helper_raws = {path: b"fixture" for path in composition_tool.HELPER_PATHS}
        with self.assertRaises(source_tool.SourceToolError) as raised:
            composition_tool._run_smoke(fixture_inputs(), 29, helper_raws)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)
        helper_raws.pop(composition_tool.HELPER_PATHS[-1])
        self.assert_integrity(
            lambda: composition_tool._run_smoke(fixture_inputs(), 30, helper_raws)
        )

    def test_run_smoke_uses_fixed_argv_environment_and_drains_streams(self) -> None:
        result, error, popen, cgroup = self.run_fake_smoke(VALID_SMOKE, b"", 0)
        self.assertIsNone(error)
        self.assertEqual((VALID_SMOKE, b""), result)
        self.assertIs(cgroup.removed, True)
        argv = popen.call_args.args[0]
        self.assertEqual(
            [
                "/usr/bin/unshare",
                "--mount",
                "--net",
                "--pid",
                "--fork",
                "--kill-child=SIGKILL",
                "--uts",
                "--ipc",
                "--propagation",
                "private",
                "/bin/bash",
                "--noprofile",
                "--norc",
            ],
            argv[:13],
        )
        self.assertEqual(composition_tool.SMOKE_PROFILE, argv[-1])
        self.assertEqual("/", popen.call_args.kwargs["cwd"])
        self.assertEqual(
            {
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C",
                "TZ": "UTC",
                "SOURCE_DATE_EPOCH": "946684800",
            },
            popen.call_args.kwargs["env"],
        )
        self.assertEqual(6, len(popen.call_args.kwargs["pass_fds"]))
        self.assertIs(popen.call_args.kwargs["start_new_session"], True)

    def test_run_smoke_maps_nonzero_and_stderr_tail_to_integrity(self) -> None:
        result, error, _popen, cgroup = self.run_fake_smoke(b"partial\n", b"boom\n", 7)
        self.assertIsNone(result)
        self.assertIsInstance(error, source_tool.SourceToolError)
        self.assertEqual(source_tool.EXIT_INTEGRITY, error.exit_code)
        self.assertIn("exited 7", str(error))
        self.assertIn("boom", str(error))
        self.assertIs(cgroup.removed, True)

    def test_bounded_log_append_rejects_overflow(self) -> None:
        buffer = bytearray(b"x" * composition_tool.MAX_LOG_BYTES)
        composition_tool._append_bounded(buffer, b"", "stdout")
        self.assert_integrity(
            lambda: composition_tool._append_bounded(buffer, b"x", "stdout")
        )

    def test_run_smoke_closes_first_helper_if_second_pin_fails(self) -> None:
        descriptor = os.open(os.devnull, os.O_RDONLY)
        calls = 0

        def pin(*_args: object) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return descriptor
            raise source_tool.SourceToolError("fixture pin failure", source_tool.EXIT_INTEGRITY)

        helper_raws = {path: b"fixture" for path in composition_tool.HELPER_PATHS}
        with patch("composition_tool._open_pinned_helper", side_effect=pin):
            self.assert_integrity(
                lambda: composition_tool._run_smoke(fixture_inputs(), 30, helper_raws)
            )
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_run_smoke_closes_all_helpers_if_signal_masking_fails(self) -> None:
        descriptors = [os.open(os.devnull, os.O_RDONLY) for _ in range(3)]
        helper_raws = {path: b"fixture" for path in composition_tool.HELPER_PATHS}
        with (
            patch("composition_tool._open_pinned_helper", side_effect=descriptors),
            patch.object(signal, "SIG_BLOCK", 0, create=True),
            patch.object(
                signal,
                "pthread_sigmask",
                side_effect=RuntimeError("fixture signal failure"),
                create=True,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture signal failure"):
                composition_tool._run_smoke(fixture_inputs(), 30, helper_raws)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_receipt_location_rejects_inputs_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            inputs = fixture_inputs(base)
            cache = base / "cache"
            composition_tool._assert_receipt_location(base / "outside.json", cache, inputs)
            for output in (
                inputs.apt_root / "receipt.json",
                inputs.sdk_root / "receipt.json",
                cache / "receipt.json",
            ):
                with self.subTest(output=output):
                    self.assert_integrity(
                        lambda output=output: composition_tool._assert_receipt_location(
                            output,
                            cache,
                            inputs,
                        )
                    )

    def test_quiet_input_verification_has_no_success_output(self) -> None:
        def noisy(*_args: object) -> None:
            print("nested verifier success")

        captured = io.StringIO()
        with (
            patch(
                "composition_tool.environment_tool.verify_apt_environment",
                side_effect=noisy,
            ),
            patch(
                "composition_tool.sdk_tool.verify_sdk_projection",
                side_effect=noisy,
            ),
            contextlib.redirect_stdout(captured),
        ):
            composition_tool._quietly_verify_input_trees(
                Path("manifest"),
                Path("source"),
                Path("cache"),
                Path("apt"),
                Path("sdk"),
            )
        self.assertEqual("", captured.getvalue())

    def test_reverify_bound_inputs_requires_receipt_bytes_to_stay_fixed(self) -> None:
        inputs = fixture_inputs()
        with (
            patch("composition_tool._quietly_verify_input_trees"),
            patch(
                "composition_tool.environment_tool._read_receipt",
                return_value=(inputs.apt_receipt, inputs.apt_receipt_raw),
            ),
            patch(
                "composition_tool.sdk_tool._read_receipt",
                return_value=(inputs.sdk_receipt, inputs.sdk_receipt_raw),
            ),
            patch("composition_tool._assert_identity") as identity,
            patch("composition_tool.environment_tool._assert_no_nested_mounts") as mounts,
        ):
            composition_tool._reverify_bound_inputs(
                Path("manifest"),
                Path("source"),
                Path("cache"),
                inputs,
            )
        self.assertEqual(2, identity.call_count)
        self.assertEqual(2, mounts.call_count)

        changed_apt = copy.deepcopy(inputs.apt_receipt)
        changed_apt["tree"]["sha256"] = "f" * 64
        changed_raw = composition_tool._canonical_json(changed_apt)
        with (
            patch("composition_tool._quietly_verify_input_trees"),
            patch(
                "composition_tool.environment_tool._read_receipt",
                return_value=(changed_apt, changed_raw),
            ),
            patch(
                "composition_tool.sdk_tool._read_receipt",
                return_value=(inputs.sdk_receipt, inputs.sdk_receipt_raw),
            ),
        ):
            self.assert_integrity(
                lambda: composition_tool._reverify_bound_inputs(
                    Path("manifest"),
                    Path("source"),
                    Path("cache"),
                    inputs,
                )
            )

    def test_linux_root_gate_is_fail_closed(self) -> None:
        with patch("composition_tool.sys.platform", "win32"):
            with self.assertRaises(source_tool.SourceToolError) as raised:
                composition_tool._require_linux_root("fixture")
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_smoke_helpers_capture_aapt_stderr_and_probe_both_inputs(self) -> None:
        namespace = composition_tool.NAMESPACE_HELPER.read_text(encoding="utf-8")
        chroot = composition_tool.CHROOT_HELPER.read_text(encoding="utf-8")
        self.assertIn("seccomp_helper_sha256", namespace)
        self.assertIn("declare -A seen_mounts=()", namespace)
        self.assertIn("aapt2 version 2>&1", chroot)
        self.assertIn("/opt/zivplayer/toolchain/.ziv-write-probe", chroot)
        self.assertIn("/usr/.ziv-root-write-probe", chroot)

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "receipt publication requires Linux root on ext4",
    )
    def test_publish_receipt_is_atomic_canonical_and_no_replace(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            parent = Path(directory)
            parent.chmod(0o700)
            output = parent / "composition.json"
            receipt = {"kind": "fixture", "ready": False}
            composition_tool._publish_receipt(output, receipt)
            info = output.lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(0o644, stat.S_IMODE(info.st_mode))
            self.assertEqual((0, 0), (info.st_uid, info.st_gid))
            self.assertEqual(composition_tool.NORMALIZED_MTIME_NS, info.st_mtime_ns)
            self.assertEqual(composition_tool._canonical_json(receipt), output.read_bytes())
            self.assert_integrity(
                lambda: composition_tool._publish_receipt(output, receipt)
            )

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "receipt publication requires Linux root on ext4",
    )
    def test_publish_receipt_cleans_staging_after_rename_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            parent = Path(directory)
            parent.chmod(0o700)
            output = parent / "composition.json"
            with patch(
                "composition_tool._rename_no_replace_at",
                side_effect=source_tool.SourceToolError(
                    "fixture rename failure",
                    source_tool.EXIT_INTEGRITY,
                ),
            ):
                self.assert_integrity(
                    lambda: composition_tool._publish_receipt(output, {"fixture": True})
                )
            self.assertEqual([], list(parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
