# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import native_executor_tool  # noqa: E402
import source_tool  # noqa: E402


COMMITTED_POLICY = REPOSITORY_ROOT / "native" / "native-executor-policy.toml"
COMMITTED_PROFILE = REPOSITORY_ROOT / "native" / "native-build-profile.toml"
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"
COMMITTED_TOOLCHAIN_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"


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
        return [(entry, None) for entry in tuple(self.mapping.values())]

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
        self.pid = 4242

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode


class StubbornProcess:
    pid = 4242
    returncode = None

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired("fixture", timeout)


def fixture_policy() -> native_executor_tool.LoadedExecutorPolicy:
    return native_executor_tool.load_execution_policy(
        COMMITTED_POLICY,
        COMMITTED_PROFILE,
        COMMITTED_SOURCE_MANIFEST,
        COMMITTED_TOOLCHAIN_MANIFEST,
    )


def fixture_argument_inputs() -> SimpleNamespace:
    policy = fixture_policy()
    helper_descriptors = iter((10, 11, 12, 13))
    files = {
        f"helper:{relative}": SimpleNamespace(
            descriptor=next(helper_descriptors),
            raw=raw,
        )
        for relative, raw in policy.helper_raws.items()
    }
    directories = {
        "workspace": SimpleNamespace(descriptor=5, identity=(2096, 100), label="workspace"),
        "source": SimpleNamespace(descriptor=6, identity=(2096, 101), label="source"),
        "output": SimpleNamespace(descriptor=7, identity=(2096, 102), label="output"),
        "home": SimpleNamespace(descriptor=8, identity=(2096, 103), label="home"),
        "tmp": SimpleNamespace(descriptor=9, identity=(2096, 104), label="tmp"),
    }
    return SimpleNamespace(
        policy=policy,
        files=files,
        directories=directories,
        build_workspace=PurePosixPath("/var/tmp/zivplayer-native-build"),
        composition_inputs=SimpleNamespace(
            apt_root=PurePosixPath("/var/tmp/zivplayer-toolchain-apt"),
            sdk_root=PurePosixPath("/var/tmp/zivplayer-sdk-projection"),
            apt_fd=3,
            sdk_fd=4,
            apt_identity=(2096, 200),
            sdk_identity=(2096, 201),
        ),
    )


