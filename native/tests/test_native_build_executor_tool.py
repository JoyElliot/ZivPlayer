# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def test_failure_diagnostic_tail_is_bounded_and_never_blank(self) -> None:
        self.assertEqual("<empty>", native_build_executor_tool._diagnostic_tail(b""))  # noqa: SLF001
        self.assertEqual(
            "x" * 4096,
            native_build_executor_tool._diagnostic_tail(b"prefix" + (b"x" * 4096)),  # noqa: SLF001
        )
        self.assertEqual(
            "�message",
            native_build_executor_tool._diagnostic_tail(b"\xffmessage\n"),  # noqa: SLF001
        )

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

        changed = copy.deepcopy(loaded.data)
        changed["execution"]["leaderSigtermGraceSeconds"] = 3  # type: ignore[index]
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_executor_tool._assert_runtime_binding(  # noqa: SLF001
                changed,
                loaded.probe_policy,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("SIGTERM grace differs", str(raised.exception))

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

    def test_attempt_marker_has_the_exact_twenty_policy_fields(self) -> None:
        loaded = self.load_policy()
        inputs = SimpleNamespace(
            policy=loaded,
            preparation=SimpleNamespace(composition_receipt_raw=b"composition"),
            preparation_receipt_raw=b"preparation",
        )
        marker = native_build_executor_tool._attempt_marker_data(inputs)  # noqa: SLF001
        workspace = loaded.data["workspace"]
        assert isinstance(workspace, dict)
        self.assertEqual(20, len(marker))
        self.assertEqual(workspace["attemptMarkerFields"], sorted(marker))
        self.assertEqual("consumed-before-launch", marker["state"])
        self.assertEqual(loaded.profile.build["commands"], marker["commands"])
        for key in (
            "artifactAudited",
            "artifactStaged",
            "buildExecuted",
            "ready",
            "releaseInput",
        ):
            self.assertIs(marker[key], False)

    def test_readelf_parser_captures_identity_dependencies_symbols_and_android_note(self) -> None:
        android_note = (
            (26).to_bytes(4, "little")
            + b"r29\0"
            + b"\0" * 60
            + b"1234\0"
            + b"\0" * 59
        )
        raw = (
            "ELF Header:\n"
            "  Class:                             ELF64\n"
            "  Type:                              DYN (Shared object file)\n"
            "  Machine:                           AArch64\n"
            "Program Headers:\n"
            "  LOAD 0x000000 0x0000000000000000 0x0 0x10 0x10 R E 0x4000\n"
            "Dynamic section contains 3 entries:\n"
            "  0x1 (NEEDED) Shared library: [libc.so]\n"
            "  0xe (SONAME) Library soname: [libfixture.so]\n"
            "Symbol table '.dynsym' contains 3 entries:\n"
            "  Num: Value Size Type Bind Vis Ndx Name\n"
            "  1: 0000000000000000 0 FUNC GLOBAL DEFAULT UND memcpy@LIBC\n"
            "  2: 0000000000004000 8 FUNC GLOBAL DEFAULT 7 fixture_export\n"
            "Displaying notes found in: .note.android.ident\n"
            " description data: "
            + android_note.hex(" ")
            + "\n"
        ).encode("ascii")
        parsed = native_build_executor_tool._parse_readelf_output(  # noqa: SLF001
            raw,
            "fixture",
        )
        self.assertEqual(64, parsed.elf_class)
        self.assertEqual("ET_DYN", parsed.elf_type)
        self.assertEqual("AArch64", parsed.machine)
        self.assertEqual("libfixture.so", parsed.soname)
        self.assertEqual(("libc.so",), parsed.needed)
        self.assertEqual(frozenset({"memcpy"}), parsed.undefined)
        self.assertEqual(frozenset({"fixture_export"}), parsed.exports)
        self.assertEqual("r29", parsed.android_ident["ndkVersion"])  # type: ignore[index]

    def test_android_ident_requires_r29_only_for_built_artifacts(self) -> None:
        parsed = SimpleNamespace(android_ident={"ndkVersion": "r28"})
        runtime = SimpleNamespace(
            abi="arm64-v8a",
            library="libc++_shared.so",
            source_kind="locked-ndk-runtime",
        )
        native_build_executor_tool._validate_android_ident(runtime, parsed)  # noqa: SLF001

        built = SimpleNamespace(
            abi="arm64-v8a",
            library="libmpv.so",
            source_kind="built",
        )
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_executor_tool._validate_android_ident(built, parsed)  # noqa: SLF001
        self.assertIn("does not report NDK r29", str(raised.exception))

    def test_audit_cleanup_kills_the_process_group_after_leader_exit(self) -> None:
        process = Mock(pid=4242)
        with (
            patch.object(
                native_build_executor_tool.signal,
                "SIGKILL",
                9,
                create=True,
            ),
            patch.object(
                native_build_executor_tool.signal,
                "pidfd_send_signal",
                create=True,
            ) as pidfd_send_signal,
            patch.object(native_build_executor_tool.os, "killpg", create=True) as killpg,
        ):
            native_build_executor_tool._terminate_audit_process(process, 17)  # noqa: SLF001
        pidfd_send_signal.assert_called_once_with(17, 9)
        killpg.assert_called_once_with(4242, 9)
        process.wait.assert_called_once_with(timeout=10)

    def test_audit_cleanup_still_kills_group_when_pidfd_signal_fails(self) -> None:
        process = Mock(pid=4242)
        with (
            patch.object(
                native_build_executor_tool.signal,
                "SIGKILL",
                9,
                create=True,
            ),
            patch.object(
                native_build_executor_tool.signal,
                "pidfd_send_signal",
                side_effect=OSError("pidfd failure"),
                create=True,
            ),
            patch.object(
                native_build_executor_tool.os,
                "killpg",
                create=True,
            ) as killpg,
        ):
            with self.assertRaises(OSError):
                native_build_executor_tool._terminate_audit_process(process, 17)  # noqa: SLF001
        killpg.assert_called_once_with(4242, 9)
        process.wait.assert_called_once_with(timeout=10)

    def test_audit_rejects_reserved_or_duplicate_descriptors_before_launch(self) -> None:
        for tool_descriptor, target_descriptor in ((2, 4), (4, 4), (4, 255)):
            with self.subTest(
                tool_descriptor=tool_descriptor,
                target_descriptor=target_descriptor,
            ):
                with patch.object(native_build_executor_tool.subprocess, "Popen") as popen:
                    with self.assertRaises(source_tool.SourceToolError):
                        native_build_executor_tool._run_audit_tool(  # noqa: SLF001
                            tool_descriptor,
                            target_descriptor,
                            ["--file-header"],
                            "fixture",
                        )
                popen.assert_not_called()

    def test_build_receipt_is_exact_and_remains_nonrelease(self) -> None:
        loaded = self.load_policy()
        artifacts = []
        for abi_record in loaded.profile.abis:
            abi = str(abi_record["name"])
            for library in loaded.profile.build["expectedLibraries"]:
                name = str(library)
                artifacts.append(
                    native_build_executor_tool.PinnedArtifact(
                        abi=abi,
                        library=name,
                        source_kind=(
                            "locked-ndk-runtime" if name == "libc++_shared.so" else "built"
                        ),
                        source_relative_path=f"fixture/{name}",
                        descriptor=-1,
                        identity=(1, len(artifacts) + 1),
                        size=100 + len(artifacts),
                        sha256=hashlib.sha256(name.encode()).hexdigest(),
                        logical_path=Path("/fixture") / name,
                        resolved_path=Path("/fixture") / name,
                        logical_signature=(),
                        audit={"soname": name},
                    )
                )
        inputs = SimpleNamespace(
            policy=loaded,
            preparation=SimpleNamespace(
                source_receipt_raw=b"source",
                composition_receipt_raw=b"composition",
            ),
            preparation_receipt_raw=b"preparation",
        )
        execution = native_build_executor_tool.BuildExecutionResult(
            stdout=b"stdout",
            stderr=b"stderr",
            return_code=0,
            initial_free_bytes=1000,
            final_free_bytes=900,
            initial_free_inodes=100,
            final_free_inodes=90,
            cgroup_empty_after_exit=True,
            cgroup_removed=True,
        )
        receipt = native_build_executor_tool._build_receipt_data(  # noqa: SLF001
            inputs,
            "a" * 64,
            execution,
            artifacts,
            [],
        )
        self.assertEqual(
            native_build_executor_tool.BUILD_RECEIPT_FIELDS,
            tuple(sorted(receipt)),
        )
        self.assertEqual(18, len(receipt["artifacts"]))
        self.assertIs(receipt["buildExecuted"], True)
        self.assertIs(receipt["artifactStaged"], True)
        self.assertIs(receipt["artifactAudited"], True)
        self.assertIs(receipt["ready"], False)
        self.assertIs(receipt["releaseInput"], False)

    def test_build_process_arguments_preserve_exact_descriptor_contract(self) -> None:
        loaded = self.load_policy()
        helpers = loaded.data["helpers"]
        assert isinstance(helpers, dict)
        descriptors = {
            "workspace": 5,
            "source": 6,
            "output": 7,
            "home": 8,
            "tmp": 9,
        }
        directories = {
            key: SimpleNamespace(descriptor=value, identity=(1, value))
            for key, value in descriptors.items()
        }
        files = {}
        for path_key, descriptor in (
            ("namespacePath", 10),
            ("runnerPath", 11),
            ("seccompPath", 12),
            ("launcherPath", 13),
        ):
            relative = str(helpers[path_key])
            files[f"helper:{relative}"] = SimpleNamespace(
                descriptor=descriptor,
                raw=loaded.helper_raws[relative],
            )
        inputs = SimpleNamespace(
            policy=loaded,
            build_workspace=Path("/workspace"),
            composition_inputs=SimpleNamespace(
                apt_fd=3,
                sdk_fd=4,
                apt_root=Path("/apt"),
                sdk_root=Path("/sdk"),
                apt_identity=(1, 3),
                sdk_identity=(1, 4),
            ),
            directories=directories,
            files=files,
        )
        with patch.object(
            native_build_executor_tool.composition_tool,
            "_namespace_id",
            side_effect=lambda name: f"{name}:[1]",
        ), patch.object(
            native_build_executor_tool.native_executor_tool,
            "_namespace_path",
            side_effect=lambda path, _label: "/" + path.name,
        ):
            argv, pass_fds = native_build_executor_tool._build_process_arguments(  # noqa: SLF001
                inputs,
                14,
            )
        self.assertEqual(12, len(pass_fds))
        self.assertEqual(12, len(set(pass_fds)))
        self.assertEqual(native_build_executor_tool.CHILD_PROFILE, argv[5])
        namespace_script = f"/proc/self/fd/{files['helper:' + str(helpers['namespacePath'])].descriptor}"
        script_index = argv.index(namespace_script)
        self.assertEqual(29, len(argv[script_index + 1 :]))

    def test_execute_pre_marker_reverification_failure_never_consumes_or_launches(self) -> None:
        loaded = self.load_policy()
        preparation_raw = b"preparation"
        composition_raw = b"composition"
        data = copy.deepcopy(loaded.data)
        data["binding"]["preparationReceiptSha256"] = hashlib.sha256(preparation_raw).hexdigest()  # type: ignore[index]
        data["binding"]["toolchainCompositionReceiptSha256"] = hashlib.sha256(composition_raw).hexdigest()  # type: ignore[index]
        selected = replace(loaded, data=data)
        fake_inputs = SimpleNamespace(close=Mock())
        failure = source_tool.SourceToolError("pre-marker drift", source_tool.EXIT_INTEGRITY)
        marker = Mock()
        runner = Mock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_require_linux_root"))
            stack.enter_context(patch.object(native_build_executor_tool, "load_build_execution_policy", return_value=selected))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_resolved_build_workspace", return_value=(Path("/fixture"), Path("/fixture/workspace"))))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "preflight", return_value=selected.profile))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_snapshot_preparation_inputs", return_value=SimpleNamespace(composition_receipt_raw=composition_raw)))
            stack.enter_context(patch.object(native_build_executor_tool.native_executor_tool, "_snapshot_probe_directories", return_value={}))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_verify_prepared_workspace", return_value=({}, preparation_raw)))
            stack.enter_context(patch.object(native_build_executor_tool, "_verify_io_device_scope"))
            stack.enter_context(patch.object(native_build_executor_tool, "_verify_audit_tool", return_value=(Path("/audit"), b"audit")))
            stack.enter_context(patch.object(native_build_executor_tool, "_pin_build_inputs", return_value=fake_inputs))
            stack.enter_context(patch.object(native_build_executor_tool, "_reverify_build_inputs", side_effect=failure))
            stack.enter_context(patch.object(native_build_executor_tool, "_publish_attempt_marker", marker))
            stack.enter_context(patch.object(native_build_executor_tool, "_run_locked_build", runner))
            with self.assertRaises(source_tool.SourceToolError):
                native_build_executor_tool.execute(*((Path("/fixture"),) * 11))
        marker.assert_not_called()
        runner.assert_not_called()
        fake_inputs.close.assert_called_once_with()

    def test_execute_build_failure_cannot_publish_receipt(self) -> None:
        loaded = self.load_policy()
        preparation_raw = b"preparation"
        composition_raw = b"composition"
        data = copy.deepcopy(loaded.data)
        data["binding"]["preparationReceiptSha256"] = hashlib.sha256(preparation_raw).hexdigest()  # type: ignore[index]
        data["binding"]["toolchainCompositionReceiptSha256"] = hashlib.sha256(composition_raw).hexdigest()  # type: ignore[index]
        selected = replace(loaded, data=data)
        fake_inputs = SimpleNamespace(close=Mock())
        build_failure = source_tool.SourceToolError("build failed", source_tool.EXIT_INTEGRITY)
        receipt_publisher = Mock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_require_linux_root"))
            stack.enter_context(patch.object(native_build_executor_tool, "load_build_execution_policy", return_value=selected))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_resolved_build_workspace", return_value=(Path("/fixture"), Path("/fixture/workspace"))))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "preflight", return_value=selected.profile))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_snapshot_preparation_inputs", return_value=SimpleNamespace(composition_receipt_raw=composition_raw)))
            stack.enter_context(patch.object(native_build_executor_tool.native_executor_tool, "_snapshot_probe_directories", return_value={}))
            stack.enter_context(patch.object(native_build_executor_tool.native_build_tool, "_verify_prepared_workspace", return_value=({}, preparation_raw)))
            stack.enter_context(patch.object(native_build_executor_tool, "_verify_io_device_scope"))
            stack.enter_context(patch.object(native_build_executor_tool, "_verify_audit_tool", return_value=(Path("/audit"), b"audit")))
            stack.enter_context(patch.object(native_build_executor_tool, "_pin_build_inputs", return_value=fake_inputs))
            stack.enter_context(patch.object(native_build_executor_tool, "_reverify_build_inputs"))
            stack.enter_context(patch.object(native_build_executor_tool, "_publish_attempt_marker", return_value=(b"marker", "a" * 64)))
            stack.enter_context(patch.object(native_build_executor_tool, "_run_locked_build", side_effect=build_failure))
            stack.enter_context(patch.object(native_build_executor_tool, "_publish_build_receipt", receipt_publisher))
            with self.assertRaises(source_tool.SourceToolError):
                native_build_executor_tool.execute(*((Path("/fixture"),) * 11))
        receipt_publisher.assert_not_called()
        fake_inputs.close.assert_called_once_with()

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "requires a root Linux filesystem",
    )
    def test_linux_attempt_marker_and_receipt_are_no_replace_and_durable(self) -> None:
        loaded = self.load_policy()
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            workspace_path = Path(temporary)
            os.chmod(workspace_path, 0o700)
            for name in ("source", "output", "home", "tmp"):
                (workspace_path / name).mkdir()
            (workspace_path / native_build_executor_tool.native_build_tool.PREPARATION_RECEIPT_NAME).write_bytes(
                b"preparation"
            )
            workspace = native_build_executor_tool.native_executor_tool._open_pinned_directory(  # noqa: SLF001
                workspace_path,
                label="test workspace",
                expected_mode=0o700,
            )
            inputs = SimpleNamespace(
                policy=loaded,
                preparation=SimpleNamespace(composition_receipt_raw=b"composition"),
                preparation_receipt_raw=b"preparation",
                build_workspace=workspace_path,
                directories={"workspace": workspace},
                files={},
            )
            try:
                marker_raw, marker_sha = native_build_executor_tool._publish_attempt_marker(  # noqa: SLF001
                    inputs
                )
                self.assertEqual(hashlib.sha256(marker_raw).hexdigest(), marker_sha)
                marker_path = workspace_path / loaded.data["workspace"]["attemptMarkerName"]  # type: ignore[index]
                marker_info = marker_path.lstat()
                self.assertEqual(0o600, stat.S_IMODE(marker_info.st_mode))
                self.assertEqual(0, marker_info.st_uid)
                self.assertEqual(0, marker_info.st_gid)
                self.assertEqual(1, marker_info.st_nlink)
                self.assertEqual(
                    loaded.data["workspace"]["attemptMarkerNormalizedMtimeNs"],  # type: ignore[index]
                    marker_info.st_mtime_ns,
                )
                with self.assertRaises(source_tool.SourceToolError):
                    native_build_executor_tool._publish_attempt_marker(inputs)  # noqa: SLF001

                receipt_path = workspace_path / loaded.data["receipt"]["name"]  # type: ignore[index]
                publication_failure = source_tool.SourceToolError(
                    "fixture post-publication failure",
                    source_tool.EXIT_INTEGRITY,
                )
                with patch.object(
                    native_build_executor_tool,
                    "_assert_build_file_pin",
                    side_effect=publication_failure,
                ):
                    with self.assertRaises(source_tool.SourceToolError):
                        native_build_executor_tool._publish_build_receipt(  # noqa: SLF001
                            inputs,
                            {"kind": "rolled-back-receipt"},
                        )
                self.assertFalse(receipt_path.exists())
                self.assertFalse(any(path.name.endswith(".part") for path in workspace_path.iterdir()))

                receipt_raw, receipt_sha = native_build_executor_tool._publish_build_receipt(  # noqa: SLF001
                    inputs,
                    {"kind": "test-receipt"},
                )
                self.assertEqual(hashlib.sha256(receipt_raw).hexdigest(), receipt_sha)
                receipt_info = receipt_path.lstat()
                self.assertEqual(0o644, stat.S_IMODE(receipt_info.st_mode))
                self.assertEqual(
                    loaded.data["receipt"]["normalizedMtimeNs"],  # type: ignore[index]
                    receipt_info.st_mtime_ns,
                )
                with self.assertRaises(source_tool.SourceToolError):
                    native_build_executor_tool._publish_build_receipt(  # noqa: SLF001
                        inputs,
                        {"kind": "replacement"},
                    )
                self.assertEqual(
                    {"kind": "test-receipt"},
                    json.loads(receipt_path.read_text(encoding="utf-8")),
                )
            finally:
                for pinned in inputs.files.values():
                    if pinned.descriptor >= 0:
                        os.close(pinned.descriptor)
                        pinned.descriptor = -1
                os.close(workspace.descriptor)
                workspace.descriptor = -1

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "requires a root Linux filesystem",
    )
    def test_linux_staging_publishes_exact_per_abi_trees(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            root = Path(temporary)
            source_path = root / "source"
            output_path = root / "output"
            source_path.mkdir(mode=0o755)
            output_path.mkdir(mode=0o700)
            source_pin = native_build_executor_tool.native_executor_tool._open_pinned_directory(  # noqa: SLF001
                source_path,
                label="test source",
                expected_mode=0o755,
            )
            output_pin = native_build_executor_tool.native_executor_tool._open_pinned_directory(  # noqa: SLF001
                output_path,
                label="test output",
                expected_mode=0o700,
            )
            artifacts = []
            try:
                for index, abi in enumerate(("arm64-v8a", "x86_64"), start=1):
                    abi_source = source_path / abi
                    abi_source.mkdir(mode=0o755)
                    library_path = abi_source / "libfixture.so"
                    library_path.write_bytes(f"fixture-{abi}".encode("ascii"))
                    artifacts.append(
                        native_build_executor_tool._open_pinned_artifact(  # noqa: SLF001
                            abi=abi,
                            library="libfixture.so",
                            source_kind="built",
                            root_path=source_path,
                            root_descriptor=source_pin.descriptor,
                            logical_path=library_path,
                            maximum=1024,
                        )
                    )
                    self.assertEqual(index, len(artifacts))
                fake_inputs = SimpleNamespace(
                    policy=SimpleNamespace(
                        data={
                            "artifacts": {
                                "abiDirectoryMode": 0o755,
                                "libraryMode": 0o644,
                                "normalizedMtimeNs": 946684800000000000,
                            }
                        },
                        profile=SimpleNamespace(
                            abis=(
                                {"name": "arm64-v8a"},
                                {"name": "x86_64"},
                            )
                        ),
                    ),
                    directories={"output": output_pin},
                )
                native_build_executor_tool._stage_artifacts(fake_inputs, artifacts)  # noqa: SLF001
                self.assertEqual(["arm64-v8a", "x86_64"], sorted(os.listdir(output_path)))
                for artifact in artifacts:
                    staged = output_path / artifact.abi / artifact.library
                    self.assertEqual(artifact.sha256, hashlib.sha256(staged.read_bytes()).hexdigest())
                    info = staged.lstat()
                    self.assertEqual(0o644, stat.S_IMODE(info.st_mode))
                    self.assertEqual(946684800000000000, info.st_mtime_ns)
                    artifact.staged_pin = native_build_executor_tool._open_pinned_artifact(  # noqa: SLF001
                        abi=artifact.abi,
                        library=artifact.library,
                        source_kind="staged",
                        root_path=output_path,
                        root_descriptor=output_pin.descriptor,
                        logical_path=staged,
                        maximum=1024,
                        expected_size=artifact.size,
                        expected_sha256=artifact.sha256,
                    )
                native_build_executor_tool._reverify_staged_artifacts(  # noqa: SLF001
                    fake_inputs,
                    artifacts,
                )

                replaced = output_path / "arm64-v8a" / "libfixture.so"
                replaced.unlink()
                replaced.write_bytes(b"replacement")
                with self.assertRaises(source_tool.SourceToolError):
                    native_build_executor_tool._reverify_staged_artifacts(  # noqa: SLF001
                        fake_inputs,
                        artifacts,
                    )
            finally:
                for artifact in artifacts:
                    artifact.close()
                os.close(output_pin.descriptor)
                output_pin.descriptor = -1
                os.close(source_pin.descriptor)
                source_pin.descriptor = -1

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "requires a root Linux filesystem",
    )
    def test_linux_locked_readelf_parses_real_api26_stubs_and_runtime(self) -> None:
        sdk_root = native_build_executor_tool.native_build_tool.DEFAULT_SDK_ROOT
        if not sdk_root.exists():
            self.skipTest("locked SDK projection is unavailable")
        loaded = self.load_policy()
        tool_path, _tool_raw = native_build_executor_tool._verify_audit_tool(  # noqa: SLF001
            loaded,
            sdk_root,
        )
        toolchain_root = (
            sdk_root
            / "android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64"
        )
        tool_fd = os.open(tool_path, os.O_RDONLY)
        try:
            for abi_record in loaded.profile.abis:
                abi = str(abi_record["name"])
                runtime_directory = str(abi_record["ndkRuntimeDirectory"])
                stub_root = toolchain_root / "sysroot/usr/lib" / runtime_directory / "26"
                for soname in loaded.data["audit"]["platformSonameAllowlist"]:  # type: ignore[index]
                    label = f"real {abi} API 26 {soname} stub"
                    stub_fd = os.open(stub_root / str(soname), os.O_RDONLY)
                    try:
                        output = native_build_executor_tool._run_audit_tool(  # noqa: SLF001
                            tool_fd,
                            stub_fd,
                            list(loaded.data["audit"]["elfToolArguments"]),  # type: ignore[index]
                            label,
                        )
                        parsed = native_build_executor_tool._parse_readelf_output(  # noqa: SLF001
                            output,
                            label,
                        )
                    finally:
                        os.close(stub_fd)
                    self.assertEqual(abi_record["elfClass"], parsed.elf_class)
                    self.assertEqual(abi_record["elfMachine"], parsed.machine)
                    self.assertEqual("ET_DYN", parsed.elf_type)
                    self.assertEqual(soname, parsed.soname)

                runtime_path = (
                    toolchain_root
                    / "sysroot/usr/lib"
                    / runtime_directory
                    / "libc++_shared.so"
                )
                runtime_fd = os.open(runtime_path, os.O_RDONLY)
                try:
                    output = native_build_executor_tool._run_audit_tool(  # noqa: SLF001
                        tool_fd,
                        runtime_fd,
                        list(loaded.data["audit"]["elfToolArguments"]),  # type: ignore[index]
                        f"real {abi} locked libc++ runtime",
                    )
                    runtime = native_build_executor_tool._parse_readelf_output(  # noqa: SLF001
                        output,
                        f"real {abi} locked libc++ runtime",
                    )
                finally:
                    os.close(runtime_fd)
                self.assertEqual("libc++_shared.so", runtime.soname)
                self.assertTrue(runtime.load_segments)
                self.assertTrue(
                    all(segment["alignmentBytes"] == 16384 for segment in runtime.load_segments)
                )
                self.assertEqual("r28", runtime.android_ident["ndkVersion"])  # type: ignore[index]
                native_build_executor_tool._validate_android_ident(  # noqa: SLF001
                    SimpleNamespace(
                        abi=abi,
                        library="libc++_shared.so",
                        source_kind="locked-ndk-runtime",
                    ),
                    runtime,
                )
        finally:
            os.close(tool_fd)

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

    def test_cli_exposes_explicit_one_shot_execution(self) -> None:
        parser = native_build_executor_tool._parser()  # noqa: SLF001
        subparsers = [
            action
            for action in parser._actions  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
        ]
        self.assertEqual(1, len(subparsers))
        self.assertEqual(
            {"validate", "verify-inputs", "execute"},
            set(subparsers[0].choices),
        )

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
