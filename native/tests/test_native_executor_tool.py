# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import native_executor_tool  # noqa: E402
import source_tool  # noqa: E402


COMMITTED_POLICY = REPOSITORY_ROOT / "native" / "native-executor-policy.toml"
COMMITTED_PROFILE = REPOSITORY_ROOT / "native" / "native-build-profile.toml"
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"
COMMITTED_TOOLCHAIN_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"


class NativeExecutorPolicyTest(unittest.TestCase):
    def assert_schema_error(self, data: dict[str, object], fragment: str) -> None:
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool.validate_policy_data(data)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)
        self.assertIn(fragment, str(raised.exception))

    def test_committed_probe_policy_binds_profile_and_helpers(self) -> None:
        loaded = native_executor_tool.load_execution_policy(
            COMMITTED_POLICY,
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        self.assertEqual(native_executor_tool.EXPECTED_POLICY_SHA256, loaded.sha256)
        self.assertEqual(native_executor_tool.EXPECTED_PROFILE_SHA256, loaded.profile.sha256)
        self.assertEqual(
            tuple(
                str(loaded.data["helpers"][path_key])  # type: ignore[index]
                for path_key, _digest_key in native_executor_tool.HELPER_KEYS
            ),
            tuple(loaded.helper_raws),
        )
        gate = loaded.data["gate"]
        self.assertIsInstance(gate, dict)
        assert isinstance(gate, dict)
        self.assertEqual("namespace-probe", gate["phase"])
        for key in ("buildCommands", "artifactStaging", "buildReceipt", "ready", "releaseInput"):
            self.assertIs(gate[key], False)

    def test_policy_schema_is_exact_and_type_strict(self) -> None:
        cases: list[tuple[dict[str, object], str]] = []
        missing = copy.deepcopy(native_executor_tool.EXPECTED_POLICY)
        del missing["namespace"]  # type: ignore[arg-type]
        cases.append((missing, "missing namespace"))
        extra = copy.deepcopy(native_executor_tool.EXPECTED_POLICY)
        extra["unexpected"] = True
        cases.append((extra, "unknown unexpected"))
        boolean_as_integer = copy.deepcopy(native_executor_tool.EXPECTED_POLICY)
        boolean_as_integer["gate"]["buildCommands"] = 0  # type: ignore[index]
        cases.append((boolean_as_integer, "buildCommands"))
        opened_build_gate = copy.deepcopy(native_executor_tool.EXPECTED_POLICY)
        opened_build_gate["gate"]["buildCommands"] = True  # type: ignore[index]
        cases.append((opened_build_gate, "buildCommands"))
        reordered_controllers = copy.deepcopy(native_executor_tool.EXPECTED_POLICY)
        reordered_controllers["cgroup"]["controllers"] = [  # type: ignore[index]
            "io",
            "cpu",
            "memory",
            "pids",
        ]
        cases.append((reordered_controllers, "controllers[0]"))
        for data, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assert_schema_error(data, fragment)

    def test_policy_byte_drift_is_an_integrity_error(self) -> None:
        original = native_executor_tool._stable_bytes  # noqa: SLF001

        def drifting_reader(
            path: Path,
            *,
            maximum: int,
            label: str,
            missing_exit: int,
        ) -> bytes:
            raw = original(
                path,
                maximum=maximum,
                label=label,
                missing_exit=missing_exit,
            )
            if path == COMMITTED_POLICY:
                return raw + b"\n"
            return raw

        with patch.object(
            native_executor_tool,
            "_stable_bytes",
            side_effect=drifting_reader,
        ), self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool.load_execution_policy(
                COMMITTED_POLICY,
                COMMITTED_PROFILE,
                COMMITTED_SOURCE_MANIFEST,
                COMMITTED_TOOLCHAIN_MANIFEST,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("policy digest differs", str(raised.exception))

    def test_each_helper_byte_drift_is_an_integrity_error(self) -> None:
        original = native_executor_tool._stable_bytes  # noqa: SLF001
        for helper_name in (
            "native-build-namespace.bash",
            "native-build-probe.bash",
            "install-seccomp.pl",
        ):
            with self.subTest(helper=helper_name):
                def drifting_reader(
                    path: Path,
                    *,
                    maximum: int,
                    label: str,
                    missing_exit: int,
                ) -> bytes:
                    raw = original(
                        path,
                        maximum=maximum,
                        label=label,
                        missing_exit=missing_exit,
                    )
                    if path.name == helper_name:
                        return raw + b"\n"
                    return raw

                with patch.object(
                    native_executor_tool,
                    "_stable_bytes",
                    side_effect=drifting_reader,
                ), self.assertRaises(source_tool.SourceToolError) as raised:
                    native_executor_tool.load_execution_policy(
                        COMMITTED_POLICY,
                        COMMITTED_PROFILE,
                        COMMITTED_SOURCE_MANIFEST,
                        COMMITTED_TOOLCHAIN_MANIFEST,
                    )
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
                self.assertIn("helper digest differs", str(raised.exception))

    def test_selected_profile_binding_drift_is_rejected(self) -> None:
        selected = native_executor_tool.native_build_tool.load_profile(
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        cases = (
            replace(selected, sha256="0" * 64),
            replace(
                selected,
                toolchain={**selected.toolchain, "hostPlatform": "linux/arm64"},
            ),
            replace(
                selected,
                policy={**selected.policy, "outputMount": "/different-output"},
            ),
        )
        for changed_profile in cases:
            with self.subTest(profile=changed_profile), patch.object(
                native_executor_tool.native_build_tool,
                "load_profile",
                return_value=changed_profile,
            ), self.assertRaises(source_tool.SourceToolError) as raised:
                native_executor_tool.load_execution_policy(
                    COMMITTED_POLICY,
                    COMMITTED_PROFILE,
                    COMMITTED_SOURCE_MANIFEST,
                    COMMITTED_TOOLCHAIN_MANIFEST,
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("not bound", str(raised.exception))

    def test_probe_helpers_do_not_execute_native_build_commands(self) -> None:
        namespace_raw = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-build-namespace.bash"
        ).read_bytes()
        probe_raw = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-build-probe.bash"
        ).read_bytes()
        self.assertIn(b"-t overlay overlay", namespace_raw)
        self.assertIn(b"ziv-native-build-namespace-probe-v1", namespace_raw)
        self.assertNotIn(b"buildscripts/buildall.sh", namespace_raw)
        self.assertEqual(1, probe_raw.count(b"/build/source/buildscripts/buildall.sh"))
        self.assertIn(b"[[ -x /build/source/buildscripts/buildall.sh ]]", probe_raw)
        self.assertIn(
            b"/run/ziv-native-build-probe.bash --namespace-probe",
            namespace_raw,
        )
        self.assertIn(b"build-not-executed", probe_raw)
        self.assertNotIn(b"< <(", namespace_raw + probe_raw)
        self.assertNotIn(b"<<<", namespace_raw + probe_raw)
        self.assertNotIn(b"--arch", namespace_raw + probe_raw)
        for forbidden in (b"clang ", b"meson ", b"readelf ", b"ninja "):
            self.assertNotIn(forbidden, namespace_raw + probe_raw)

    def test_cli_exposes_only_static_validation(self) -> None:
        parser = native_executor_tool._parser()  # noqa: SLF001
        subparsers = [
            action
            for action in parser._actions  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
        ]
        self.assertEqual(1, len(subparsers))
        self.assertEqual({"validate"}, set(subparsers[0].choices))

    def test_cli_validate_reports_build_gate_closed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        forbidden_process = AssertionError("static validation launched a process")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(
            subprocess,
            "Popen",
            side_effect=forbidden_process,
        ), patch.object(subprocess, "run", side_effect=forbidden_process):
            with patch.object(os, "system", side_effect=forbidden_process):
                result = native_executor_tool.main(["validate"])
        self.assertEqual(source_tool.EXIT_OK, result)
        self.assertEqual("", stderr.getvalue())
        self.assertIn("phase=namespace-probe", stdout.getvalue())
        self.assertIn("buildCommands=false", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