class NativeExecutorPolicyTest(unittest.TestCase):
    def assert_schema_error(self, data: dict[str, object], fragment: str) -> None:
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool.validate_policy_data(data)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)
        self.assertIn(fragment, str(raised.exception))

    def run_fake_namespace_probe(
        self,
        child_stdout: bytes,
        child_stderr: bytes,
        return_code: int,
        *,
        reserve_error: OSError | None = None,
        pidfd_error: OSError | None = None,
    ) -> tuple[
        bytes | None,
        BaseException | None,
        object,
        SimpleNamespace,
        object,
        object,
    ]:
        policy = fixture_policy()
        workspace_descriptor = os.open(os.devnull, os.O_RDONLY)
        cgroup = SimpleNamespace(
            procs_fd=os.open(os.devnull, os.O_WRONLY),
            removed=False,
            name=".zivplayer-apt-fixture",
        )
        reserve_pidfd = os.open(os.devnull, os.O_RDONLY)
        pidfd = os.open(os.devnull, os.O_RDONLY)
        process = FakeProcess(child_stdout, child_stderr, return_code)
        inputs = SimpleNamespace(
            policy=policy,
            directories={"workspace": SimpleNamespace(descriptor=workspace_descriptor)},
        )

        def remove_cgroup(handle: SimpleNamespace) -> None:
            handle.removed = True

        result: bytes | None = None
        error: BaseException | None = None

        def open_pidfd(pid: int, _flags: int) -> int:
            if pid == os.getpid():
                if reserve_error is not None:
                    raise reserve_error
                return reserve_pidfd
            if pidfd_error is not None:
                raise pidfd_error
            return pidfd

        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    patch("native_executor_tool._assert_pinned_probe_inputs")
                )
                stack.enter_context(
                    patch("native_executor_tool._assert_host_resource_limits")
                )
                stack.enter_context(
                    patch(
                        "native_executor_tool.os.statvfs",
                        return_value=SimpleNamespace(
                            f_bavail=16 * 1024 * 1024 * 1024,
                            f_frsize=1,
                            f_favail=1_000_000,
                        ),
                        create=True,
                    )
                )
                stack.enter_context(
                    patch(
                        "native_executor_tool.environment_tool._prepare_installer_cgroup",
                        return_value=cgroup,
                    )
                )
                stack.enter_context(
                    patch(
                        "native_executor_tool._probe_process_arguments",
                        return_value=(["/fixture/child"], (cgroup.procs_fd,)),
                    )
                )
                stack.enter_context(
                    patch(
                        "native_executor_tool.environment_tool._cgroup_limit_violation",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch(
                        "native_executor_tool.environment_tool._cgroup_is_empty",
                        return_value=True,
                    )
                )
                stack.enter_context(
                    patch(
                        "native_executor_tool.environment_tool._remove_installer_cgroup",
                        side_effect=remove_cgroup,
                    )
                )
                kill_cgroup = stack.enter_context(
                    patch("native_executor_tool.environment_tool._kill_installer_cgroup")
                )
                stack.enter_context(
                    patch("native_executor_tool._filesystem_violation", return_value=None)
                )
                stack.enter_context(
                    patch(
                        "native_executor_tool.selectors.DefaultSelector",
                        side_effect=FakeSelector,
                    )
                )
                stack.enter_context(patch("native_executor_tool.os.set_blocking"))
                popen = stack.enter_context(
                    patch("native_executor_tool.subprocess.Popen", return_value=process)
                )
                stack.enter_context(
                    patch.object(
                        os,
                        "pidfd_open",
                        side_effect=open_pidfd,
                        create=True,
                    )
                )
                stack.enter_context(
                    patch.object(signal, "pidfd_send_signal", create=True)
                )
                stack.enter_context(patch.object(signal, "SIG_BLOCK", 0, create=True))
                stack.enter_context(
                    patch.object(signal, "SIG_SETMASK", 2, create=True)
                )
                signal_mask = stack.enter_context(
                    patch.object(
                        signal,
                        "pthread_sigmask",
                        return_value=set(),
                        create=True,
                    )
                )
                try:
                    result = native_executor_tool._run_namespace_probe(inputs)
                except BaseException as caught:
                    error = caught
        finally:
            os.close(workspace_descriptor)
            if cgroup.procs_fd >= 0:
                os.close(cgroup.procs_fd)
                cgroup.procs_fd = -1
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
            try:
                os.close(pidfd)
            except OSError:
                pass
            try:
                os.close(reserve_pidfd)
            except OSError:
                pass
        return result, error, popen, cgroup, signal_mask, kill_cgroup

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
            Path(relative).name
            for relative in native_executor_tool.EXPECTED_POLICY["helpers"].values()  # type: ignore[union-attr]
            if isinstance(relative, str) and relative.startswith("native/")
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
        launcher_raw = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-executor-child.py"
        ).read_bytes()
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
        self.assertIn(
            b"len(command) <= len(EXPECTED_COMMAND_PREFIX)",
            launcher_raw,
        )
        self.assertIn(
            b"pthread_sigmask(signal.SIG_UNBLOCK, CONTROL_SIGNALS)",
            launcher_raw,
        )
        self.assertLess(
            launcher_raw.index(b"libc.prctl(1, signal.SIGKILL"),
            launcher_raw.index(b"separator = argv.index"),
        )

    def test_cli_exposes_only_validation_and_probe(self) -> None:
        parser = native_executor_tool._parser()  # noqa: SLF001
        subparsers = [
            action
            for action in parser._actions  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
        ]
        self.assertEqual(1, len(subparsers))
        self.assertEqual({"probe", "validate"}, set(subparsers[0].choices))

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

    def test_probe_transcript_is_exact_and_build_closed(self) -> None:
        parsed = native_executor_tool._parse_probe_transcript(
            native_executor_tool.EXPECTED_PROBE_TRANSCRIPT
        )
        self.assertEqual(dict(native_executor_tool.PROBE_RECORDS), parsed)
        for changed in (
            native_executor_tool.EXPECTED_PROBE_TRANSCRIPT[:-1],
            native_executor_tool.EXPECTED_PROBE_TRANSCRIPT.replace(b"private", b"shared", 1),
            native_executor_tool.EXPECTED_PROBE_TRANSCRIPT + b"build\texecuted\n",
        ):
            with self.subTest(changed=changed[-32:]):
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    native_executor_tool._parse_probe_transcript(changed)
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_probe_arguments_use_locked_child_and_namespace_descriptors(self) -> None:
        inputs = fixture_argument_inputs()
        with patch(
            "native_executor_tool.composition_tool._namespace_id",
            side_effect=lambda name: f"{name}:[1]",
        ):
            argv, pass_fds = native_executor_tool._probe_process_arguments(inputs, 14)
        self.assertEqual(
            ["/usr/bin/python3.12", "-I", "-S", "-B"],
            argv[:4],
        )
        self.assertEqual(native_executor_tool.CHILD_PROFILE, argv[5])
        self.assertEqual("--", argv[13])
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
            argv[14:27],
        )
        self.assertEqual(12, len(pass_fds))
        self.assertEqual(len(pass_fds), len(set(pass_fds)))
        self.assertNotIn("buildall.sh", " ".join(argv))

    def test_runtime_policy_matches_shared_cgroup_and_rlimit_constants(self) -> None:
        policy = fixture_policy()
        native_executor_tool._assert_runtime_policy(policy)
        with patch.object(
            native_executor_tool.environment_tool,
            "CGROUP_PIDS_MAX",
            native_executor_tool.environment_tool.CGROUP_PIDS_MAX + 1,
        ), self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._assert_runtime_policy(policy)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_run_namespace_probe_has_bounded_fixed_launch_without_preexec(self) -> None:
        result, error, popen, cgroup, signal_mask, _kill_cgroup = self.run_fake_namespace_probe(
            native_executor_tool.EXPECTED_PROBE_TRANSCRIPT,
            b"",
            0,
        )
        self.assertIsNone(error)
        self.assertEqual(native_executor_tool.EXPECTED_PROBE_TRANSCRIPT, result)
        self.assertIs(cgroup.removed, True)
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
        self.assertIs(popen.call_args.kwargs["start_new_session"], True)
        self.assertNotIn("preexec_fn", popen.call_args.kwargs)
        self.assertEqual(2, signal_mask.call_count)
        self.assertEqual(
            native_executor_tool.CONTROL_SIGNALS,
            signal_mask.call_args_list[0].args[1],
        )

    def test_run_namespace_probe_rejects_nonzero_and_stderr(self) -> None:
        _result, error, _popen, cgroup, _signal_mask, _kill_cgroup = self.run_fake_namespace_probe(
            b"partial\n",
            b"fixture failure\n",
            7,
        )
        self.assertIsInstance(error, source_tool.SourceToolError)
        self.assertEqual(source_tool.EXIT_INTEGRITY, error.exit_code)
        self.assertIn("exited 7", str(error))
        self.assertIs(cgroup.removed, True)

    def test_pidfd_open_failure_is_preserved_and_cgroup_cleanup_runs(self) -> None:
        (
            _result,
            error,
            _popen,
            cgroup,
            signal_mask,
            kill_cgroup,
        ) = self.run_fake_namespace_probe(
            b"",
            b"",
            0,
            pidfd_error=OSError("fixture pidfd failure"),
        )
        self.assertIsInstance(error, source_tool.SourceToolError)
        self.assertEqual(source_tool.EXIT_INTEGRITY, error.exit_code)
        self.assertIn("pidfd", str(error))
        self.assertNotIsInstance(error, KeyError)
        self.assertTrue(kill_cgroup.called)
        self.assertIs(cgroup.removed, True)
        self.assertEqual(2, signal_mask.call_count)

    def test_pidfd_capacity_is_reserved_before_cgroup_or_process_launch(self) -> None:
        (
            _result,
            error,
            popen,
            cgroup,
            signal_mask,
            kill_cgroup,
        ) = self.run_fake_namespace_probe(
            b"",
            b"",
            0,
            reserve_error=OSError("fixture pidfd reserve failure"),
        )
        self.assertIsInstance(error, source_tool.SourceToolError)
        self.assertEqual(source_tool.EXIT_INTEGRITY, error.exit_code)
        self.assertIn("reserve", str(error))
        self.assertFalse(popen.called)
        self.assertFalse(kill_cgroup.called)
        self.assertIs(cgroup.removed, False)
        self.assertEqual(2, signal_mask.call_count)

    def test_cleanup_attempts_pidfd_kill_when_cgroup_operations_fail(self) -> None:
        process = StubbornProcess()
        cgroup = SimpleNamespace(name=".zivplayer-apt-fixture", removed=False)
        with (
            patch.object(signal, "SIGKILL", 9, create=True),
            patch("native_executor_tool._send_pidfd_signal") as send_signal,
            patch(
                "native_executor_tool.environment_tool._kill_installer_cgroup",
                side_effect=OSError("fixture cgroup.kill failure"),
            ),
            patch(
                "native_executor_tool.environment_tool._cgroup_is_empty",
                side_effect=OSError("fixture cgroup read failure"),
            ),
            patch(
                "native_executor_tool.environment_tool._remove_installer_cgroup"
            ) as remove_cgroup,
            self.assertRaises(source_tool.SourceToolError) as raised,
        ):
            native_executor_tool._terminate_probe_process(process, 99, cgroup)
        self.assertEqual(
            [signal.SIGTERM, 9],
            [call.args[1] for call in send_signal.call_args_list],
        )
        self.assertFalse(remove_cgroup.called)
        self.assertIn(cgroup.name, str(raised.exception))

    def test_probe_skips_post_verification_after_run_cleanup_failure(self) -> None:
        policy = fixture_policy()
        close_calls: list[bool] = []
        inputs = SimpleNamespace(close=lambda: close_calls.append(True))
        fixture_path = Path("/fixture")
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("native_executor_tool.native_build_tool._require_linux_root")
            )
            stack.enter_context(
                patch("native_executor_tool.load_execution_policy", return_value=policy)
            )
            stack.enter_context(patch("native_executor_tool._assert_runtime_policy"))
            stack.enter_context(
                patch(
                    "native_executor_tool.native_build_tool._resolved_build_workspace",
                    return_value=(fixture_path, fixture_path / "workspace"),
                )
            )
            stack.enter_context(
                patch(
                    "native_executor_tool.native_build_tool.preflight",
                    return_value=policy.profile,
                )
            )
            stack.enter_context(
                patch(
                    "native_executor_tool.native_build_tool._snapshot_preparation_inputs",
                    return_value=object(),
                )
            )
            stack.enter_context(
                patch("native_executor_tool._snapshot_probe_directories", return_value={})
            )
            stack.enter_context(
                patch(
                    "native_executor_tool.native_build_tool._verify_prepared_workspace",
                    return_value=({}, b"fixture receipt"),
                )
            )
            stack.enter_context(
                patch("native_executor_tool._pin_probe_inputs", return_value=inputs)
            )
            reverify = stack.enter_context(
                patch("native_executor_tool._reverify_probe_inputs")
            )
            stack.enter_context(
                patch(
                    "native_executor_tool._run_namespace_probe",
                    side_effect=native_executor_tool.environment_tool.InstallerIsolationError(
                        "fixture cleanup failure",
                        retain_staging=True,
                    ),
                )
            )
            parse_transcript = stack.enter_context(
                patch("native_executor_tool._parse_probe_transcript")
            )
            with self.assertRaises(source_tool.SourceToolError):
                native_executor_tool.probe(
                    COMMITTED_POLICY,
                    COMMITTED_PROFILE,
                    COMMITTED_SOURCE_MANIFEST,
                    COMMITTED_TOOLCHAIN_MANIFEST,
                    fixture_path / "source-cache",
                    fixture_path / "toolchain-cache",
                    fixture_path / "source",
                    fixture_path / "apt",
                    fixture_path / "sdk",
                    fixture_path / "composition.json",
                    fixture_path / "workspace",
                )
        self.assertEqual(1, reverify.call_count)
        self.assertFalse(parse_transcript.called)
        self.assertEqual([True], close_calls)

    def test_bounded_probe_output_rejects_overflow(self) -> None:
        buffer = bytearray(b"x" * 8)
        native_executor_tool._append_bounded(
            buffer,
            b"",
            maximum=8,
            label="stdout",
        )
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._append_bounded(
                buffer,
                b"x",
                maximum=8,
                label="stdout",
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_pinned_file_size_is_rejected_before_reading(self) -> None:
        with patch(
            "native_executor_tool.os.pread",
            create=True,
        ) as pread, self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._pread_exact(
                3,
                native_executor_tool.MAX_PINNED_FILE_BYTES + 1,
                "fixture file",
            )
        self.assertFalse(pread.called)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_directory_baseline_rejects_verify_to_pin_replacement(self) -> None:
        baseline = {"workspace": (1, 2, 3)}
        directories = {
            "workspace": SimpleNamespace(stat_signature=(1, 2, 3)),
        }
        native_executor_tool._assert_directory_baseline(baseline, directories)
        directories["workspace"].stat_signature = (1, 2, 4)
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._assert_directory_baseline(baseline, directories)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    @unittest.skipUnless(
        sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "descriptor pin replacement checks require Linux root",
    )
    def test_pinned_directory_and_file_reject_path_or_byte_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pinned_directory_path = base / "pinned-directory"
            pinned_directory_path.mkdir(mode=0o700)
            os.chmod(pinned_directory_path, 0o700)
            pinned_directory = native_executor_tool._open_pinned_directory(
                pinned_directory_path,
                label="fixture directory",
                expected_mode=0o700,
            )
            held_directory = base / "held-directory"
            pinned_directory_path.rename(held_directory)
            pinned_directory_path.mkdir(mode=0o700)
            os.chmod(pinned_directory_path, 0o700)
            try:
                with self.assertRaises(source_tool.SourceToolError):
                    native_executor_tool._assert_pinned_directory(pinned_directory)
            finally:
                os.close(pinned_directory.descriptor)

            pinned_file_path = base / "pinned-file"
            pinned_file_path.write_bytes(b"locked")
            pinned_file = native_executor_tool._open_pinned_file(
                pinned_file_path,
                b"locked",
                label="fixture file",
            )
            pinned_file_path.write_bytes(b"change")
            try:
                with self.assertRaises(source_tool.SourceToolError):
                    native_executor_tool._assert_pinned_file(pinned_file)
            finally:
                os.close(pinned_file.descriptor)


if __name__ == "__main__":
    unittest.main()
