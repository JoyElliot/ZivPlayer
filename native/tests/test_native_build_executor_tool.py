# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import native_build_executor_tool  # noqa: E402
import source_tool  # noqa: E402


COMMITTED_POLICY = REPOSITORY_ROOT / "native" / "native-build-executor-policy.toml"
COMMITTED_PROFILE = REPOSITORY_ROOT / "native" / "native-build-profile.toml"
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"
COMMITTED_TOOLCHAIN_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"


class NativeBuildExecutorPolicyTest(unittest.TestCase):
    def load_policy(self) -> native_build_executor_tool.LoadedBuildExecutorPolicy:
        return native_build_executor_tool.load_build_execution_policy(
            COMMITTED_POLICY,
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )

    def assert_schema_error(self, data: dict[str, object], fragment: str) -> None:
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_executor_tool.validate_policy_data(data)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)
        self.assertIn(fragment, str(raised.exception))

    def test_committed_policy_binds_closed_loop_without_release_gate(self) -> None:
        loaded = self.load_policy()
        self.assertEqual(native_build_executor_tool.EXPECTED_POLICY_SHA256, loaded.sha256)
        self.assertEqual(
            loaded.data["binding"]["profileSha256"],  # type: ignore[index]
            loaded.profile.sha256,
        )
        self.assertEqual(
            loaded.data["binding"]["namespaceProbePolicySha256"],  # type: ignore[index]
            loaded.probe_policy.sha256,
        )
        gate = loaded.data["gate"]
        self.assertIsInstance(gate, dict)
        assert isinstance(gate, dict)
        self.assertEqual("offline-inspection-build", gate["phase"])
        for key in ("buildCommands", "artifactStaging", "artifactAudit", "buildReceipt"):
            self.assertIs(gate[key], True)
        self.assertIs(gate["ready"], False)
        self.assertIs(gate["releaseInput"], False)

    def test_policy_schema_is_exact_ordered_and_type_strict(self) -> None:
        cases: list[tuple[dict[str, object], str]] = []
        missing = copy.deepcopy(native_build_executor_tool.EXPECTED_POLICY)
        del missing["audit"]
        cases.append((missing, "missing audit"))
        extra = copy.deepcopy(native_build_executor_tool.EXPECTED_POLICY)
        extra["unexpected"] = True
        cases.append((extra, "unknown unexpected"))
        boolean_as_integer = copy.deepcopy(native_build_executor_tool.EXPECTED_POLICY)
        boolean_as_integer["gate"]["buildCommands"] = 1  # type: ignore[index]
        cases.append((boolean_as_integer, "buildCommands type"))
        closed_audit = copy.deepcopy(native_build_executor_tool.EXPECTED_POLICY)
        closed_audit["gate"]["artifactAudit"] = False  # type: ignore[index]
        cases.append((closed_audit, "artifactAudit"))
        opened_release = copy.deepcopy(native_build_executor_tool.EXPECTED_POLICY)
        opened_release["gate"]["releaseInput"] = True  # type: ignore[index]
        cases.append((opened_release, "releaseInput"))
        reordered_libraries = copy.deepcopy(native_build_executor_tool.EXPECTED_POLICY)
        libraries = reordered_libraries["artifacts"]["expectedLibraries"]  # type: ignore[index]
        libraries[0], libraries[1] = libraries[1], libraries[0]
        cases.append((reordered_libraries, "expectedLibraries[0]"))
        for data, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assert_schema_error(data, fragment)

    def test_policy_byte_drift_is_an_integrity_error(self) -> None:
        original = native_build_executor_tool._stable_bytes  # noqa: SLF001

        def drifting_reader(path: Path, *, maximum: int, label: str) -> bytes:
            raw = original(path, maximum=maximum, label=label)
            return raw + b"\n" if path == COMMITTED_POLICY else raw

        with patch.object(
            native_build_executor_tool,
            "_stable_bytes",
            side_effect=drifting_reader,
        ), self.assertRaises(source_tool.SourceToolError) as raised:
            self.load_policy()
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("policy digest differs", str(raised.exception))

    def test_each_helper_byte_drift_is_an_integrity_error(self) -> None:
        original = native_build_executor_tool._stable_bytes  # noqa: SLF001
        helpers = native_build_executor_tool.EXPECTED_POLICY["helpers"]
        assert isinstance(helpers, dict)
        helper_names = tuple(
            Path(str(helpers[path_key])).name
            for path_key, _digest_key in native_build_executor_tool.HELPER_KEYS
        )
        for helper_name in helper_names:
            with self.subTest(helper=helper_name):
                def drifting_reader(path: Path, *, maximum: int, label: str) -> bytes:
                    raw = original(path, maximum=maximum, label=label)
                    return raw + b"\n" if path.name == helper_name else raw

                with patch.object(
                    native_build_executor_tool,
                    "_stable_bytes",
                    side_effect=drifting_reader,
                ), self.assertRaises(source_tool.SourceToolError) as raised:
                    self.load_policy()
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
                self.assertIn("helper digest differs", str(raised.exception))

    def test_selected_profile_drift_is_rejected(self) -> None:
        selected = native_build_executor_tool.native_build_tool.load_profile(
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        cases = (
            replace(selected, sha256="0" * 64),
            replace(
                selected,
                policy={**selected.policy, "outputMount": "/different-output"},
            ),
            replace(
                selected,
                build={**selected.build, "expectedLibraries": ["libmpv.so"]},
            ),
            replace(
                selected,
                abis=(
                    {**selected.abis[0], "elfMachine": "different"},
                    selected.abis[1],
                ),
            ),
        )
        for changed_profile in cases:
            with self.subTest(profile=changed_profile), patch.object(
                native_build_executor_tool.native_build_tool,
                "load_profile",
                return_value=changed_profile,
            ), self.assertRaises(source_tool.SourceToolError) as raised:
                self.load_policy()
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("not bound", str(raised.exception))

    def test_accepted_probe_binding_drift_is_rejected(self) -> None:
        selected = self.load_policy().probe_policy
        with patch.object(
            native_build_executor_tool.native_executor_tool,
            "load_execution_policy",
            return_value=replace(selected, sha256="0" * 64),
        ), patch.object(
            native_build_executor_tool.native_executor_tool,
            "_assert_runtime_policy",
        ), self.assertRaises(source_tool.SourceToolError) as raised:
            self.load_policy()
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("namespace-probe policy digest differs", str(raised.exception))

    def test_runtime_limits_are_bound_to_probe_and_host_constants(self) -> None:
        loaded = self.load_policy()
        changed = copy.deepcopy(loaded.data)
        changed["cgroup"]["pidsMax"] += 1  # type: ignore[index,operator]
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_executor_tool._assert_runtime_binding(  # noqa: SLF001
                changed,
                loaded.probe_policy,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("cgroup limits differ", str(raised.exception))

        with patch.object(
            native_build_executor_tool.native_executor_tool.environment_tool,
            "CGROUP_DRAIN_TIMEOUT_SECONDS",
            11,
        ), self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_executor_tool._assert_runtime_binding(  # noqa: SLF001
                loaded.data,
                loaded.probe_policy,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("drain timeout differs", str(raised.exception))

    def test_runner_contains_only_the_two_exact_profile_commands(self) -> None:
        runner = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-build-runner.bash"
        ).read_bytes()
        namespace = (
            REPOSITORY_ROOT
            / "native"
            / "toolchain"
            / "native-build-execution-namespace.bash"
        ).read_bytes()
        self.assertEqual(2, runner.count(b'"${build_script}" --arch'))
        self.assertEqual(1, runner.count(b'"${build_script}" --arch arm64 mpv'))
        self.assertEqual(1, runner.count(b'"${build_script}" --arch x86_64 mpv'))
        self.assertNotIn(b"eval ", runner)
        self.assertNotIn(b"bash -c", runner)
        self.assertNotIn(b"< <(", runner)
        self.assertNotIn(b"<<<", runner)
        self.assertNotIn(b"[[ -w ", runner)
        self.assertIn(b"require_read_only_mount /", runner)
        self.assertIn(b"require_read_only_mount /opt/zivplayer/toolchain", runner)
        self.assertIn(b"ziv-native-build-namespace-execution-v1", namespace)
        self.assertIn(
            b"/run/ziv-native-build-runner.bash --execute-locked-build",
            namespace,
        )
        self.assertNotIn(b"native-build-probe.bash", namespace)
        self.assertIn(b"staging-pending-parent-audit", runner)

    def test_policy_requires_one_shot_state_audit_and_nonrelease_receipt(self) -> None:
        policy = native_build_executor_tool.EXPECTED_POLICY
        workspace = policy["workspace"]
        audit = policy["audit"]
        receipt = policy["receipt"]
        assert isinstance(workspace, dict)
        assert isinstance(audit, dict)
        assert isinstance(receipt, dict)
        self.assertEqual("single-attempt", policy["execution"]["workspaceReuse"])  # type: ignore[index]
        self.assertEqual(
            "ziv-native-build-namespace-execution-v1",
            policy["execution"]["namespaceProfile"],  # type: ignore[index]
        )
        self.assertEqual(29, policy["execution"]["namespaceArgumentCount"])  # type: ignore[index]
        self.assertEqual(10, policy["execution"]["preservedDescriptorCount"])  # type: ignore[index]
        self.assertEqual(
            [
                "/usr/bin/perl",
                "/usr/share/zivplayer/toolchain-evidence/apt-stage/helpers/native/toolchain/install-seccomp.pl",
            ],
            policy["execution"]["seccompInvocation"],  # type: ignore[index]
        )
        self.assertEqual(
            ["/bin/bash", "--noprofile", "--norc"],
            policy["execution"]["runnerInterpreterInvocation"],  # type: ignore[index]
        )
        self.assertEqual(
            ["/run/ziv-native-build-runner.bash", "--execute-locked-build"],
            policy["execution"]["runnerInvocation"],  # type: ignore[index]
        )
        self.assertEqual("2", policy["execution"]["procHidepidRequested"])  # type: ignore[index]
        self.assertEqual(
            ["2", "invisible"],
            policy["execution"]["procHidepidAccepted"],  # type: ignore[index]
        )
        self.assertEqual(
            "dedicated-build-wall-clock-deadline",
            policy["execution"]["timeoutValidation"],  # type: ignore[index]
        )
        self.assertEqual(
            "locked-runner-bytes-plus-parent-launch-and-exit",
            policy["execution"]["commandEventSource"],  # type: ignore[index]
        )
        self.assertEqual(
            "diagnostic-only-non-authoritative",
            policy["execution"]["childStdoutMarkers"],  # type: ignore[index]
        )
        self.assertEqual(
            "pidfd-sigterm-2s-cgroup-kill-pidfd-sigkill",
            policy["execution"]["failureTeardown"],  # type: ignore[index]
        )
        self.assertEqual(
            "require-empty-and-identity",
            policy["execution"]["cgroupRemoval"],  # type: ignore[index]
        )
        self.assertEqual(
            "workspace-root-create-no-replace-fsync",
            workspace["attemptMarkerPublication"],
        )
        self.assertIs(workspace["attemptMarkerCanonicalJson"], True)
        self.assertEqual(0, workspace["attemptMarkerOwnerUid"])
        self.assertEqual(0, workspace["attemptMarkerOwnerGid"])
        self.assertEqual(1, workspace["attemptMarkerLinkCount"])
        self.assertEqual("forbidden", workspace["attemptMarkerXattrs"])
        self.assertEqual(
            "stable-canonical-bytes-and-sha256",
            workspace["attemptMarkerReadback"],
        )
        self.assertIs(workspace["attemptMarkerParentFsync"], True)
        self.assertIn("policy-sha256", workspace["attemptMarkerRecords"])
        self.assertIn("exact-command-order", workspace["attemptMarkerRecords"])
        self.assertEqual(
            "canonical-json-lexicographic",
            workspace["attemptMarkerFieldOrder"],
        )
        self.assertEqual("forbidden", workspace["attemptMarkerAdditionalFields"])
        self.assertEqual(
            sorted(workspace["attemptMarkerFields"]),
            workspace["attemptMarkerFields"],
        )
        self.assertIn("policySha256", workspace["attemptMarkerFields"])
        self.assertIn("commands", workspace["attemptMarkerFields"])
        self.assertIn("state", workspace["attemptMarkerFields"])
        self.assertEqual("consumed-before-launch", workspace["attemptMarkerState"])
        self.assertEqual(
            "reverify-external-canonical-source-input-unchanged",
            workspace["canonicalSourcePostcondition"],
        )
        self.assertEqual(
            "allow-build-mutations-within-pinned-directory-and-bounded-filesystem-delta",
            workspace["preparedSourcePostcondition"],
        )
        for key in (
            "attemptMarkerBuildExecuted",
            "attemptMarkerArtifactStaged",
            "attemptMarkerArtifactAudited",
            "attemptMarkerReady",
            "attemptMarkerReleaseInput",
        ):
            self.assertIs(workspace[key], False)
        self.assertIn("without-build-receipt", workspace["failureDisposition"])
        self.assertEqual(16384, audit["pageSizeBytes"])
        self.assertEqual("record-java-prefix-and-require-none", audit["jniExportPolicy"])
        self.assertIs(receipt["buildExecuted"], True)
        self.assertIs(receipt["artifactStaged"], True)
        self.assertIs(receipt["artifactAudited"], True)
        self.assertIs(receipt["ready"], False)
        self.assertIs(receipt["releaseInput"], False)

    def test_verify_inputs_binds_receipts_and_audit_tool_without_execution(self) -> None:
        loaded = self.load_policy()
        preparation_raw = b"fixture preparation receipt"
        composition_raw = b"fixture composition receipt"
        data = copy.deepcopy(loaded.data)
        binding = data["binding"]
        assert isinstance(binding, dict)
        binding["preparationReceiptSha256"] = hashlib.sha256(preparation_raw).hexdigest()
        binding["toolchainCompositionReceiptSha256"] = hashlib.sha256(
            composition_raw
        ).hexdigest()
        selected = replace(loaded, data=data)
        paths = (Path("/fixture"),) * 11
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    native_build_executor_tool,
                    "load_build_execution_policy",
                    return_value=selected,
                )
            )
            stack.enter_context(
                patch.object(native_build_executor_tool.native_build_tool, "_require_linux_root")
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "_resolved_build_workspace",
                    return_value=(Path("/fixture"), Path("/fixture/workspace")),
                )
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "preflight",
                    return_value=selected.profile,
                )
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "_snapshot_preparation_inputs",
                    return_value=SimpleNamespace(
                        composition_receipt_raw=composition_raw,
                    ),
                )
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "_verify_prepared_workspace",
                    return_value=({}, preparation_raw),
                )
            )
            audit_tool = stack.enter_context(
                patch.object(native_build_executor_tool, "_verify_audit_tool")
            )
            io_scope = stack.enter_context(
                patch.object(native_build_executor_tool, "_verify_io_device_scope")
            )
            result = native_build_executor_tool.verify_inputs(*paths)
        self.assertIs(selected, result)
        io_scope.assert_called_once_with(
            selected,
            Path("/fixture"),
            Path("/fixture"),
            Path("/fixture"),
            Path("/fixture/workspace"),
        )
        audit_tool.assert_called_once_with(selected, Path("/fixture"))

    def test_verify_io_device_scope_rejects_split_devices(self) -> None:
        loaded = self.load_policy()
        device_infos = (
            SimpleNamespace(st_dev=1),
            SimpleNamespace(st_dev=1),
            SimpleNamespace(st_dev=1),
            SimpleNamespace(st_dev=2),
        )
        with patch.object(os, "stat", side_effect=device_infos), self.assertRaises(
            source_tool.SourceToolError
        ) as raised:
            native_build_executor_tool._verify_io_device_scope(  # noqa: SLF001
                loaded,
                Path("/apt"),
                Path("/sdk"),
                Path("/source"),
                Path("/workspace"),
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("do not share", str(raised.exception))

    def test_verify_inputs_rejects_bound_receipt_drift(self) -> None:
        loaded = self.load_policy()
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    native_build_executor_tool,
                    "load_build_execution_policy",
                    return_value=loaded,
                )
            )
            stack.enter_context(
                patch.object(native_build_executor_tool.native_build_tool, "_require_linux_root")
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "_resolved_build_workspace",
                    return_value=(Path("/fixture"), Path("/fixture/workspace")),
                )
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "preflight",
                    return_value=loaded.profile,
                )
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "_snapshot_preparation_inputs",
                    return_value=SimpleNamespace(composition_receipt_raw=b"wrong composition"),
                )
            )
            stack.enter_context(
                patch.object(
                    native_build_executor_tool.native_build_tool,
                    "_verify_prepared_workspace",
                    return_value=({}, b"wrong preparation"),
                )
            )
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_executor_tool.verify_inputs(*((Path("/fixture"),) * 11))
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("preparation receipt digest differs", str(raised.exception))

    def test_cli_exposes_only_read_only_validation(self) -> None:
        parser = native_build_executor_tool._parser()  # noqa: SLF001
        subparsers = [
            action
            for action in parser._actions  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
        ]
        self.assertEqual(1, len(subparsers))
        self.assertEqual({"validate", "verify-inputs"}, set(subparsers[0].choices))

    def test_cli_validate_cannot_launch_a_process(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        forbidden = AssertionError("static build-policy validation launched a process")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(
            subprocess,
            "Popen",
            side_effect=forbidden,
        ), patch.object(subprocess, "run", side_effect=forbidden), patch.object(
            os,
            "system",
            side_effect=forbidden,
        ):
            result = native_build_executor_tool.main(["validate"])
        self.assertEqual(source_tool.EXIT_OK, result)
        self.assertEqual("", stderr.getvalue())
        self.assertIn("phase=offline-inspection-build", stdout.getvalue())
        self.assertIn("buildCommands=true", stdout.getvalue())
        self.assertIn("artifactAudit=true", stdout.getvalue())
        self.assertIn("ready=false", stdout.getvalue())
        self.assertIn("releaseInput=false", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
