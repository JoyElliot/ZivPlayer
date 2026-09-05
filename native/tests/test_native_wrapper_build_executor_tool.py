# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import native_build_executor_tool  # noqa: E402
import native_build_tool  # noqa: E402
import native_wrapper_build_executor_tool  # noqa: E402
import source_tool  # noqa: E402


COMMITTED_POLICY = (
    REPOSITORY_ROOT / "native" / "native-wrapper-build-executor-policy.toml"
)
COMMITTED_PROFILE = REPOSITORY_ROOT / "native" / "native-wrapper-build-profile.toml"
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"
COMMITTED_TOOLCHAIN_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"
LOCKED_POLICY_SHA256 = "1d46ecc732f4aa548fb77b2bf63e96be66b217a8aee869ce8bbcd4626ec1401c"
LOCKED_PROFILE_SHA256 = "d6cf2a360b4c8f159e49fc9a9872faf4a21e3dc5e8a225905d3b3ccaf4fe42ce"
LOCKED_PROBE_SHA256 = "e38767eb0e8e095364d13040a9ce3f49479f9147e1a2740d4f81860c496d1647"
LOCKED_TRANSCRIPT_SHA256 = "e182d70ac1a76b00f7f3a622835c23621ba3df4654a339b945f66094f959dc9f"
LOCKED_PREPARATION_SHA256 = "e51dae00b7f145de9663ee1b90010bf9904f8775ff2f12624454040c941132b4"
EXPECTED_LIBRARIES = (
    "libavcodec.so",
    "libavdevice.so",
    "libavfilter.so",
    "libavformat.so",
    "libavutil.so",
    "libc++_shared.so",
    "libmpv.so",
    "libswresample.so",
    "libswscale.so",
    "libzivplayer_mpv.so",
)
EXPECTED_COMMANDS = (
    (
        "/build/source/buildscripts/buildall.sh",
        "--arch",
        "arm64",
        "mpv+zivplayer_mpv",
    ),
    (
        "/build/source/buildscripts/buildall.sh",
        "--arch",
        "x86_64",
        "mpv+zivplayer_mpv",
    ),
)


class FakeSelector:
    def __init__(self) -> None:
        self.mapping: dict[object, object] = {}

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
        self.mapping.clear()


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


def successful_execution(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    initial_free_bytes: int = 1,
    final_free_bytes: int = 1,
    initial_free_inodes: int = 1,
    final_free_inodes: int = 1,
) -> native_wrapper_build_executor_tool.WrapperBuildExecutionResult:
    return native_wrapper_build_executor_tool.WrapperBuildExecutionResult(
        stdout=stdout,
        stderr=stderr,
        return_code=0,
        initial_free_bytes=initial_free_bytes,
        final_free_bytes=final_free_bytes,
        initial_free_inodes=initial_free_inodes,
        final_free_inodes=final_free_inodes,
        cgroup_empty_after_exit=True,
        cgroup_removed=True,
        parent_events=(
            native_wrapper_build_executor_tool.ParentRunnerEvent(
                sequence=1,
                event="runner-launched",
                return_code=None,
            ),
            native_wrapper_build_executor_tool.ParentRunnerEvent(
                sequence=2,
                event="runner-exited",
                return_code=0,
            ),
        ),
    )


def loaded_policy() -> native_build_executor_tool.LoadedBuildExecutorPolicy:
    return native_wrapper_build_executor_tool.load_build_execution_policy(
        COMMITTED_POLICY,
        COMMITTED_PROFILE,
        COMMITTED_SOURCE_MANIFEST,
        COMMITTED_TOOLCHAIN_MANIFEST,
    )


def argument_inputs() -> SimpleNamespace:
    policy = loaded_policy()
    helper_descriptors = iter((11, 12, 13, 14))
    files = {
        f"helper:{relative}": SimpleNamespace(
            descriptor=next(helper_descriptors),
            raw=raw,
        )
        for relative, raw in policy.helper_raws.items()
    }
    directories = {
        "workspace": SimpleNamespace(descriptor=5, identity=(2096, 100)),
        "source": SimpleNamespace(descriptor=6, identity=(2096, 101)),
        "output": SimpleNamespace(descriptor=7, identity=(2096, 102)),
        "home": SimpleNamespace(descriptor=8, identity=(2096, 103)),
        "tmp": SimpleNamespace(descriptor=9, identity=(2096, 104)),
        "wrapper": SimpleNamespace(descriptor=10, identity=(2096, 105)),
    }
    return SimpleNamespace(
        policy=policy,
        files=files,
        directories=directories,
        build_workspace=PurePosixPath(
            "/var/tmp/zivplayer-native-build-wrapper-d6cf2a36-20260905-a1"
        ),
        composition_inputs=SimpleNamespace(
            apt_root=PurePosixPath("/var/tmp/zivplayer-toolchain-apt"),
            sdk_root=PurePosixPath("/var/tmp/zivplayer-sdk-projection"),
            apt_fd=3,
            sdk_fd=4,
            apt_identity=(2096, 200),
            sdk_identity=(2096, 201),
        ),
    )


def readelf_fixture() -> bytes:
    android_note = (
        (26).to_bytes(4, "little")
        + b"r29\0"
        + b"\0" * 60
        + b"1234\0"
        + b"\0" * 59
    )
    return (
        "ELF Header:\n"
        "  Class:                             ELF64\n"
        "  Type:                              DYN (Shared object file)\n"
        "  Machine:                           AArch64\n"
        "Program Headers:\n"
        "  LOAD 0x000000 0x0000000000000000 0x0 0x10 0x10 R E 0x4000\n"
        "Dynamic section contains 3 entries:\n"
        "  0x1 (NEEDED) Shared library: [libc.so]\n"
        "  0xe (SONAME) Library soname: [libzivplayer_mpv.so]\n"
        "Symbol table '.dynsym' contains 6 entries:\n"
        "  Num: Value Size Type Bind Vis Ndx Name\n"
        "  0: 0000000000000000 0 NOTYPE LOCAL DEFAULT UND\n"
        "  1: 0000000000000000 0 FUNC GLOBAL DEFAULT UND memcpy\n"
        "  2: 0000000000004000 8 FUNC GLOBAL DEFAULT 7 fixture_export\n"
        "  3: 0000000000004010 8 FUNC GLOBAL DEFAULT 7 JNI_OnLoad\n"
        "  4: 0000000000004020 8 FUNC GLOBAL DEFAULT 7 JNI_OnUnload\n"
        "  5: 0000000000000000 0 NOTYPE WEAK DEFAULT UND memfd_create\n"
        "Displaying notes found in: .note.android.ident\n"
        " description data: "
        + android_note.hex(" ")
        + "\n"
    ).encode("ascii")


def audit_record(*, library: str, machine: str, wrapper: bool) -> dict[str, object]:
    jni_exports = (
        [
            {
                "binding": "GLOBAL",
                "name": name,
                "sectionIndex": "7",
                "symbolType": "FUNC",
                "visibility": "DEFAULT",
            }
            for name in ("JNI_OnLoad", "JNI_OnUnload")
        ]
        if wrapper
        else []
    )
    return {
        "androidIdent": {"apiLevel": 26, "ndkBuild": "1234", "ndkVersion": "r29"},
        "elfClass": 64,
        "elfMachine": machine,
        "elfType": "ET_DYN",
        "jniExports": jni_exports,
        "loadSegments": [
            {"alignmentBytes": 16384, "offsetBytes": 0, "virtualAddress": 0}
        ],
        "needed": ["libc.so"],
        "neededProviders": [{"kind": "api26-platform-stub", "soname": "libc.so"}],
        "readelfSha256": "1" * 64,
        "readelfSizeBytes": 100,
        "resolvedSymbolProviders": [{"resolvedSymbolCount": 1, "soname": "libc.so"}],
        "soname": library,
        "strongUndefinedSymbolCount": 1,
        "strongUndefinedSymbolsSha256": "2" * 64,
        "undefinedSymbolCount": 1,
        "undefinedSymbolsSha256": "3" * 64,
        "unresolvedWeakSymbols": [],
        "weakUndefinedSymbolCount": 0,
        "weakUndefinedSymbolsSha256": "4" * 64,
    }


def receipt_inputs() -> tuple[SimpleNamespace, list[native_build_executor_tool.PinnedArtifact]]:
    policy = loaded_policy()
    wrapper_raws = native_build_tool._wrapper_input_snapshots(policy.profile)  # noqa: SLF001
    files = {
        f"wrapper:{record['destination']}": SimpleNamespace(raw=raw)
        for record, raw in zip(policy.profile.wrapper_inputs, wrapper_raws, strict=True)
    }
    inputs = SimpleNamespace(
        policy=policy,
        files=files,
        preparation=SimpleNamespace(
            source_receipt_raw=b"source receipt",
            composition_receipt_raw=b"composition receipt",
        ),
        preparation_receipt_raw=b"preparation receipt",
    )
    artifacts: list[native_build_executor_tool.PinnedArtifact] = []
    built = set(policy.profile.build["builtLibraries"])
    for abi in policy.profile.abis:
        abi_name = str(abi["name"])
        for library in policy.profile.build["expectedLibraries"]:
            library_name = str(library)
            wrapper = library_name == "libzivplayer_mpv.so"
            if library_name in built:
                source_root = PurePosixPath(
                    str(policy.data["artifacts"]["builtLibraryRootTemplate"]).format(
                        upstreamArch=abi["upstreamArch"]
                    )
                )
                source_mount = PurePosixPath(str(policy.data["binding"]["sourceMount"]))
            else:
                source_root = PurePosixPath(
                    str(policy.data["artifacts"]["runtimeLibraryRootTemplate"]).format(
                        ndkRuntimeDirectory=abi["ndkRuntimeDirectory"]
                    )
                )
                source_mount = PurePosixPath(
                    str(policy.data["binding"]["toolchainMount"])
                )
            source_relative_path = (
                source_root.relative_to(source_mount) / library_name
            ).as_posix()
            artifacts.append(
                native_build_executor_tool.PinnedArtifact(
                    abi=abi_name,
                    library=library_name,
                    source_kind="built" if library_name in built else "locked-ndk-runtime",
                    source_relative_path=source_relative_path,
                    descriptor=-1,
                    identity=(1, len(artifacts) + 1),
                    size=100 + len(artifacts),
                    sha256=f"{len(artifacts) + 1:064x}",
                    logical_path=Path(library_name),
                    resolved_path=Path(library_name),
                    logical_signature=(),
                    audit=audit_record(
                        library=library_name,
                        machine=str(abi["elfMachine"]),
                        wrapper=wrapper,
                    ),
                )
            )
    return inputs, artifacts


def platform_evidence(
    policy: native_build_executor_tool.LoadedBuildExecutorPolicy,
) -> list[dict[str, object]]:
    sonames = policy.data["audit"]["platformSonameAllowlist"]
    return [
        {
            "abi": abi["name"],
            "stubs": [
                {
                    "exportedSymbolCount": 1,
                    "exportedSymbolsSha256": "5" * 64,
                    "readelfSha256": "6" * 64,
                    "sha256": "7" * 64,
                    "sizeBytes": 100,
                    "soname": soname,
                }
                for soname in sonames
            ],
        }
        for abi in policy.profile.abis
    ]


class WrapperBuildPolicyTest(unittest.TestCase):
    def test_committed_policy_and_all_bindings_are_locked(self) -> None:
        policy = loaded_policy()
        self.assertEqual(LOCKED_POLICY_SHA256, policy.sha256)
        self.assertEqual(LOCKED_PROFILE_SHA256, policy.profile.sha256)
        self.assertEqual(LOCKED_PROBE_SHA256, policy.probe_policy.sha256)
        binding = policy.data["binding"]
        self.assertEqual(LOCKED_PREPARATION_SHA256, binding["preparationReceiptSha256"])
        self.assertEqual(LOCKED_TRANSCRIPT_SHA256, binding["namespaceProbeTranscriptSha256"])
        self.assertEqual(EXPECTED_LIBRARIES, tuple(policy.data["artifacts"]["expectedLibraries"]))
        self.assertEqual(EXPECTED_COMMANDS, tuple(map(tuple, policy.profile.build["commands"])))
        execution = policy.data["execution"]
        self.assertEqual("native-wrapper-build-profile", execution["commandsSource"])
        self.assertEqual("sequential-exact", execution["commandOrder"])
        self.assertEqual(31, execution["namespaceArgumentCount"])
        self.assertEqual(11, execution["preservedDescriptorCount"])
        self.assertEqual(
            [
                "/run/ziv-native-wrapper-build-runner.bash",
                "--execute-locked-wrapper-build",
            ],
            execution["runnerInvocation"],
        )
        self.assertEqual(
            "pidfd-sigterm-2s-cgroup-kill-pidfd-sigkill",
            execution["failureTeardown"],
        )
        self.assertEqual(20, len(policy.profile.abis) * len(EXPECTED_LIBRARIES))
        self.assertEqual(16, policy.data["contract"]["registrationMethodCount"])
        for path_key, digest_key in native_wrapper_build_executor_tool.HELPER_KEYS:
            path = REPOSITORY_ROOT / policy.data["helpers"][path_key]
            self.assertEqual(
                policy.data["helpers"][digest_key],
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )

    def test_schema_is_exact_and_nonrelease(self) -> None:
        policy = loaded_policy()
        gate = policy.data["gate"]
        for key in ("buildCommands", "artifactStaging", "artifactAudit", "buildReceipt"):
            self.assertIs(gate[key], True)
        self.assertIs(gate["ready"], False)
        self.assertIs(gate["releaseInput"], False)
        missing_nested = copy.deepcopy(policy.data)
        del missing_nested["execution"]["namespaceProfile"]
        boolean_as_integer = copy.deepcopy(policy.data)
        boolean_as_integer["gate"]["buildCommands"] = 1
        opened_release = copy.deepcopy(policy.data)
        opened_release["gate"]["releaseInput"] = True
        reordered_libraries = copy.deepcopy(policy.data)
        reordered_libraries["artifacts"]["expectedLibraries"][:2] = reversed(
            reordered_libraries["artifacts"]["expectedLibraries"][:2]
        )
        for changed, fragment in (
            ({**copy.deepcopy(policy.data), "unexpected": True}, "unknown unexpected"),
            (
                {
                    **copy.deepcopy(policy.data),
                    "kind": "ziv-native-build-executor-policy-v1",
                },
                "kind",
            ),
            (missing_nested, "missing namespaceProfile"),
            (boolean_as_integer, "buildCommands type"),
            (opened_release, "releaseInput"),
            (reordered_libraries, "expectedLibraries[0]"),
        ):
            with self.subTest(fragment=fragment), self.assertRaises(
                source_tool.SourceToolError
            ) as raised:
                native_wrapper_build_executor_tool.validate_policy_data(changed)
            self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)
            self.assertIn(fragment, str(raised.exception))

    def test_runner_and_namespace_are_wrapper_build_specific(self) -> None:
        runner = (
            REPOSITORY_ROOT
            / "native"
            / "toolchain"
            / "native-wrapper-build-runner.bash"
        ).read_text(encoding="utf-8")
        namespace = (
            REPOSITORY_ROOT
            / "native"
            / "toolchain"
            / "native-wrapper-build-execution-namespace.bash"
        ).read_text(encoding="utf-8")
        self.assertEqual(1, runner.count("--arch arm64 mpv+zivplayer_mpv"))
        self.assertEqual(1, runner.count("--arch x86_64 mpv+zivplayer_mpv"))
        self.assertNotIn("gradlew", runner)
        self.assertNotIn("ndk-build", runner)
        for record in loaded_policy().profile.wrapper_inputs:
            self.assertIn(str(record["sha256"]), runner)
        locked_inputs = [
            (match.group(1), int(match.group(2)), match.group(3))
            for line in runner.splitlines()
            if (
                match := re.fullmatch(
                    r"require_wrapper_file (\S+) (\d+) ([0-9a-f]{64})",
                    line,
                )
            )
        ]
        self.assertEqual(
            [
                (
                    str(record["destination"]),
                    int(record["size"]),
                    str(record["sha256"]),
                )
                for record in loaded_policy().profile.wrapper_inputs
            ],
            locked_inputs,
        )
        locked_commands = [
            (match.group(1), match.group(2))
            for line in runner.splitlines()
            if (
                match := re.fullmatch(
                    r'"\$\{build_script\}" --arch (arm64|x86_64) (\S+)',
                    line,
                )
            )
        ]
        self.assertEqual(
            [("arm64", "mpv+zivplayer_mpv"), ("x86_64", "mpv+zivplayer_mpv")],
            locked_commands,
        )
        self.assertIn('[[ "$#" -ne 31', namespace)
        self.assertIn(
            "require_mount_policy /build/wrapper ext4 'ro,nosuid,nodev,noexec' 'rw,exec'",
            namespace,
        )
        self.assertIn('[[ "${#seen_mounts[@]}" -eq 16 ]]', namespace)
        self.assertIn("/run/ziv-native-wrapper-build-runner.bash", namespace)

    def test_each_wrapper_contract_input_byte_drift_is_rejected(self) -> None:
        policy = loaded_policy()
        raws = list(native_build_tool._wrapper_input_snapshots(policy.profile))  # noqa: SLF001
        for index, record in enumerate(policy.profile.wrapper_inputs):
            changed = list(raws)
            changed[index] = changed[index][:-1] + bytes([changed[index][-1] ^ 1])
            with self.subTest(destination=record["destination"]), self.assertRaises(
                source_tool.SourceToolError
            ) as raised:
                native_wrapper_build_executor_tool._wrapper_contract_evidence(  # noqa: SLF001
                    policy.profile,
                    tuple(changed),
                    policy.data,
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_validate_cli_never_spawns_and_workspace_is_explicit(self) -> None:
        stdout = io.StringIO()
        forbidden = AssertionError("validation spawned a process")
        with contextlib.redirect_stdout(stdout), patch.object(
            subprocess,
            "Popen",
            side_effect=forbidden,
        ), patch.object(subprocess, "run", side_effect=forbidden), patch.object(
            os,
            "system",
            side_effect=forbidden,
        ):
            code = native_wrapper_build_executor_tool.main(["validate"])
        self.assertEqual(source_tool.EXIT_OK, code)
        self.assertIn("ready=false", stdout.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(
            SystemExit
        ) as raised:
            native_wrapper_build_executor_tool._parser().parse_args([  # noqa: SLF001
                "verify-inputs"
            ])
        self.assertEqual(2, raised.exception.code)


class WrapperBuildArgumentTest(unittest.TestCase):
    def test_process_arguments_are_exactly_31_11_and_13(self) -> None:
        inputs = argument_inputs()
        with patch.object(
            native_wrapper_build_executor_tool.composition_tool,
            "_namespace_id",
            side_effect=lambda name: f"{name}:[1]",
        ), patch.object(native_wrapper_build_executor_tool.os, "getpid", return_value=4242):
            argv, pass_fds = native_wrapper_build_executor_tool._build_process_arguments(  # noqa: SLF001
                inputs,
                15,
            )
        self.assertEqual(native_wrapper_build_executor_tool.CHILD_PROFILE, argv[5])
        self.assertEqual(
            [
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "/proc/self/fd/11",
                native_wrapper_build_executor_tool.CHILD_PROFILE,
                "4242",
                "15",
                "11",
                "4096",
                "2147483648",
                "0",
            ],
            argv[:12],
        )
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
                "/proc/self/fd/12",
            ],
            argv[14:28],
        )
        preserved = tuple(int(value) for value in argv[12].split(","))
        self.assertEqual((3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14), preserved)
        self.assertEqual((3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 11, 15), pass_fds)
        script = f"/proc/self/fd/{12}"
        script_index = argv.index(script)
        trailing = argv[script_index + 1 :]
        self.assertEqual(31, len(trailing))
        self.assertEqual(
            [
                "/var/tmp/zivplayer-toolchain-apt",
                "/var/tmp/zivplayer-sdk-projection",
                "/var/tmp/zivplayer-native-build-wrapper-d6cf2a36-20260905-a1",
                *(str(value) for value in preserved),
                "2096:200",
                "2096:201",
                "2096:100",
                "2096:101",
                "2096:102",
                "2096:103",
                "2096:104",
                "2096:105",
                hashlib.sha256(
                    inputs.files[
                        "helper:native/toolchain/native-wrapper-build-execution-namespace.bash"
                    ].raw
                ).hexdigest(),
                hashlib.sha256(
                    inputs.files[
                        "helper:native/toolchain/native-wrapper-build-runner.bash"
                    ].raw
                ).hexdigest(),
                hashlib.sha256(
                    inputs.files["helper:native/toolchain/install-seccomp.pl"].raw
                ).hexdigest(),
                "mnt:[1]",
                "net:[1]",
                "pid:[1]",
                "uts:[1]",
                "ipc:[1]",
                "ziv-native-wrapper-build-namespace-execution-v1",
            ],
            trailing,
        )

    def test_process_arguments_reject_duplicate_or_reserved_descriptors(self) -> None:
        for name, value, cgroup in (
            ("duplicate", 9, 15),
            ("reserved wrapper", 255, 15),
            ("reserved cgroup", 10, 255),
            ("oversized wrapper", 1_000_000_000, 15),
            ("oversized cgroup", 10, 1_000_000_000),
        ):
            inputs = argument_inputs()
            inputs.directories["wrapper"].descriptor = value
            with self.subTest(name=name), self.assertRaises(source_tool.SourceToolError):
                native_wrapper_build_executor_tool._build_process_arguments(  # noqa: SLF001
                    inputs,
                    cgroup,
                )


class WrapperReadelfAuditTest(unittest.TestCase):
    def evidence(self, raw: bytes | None = None, *, library: str = "libzivplayer_mpv.so"):
        policy = loaded_policy()
        parsed = native_wrapper_build_executor_tool._parse_wrapper_readelf_output(  # noqa: SLF001
            readelf_fixture() if raw is None else raw,
            "fixture",
        )
        return native_wrapper_build_executor_tool._jni_export_evidence(  # noqa: SLF001
            SimpleNamespace(policy=policy),
            SimpleNamespace(abi="arm64-v8a", library=library),
            parsed,
        )

    def test_exact_lifecycle_exports_are_retained_with_row_metadata(self) -> None:
        evidence = self.evidence()
        self.assertEqual(["JNI_OnLoad", "JNI_OnUnload"], [item["name"] for item in evidence])
        self.assertTrue(
            all(
                item["binding"] == "GLOBAL"
                and item["symbolType"] == "FUNC"
                and item["visibility"] == "DEFAULT"
                and item["sectionIndex"] == "7"
                for item in evidence
            )
        )

    def test_lifecycle_export_drift_is_rejected(self) -> None:
        raw = readelf_fixture()
        cases = {
            "missing": raw.replace(b"JNI_OnUnload", b"fixtureTwo_"),
            "duplicate": raw.replace(b"JNI_OnUnload", b"JNI_OnLoad"),
            "undefined": raw.replace(
                b"FUNC GLOBAL DEFAULT 7 JNI_OnLoad",
                b"FUNC GLOBAL DEFAULT UND JNI_OnLoad",
            ),
            "local": raw.replace(b"FUNC GLOBAL DEFAULT 7 JNI_OnLoad", b"FUNC LOCAL DEFAULT 7 JNI_OnLoad"),
            "weak": raw.replace(b"FUNC GLOBAL DEFAULT 7 JNI_OnLoad", b"FUNC WEAK DEFAULT 7 JNI_OnLoad"),
            "notype": raw.replace(b"FUNC GLOBAL DEFAULT 7 JNI_OnLoad", b"NOTYPE GLOBAL DEFAULT 7 JNI_OnLoad"),
            "hidden": raw.replace(b"FUNC GLOBAL DEFAULT 7 JNI_OnLoad", b"FUNC GLOBAL HIDDEN 7 JNI_OnLoad"),
            "protected": raw.replace(b"FUNC GLOBAL DEFAULT 7 JNI_OnLoad", b"FUNC GLOBAL PROTECTED 7 JNI_OnLoad"),
            "versioned": raw.replace(b"JNI_OnLoad\n", b"JNI_OnLoad@@ZIV\n"),
            "extra JNI": raw.replace(b"fixture_export", b"JNI_ExtraExport"),
            "Java prefix": raw.replace(b"fixture_export", b"Java_fixture_bad"),
        }
        for name, changed in cases.items():
            with self.subTest(name=name), self.assertRaises(source_tool.SourceToolError):
                self.evidence(changed)

    def test_nonwrapper_and_extra_dynsym_rows_are_rejected(self) -> None:
        with self.assertRaises(source_tool.SourceToolError):
            self.evidence(library="libmpv.so")
        extra = readelf_fixture().replace(
            b"Displaying notes found in: .note.android.ident\n",
            b"  6: 0000000000005000 8 FUNC GLOBAL DEFAULT 7 extra\n"
            b"Displaying notes found in: .note.android.ident\n",
        )
        with self.assertRaises(source_tool.SourceToolError):
            native_wrapper_build_executor_tool._parse_wrapper_readelf_output(  # noqa: SLF001
                extra,
                "extra row",
            )
        split_extra = readelf_fixture().replace(
            b"Displaying notes found in: .note.android.ident\n",
            b"intervening non-row\n"
            b"  6: 0000000000005000 8 FUNC GLOBAL DEFAULT 7 hidden_extra\n"
            b"Displaying notes found in: .note.android.ident\n",
        )
        with self.assertRaises(source_tool.SourceToolError):
            native_wrapper_build_executor_tool._parse_wrapper_readelf_output(  # noqa: SLF001
                split_extra,
                "split extra row",
            )

    def test_built_ident_requires_api26_and_ndk_r29(self) -> None:
        parsed = native_wrapper_build_executor_tool._parse_wrapper_readelf_output(  # noqa: SLF001
            readelf_fixture(),
            "fixture",
        ).base
        built = SimpleNamespace(abi="arm64-v8a", library="libzivplayer_mpv.so", source_kind="built")
        native_wrapper_build_executor_tool._validate_android_ident(  # noqa: SLF001
            built,
            parsed.android_ident,
        )
        for ident in (None, {"apiLevel": 25, "ndkVersion": "r29"}, {"apiLevel": 26, "ndkVersion": "r28"}):
            with self.subTest(ident=ident), self.assertRaises(source_tool.SourceToolError):
                native_wrapper_build_executor_tool._validate_android_ident(  # noqa: SLF001
                    built,
                    ident,
                )
        runtime = SimpleNamespace(
            abi="arm64-v8a",
            library="libc++_shared.so",
            source_kind="locked-ndk-runtime",
        )
        native_wrapper_build_executor_tool._validate_android_ident(  # noqa: SLF001
            runtime,
            None,
        )

    def test_artifact_audit_wires_all_20_through_jni_and_ident_checks(self) -> None:
        receipt_fixture, artifacts = receipt_inputs()
        policy = receipt_fixture.policy
        output = SimpleNamespace(path=Path("/output"), descriptor=80)
        inputs = SimpleNamespace(
            policy=policy,
            files={"audit-tool": SimpleNamespace(descriptor=81)},
            directories={"output": output},
            composition_inputs=SimpleNamespace(),
        )
        by_abi = {str(record["name"]): record for record in policy.profile.abis}
        built = set(policy.profile.build["builtLibraries"])

        def parsed_for_label(_raw: bytes, label: str):
            abi, library = label.split(" ", 1)
            wrapper = library == "libzivplayer_mpv.so"
            lifecycle = (
                tuple(
                    native_wrapper_build_executor_tool.DynamicSymbol(
                        name=name,
                        symbol_type="FUNC",
                        binding="GLOBAL",
                        visibility="DEFAULT",
                        section_index="7",
                    )
                    for name in ("JNI_OnLoad", "JNI_OnUnload")
                )
                if wrapper
                else ()
            )
            base = native_build_executor_tool.ParsedElf(
                elf_class=int(by_abi[abi]["elfClass"]),
                machine=str(by_abi[abi]["elfMachine"]),
                elf_type="ET_DYN",
                load_segments=(
                    {
                        "alignmentBytes": 16384,
                        "offsetBytes": 0,
                        "virtualAddress": 0,
                    },
                ),
                soname=library,
                needed=(),
                exports=frozenset(symbol.name for symbol in lifecycle),
                strong_undefined=frozenset(),
                weak_undefined=frozenset(),
                weak_undefined_symbol_types=(),
                weak_undefined_symbol_visibilities=(),
                symbol_relocations=(),
                android_ident=(
                    {"apiLevel": 26, "ndkBuild": "1234", "ndkVersion": "r29"}
                    if library in built
                    else {"apiLevel": 21, "ndkBuild": "old", "ndkVersion": "r28"}
                ),
                readelf_size=100,
                readelf_sha256="8" * 64,
            )
            return native_wrapper_build_executor_tool.WrapperParsedElf(
                base=base,
                dynamic_symbols=lifecycle,
            )

        def platform_for_abi(_inputs, abi_record):
            sonames = policy.data["audit"]["platformSonameAllowlist"]
            parsed = parsed_for_label(b"", f"{abi_record['name']} libc.so").base
            return (
                {str(soname): replace(parsed, soname=str(soname)) for soname in sonames},
                platform_evidence(policy)[
                    [record["name"] for record in policy.profile.abis].index(
                        abi_record["name"]
                    )
                ]["stubs"],
            )

        for artifact in artifacts:
            artifact.staged_path = output.path / artifact.abi / artifact.library
            artifact.staged_pin = None
            artifact.audit = {}
        with patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_audit_platform_stubs",
            side_effect=platform_for_abi,
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_open_pinned_artifact",
            side_effect=lambda **_kwargs: SimpleNamespace(descriptor=99),
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_run_audit_tool",
            return_value=b"readelf",
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_parse_wrapper_readelf_output",
            side_effect=parsed_for_label,
        ), patch.object(
            native_wrapper_build_executor_tool.os,
            "fstat",
            return_value=SimpleNamespace(
                st_mode=0o100644,
                st_mtime_ns=policy.data["artifacts"]["normalizedMtimeNs"],
            ),
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_jni_export_evidence",
            wraps=native_wrapper_build_executor_tool._jni_export_evidence,  # noqa: SLF001
        ) as jni_check, patch.object(
            native_wrapper_build_executor_tool,
            "_validate_android_ident",
            wraps=native_wrapper_build_executor_tool._validate_android_ident,  # noqa: SLF001
        ) as ident_check:
            evidence = native_wrapper_build_executor_tool._audit_artifacts(  # noqa: SLF001
                inputs,
                artifacts,
            )
        self.assertEqual(2, len(evidence))
        self.assertEqual(20, jni_check.call_count)
        self.assertEqual(20, ident_check.call_count)
        self.assertTrue(
            all(
                tuple(sorted(artifact.audit))
                == native_wrapper_build_executor_tool.ARTIFACT_AUDIT_FIELDS
                for artifact in artifacts
            )
        )


class WrapperBuildReceiptTest(unittest.TestCase):
    def test_receipt_has_exact_20_artifacts_contract_and_nonrelease_status(self) -> None:
        inputs, artifacts = receipt_inputs()
        execution = successful_execution(
            stdout=b"stdout",
            stderr=b"stderr",
            initial_free_bytes=1000,
            final_free_bytes=900,
            initial_free_inodes=100,
            final_free_inodes=90,
        )
        receipt = native_wrapper_build_executor_tool._build_receipt_data(  # noqa: SLF001
            inputs,
            "a" * 64,
            execution,
            artifacts,
            platform_evidence(inputs.policy),
        )
        self.assertEqual(
            native_wrapper_build_executor_tool.BUILD_RECEIPT_FIELDS,
            tuple(sorted(receipt)),
        )
        self.assertEqual(20, len(receipt["artifacts"]))
        self.assertEqual("ziv-native-wrapper-build-receipt-v1", receipt["kind"])
        self.assertIs(receipt["ready"], False)
        self.assertIs(receipt["releaseInput"], False)
        self.assertEqual(
            inputs.policy.data["receipt"]["pendingReleaseBlockers"],
            receipt["pendingReleaseBlockers"],
        )
        self.assertEqual(
            [
                {"event": "runner-launched", "returnCode": None, "sequence": 1},
                {"event": "runner-exited", "returnCode": 0, "sequence": 2},
            ],
            receipt["commandEvidence"]["parentEvents"],
        )
        wrapper_contract = receipt["wrapperContract"]
        self.assertEqual(
            native_wrapper_build_executor_tool.WRAPPER_CONTRACT_FIELDS,
            tuple(sorted(wrapper_contract)),
        )
        self.assertEqual(16, wrapper_contract["registrationMethodCount"])
        self.assertEqual(6, len(wrapper_contract["sourceInputs"]))
        expected_source_inputs = [
            {
                "destination": str(record["destination"]),
                "mode": int(record["mode"]),
                "role": str(record["role"]),
                "sha256": str(record["sha256"]),
                "sizeBytes": int(record["size"]),
                "source": str(record["source"]),
            }
            for record in inputs.policy.profile.wrapper_inputs
        ]
        self.assertEqual(expected_source_inputs, wrapper_contract["sourceInputs"])
        wrappers = [
            record
            for record in receipt["artifacts"]
            if record["library"] == "libzivplayer_mpv.so"
        ]
        self.assertEqual(2, len(wrappers))
        self.assertTrue(
            all(
                [value["name"] for value in record["audit"]["jniExports"]]
                == ["JNI_OnLoad", "JNI_OnUnload"]
                for record in wrappers
            )
        )
        native_wrapper_build_executor_tool.stack_executor._canonical_json(receipt)  # noqa: SLF001

    def test_receipt_rejects_nonwrapper_jni_evidence(self) -> None:
        inputs, artifacts = receipt_inputs()
        artifacts[0].audit["jniExports"] = [
            {
                "binding": "GLOBAL",
                "name": "JNI_OnLoad",
                "sectionIndex": "7",
                "symbolType": "FUNC",
                "visibility": "DEFAULT",
            }
        ]
        execution = successful_execution()
        with self.assertRaises(source_tool.SourceToolError):
            native_wrapper_build_executor_tool._build_receipt_data(  # noqa: SLF001
                inputs,
                "a" * 64,
                execution,
                artifacts,
                platform_evidence(inputs.policy),
            )

    def test_receipt_rejects_invalid_compiled_or_platform_evidence(self) -> None:
        execution = successful_execution()
        mutations = (
            "soname",
            "elfClass",
            "jni binding",
            "artifact sha",
            "artifact size",
            "source kind",
            "source path",
            "needed provider",
            "readelf sha",
            "readelf size",
            "resolved providers",
            "platform",
            "platform sha",
            "platform size",
        )
        for mutation in mutations:
            inputs, artifacts = receipt_inputs()
            evidence = platform_evidence(inputs.policy)
            if mutation == "soname":
                artifacts[0].audit["soname"] = "wrong.so"
            elif mutation == "elfClass":
                artifacts[0].audit["elfClass"] = 32
            elif mutation == "jni binding":
                wrapper = next(
                    artifact
                    for artifact in artifacts
                    if artifact.library == "libzivplayer_mpv.so"
                )
                wrapper.audit["jniExports"][0]["binding"] = "LOCAL"
            elif mutation == "artifact sha":
                artifacts[0].sha256 = "g" * 64
            elif mutation == "artifact size":
                artifacts[0].size = 0
            elif mutation == "source kind":
                artifacts[0].source_kind = "staged"
            elif mutation == "source path":
                artifacts[0].source_relative_path = f"other/{artifacts[0].library}"
            elif mutation == "needed provider":
                artifacts[0].audit["neededProviders"][0]["kind"] = "staged"
            elif mutation == "readelf sha":
                artifacts[0].audit["readelfSha256"] = "G" * 64
            elif mutation == "readelf size":
                artifacts[0].audit["readelfSizeBytes"] = 0
            elif mutation == "resolved providers":
                artifacts[0].audit["resolvedSymbolProviders"] = []
            elif mutation == "platform":
                evidence = []
            elif mutation == "platform sha":
                evidence[0]["stubs"][0]["sha256"] = "g" * 64
            else:
                evidence[0]["stubs"][0]["sizeBytes"] = 0
            with self.subTest(mutation=mutation), self.assertRaises(
                source_tool.SourceToolError
            ):
                native_wrapper_build_executor_tool._build_receipt_data(  # noqa: SLF001
                    inputs,
                    "a" * 64,
                    execution,
                    artifacts,
                    evidence,
                )

    def test_receipt_rejects_parent_runner_event_drift(self) -> None:
        inputs, artifacts = receipt_inputs()
        execution = successful_execution()
        mutations = (
            (),
            execution.parent_events[:1],
            (
                execution.parent_events[0],
                replace(execution.parent_events[1], return_code=1),
            ),
            tuple(reversed(execution.parent_events)),
        )
        for parent_events in mutations:
            with self.subTest(parent_events=parent_events), self.assertRaises(
                source_tool.SourceToolError
            ):
                native_wrapper_build_executor_tool._build_receipt_data(  # noqa: SLF001
                    inputs,
                    "a" * 64,
                    replace(execution, parent_events=parent_events),
                    artifacts,
                    platform_evidence(inputs.policy),
                )

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
        "requires a root Linux filesystem",
    )
    def test_linux_receipt_publication_is_no_replace_durable_and_rollback_safe(self) -> None:
        policy = loaded_policy()
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            workspace_path = Path(temporary)
            os.chmod(workspace_path, 0o700)
            for name, mode in (
                ("source", 0o755),
                ("output", 0o700),
                ("home", 0o700),
                ("tmp", 0o700),
                ("wrapper", 0o555),
            ):
                (workspace_path / name).mkdir(mode=mode)
            (workspace_path / native_build_tool.PREPARATION_RECEIPT_NAME).write_bytes(
                b"preparation"
            )
            workspace = (
                native_wrapper_build_executor_tool.native_executor_tool._open_pinned_directory(  # noqa: SLF001
                    workspace_path,
                    label="test wrapper workspace",
                    expected_mode=0o700,
                )
            )
            inputs = SimpleNamespace(
                policy=policy,
                preparation=SimpleNamespace(composition_receipt_raw=b"composition"),
                preparation_receipt_raw=b"preparation",
                build_workspace=workspace_path,
                directories={"workspace": workspace},
                files={},
            )
            try:
                native_wrapper_build_executor_tool.stack_executor._publish_attempt_marker(  # noqa: SLF001
                    inputs
                )
                receipt_path = workspace_path / policy.data["receipt"]["name"]
                failure = source_tool.SourceToolError(
                    "fixture publication failure",
                    source_tool.EXIT_INTEGRITY,
                )
                with patch.object(
                    native_wrapper_build_executor_tool.stack_executor,
                    "_assert_build_file_pin",
                    side_effect=failure,
                ), self.assertRaises(source_tool.SourceToolError):
                    native_wrapper_build_executor_tool._publish_build_receipt(  # noqa: SLF001
                        inputs,
                        {"kind": "rolled-back"},
                    )
                self.assertFalse(receipt_path.exists())
                self.assertFalse(
                    any(path.name.endswith(".part") for path in workspace_path.iterdir())
                )
                raw, digest = native_wrapper_build_executor_tool._publish_build_receipt(  # noqa: SLF001
                    inputs,
                    {"kind": "fixture"},
                )
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
                info = receipt_path.lstat()
                self.assertEqual(0o644, stat.S_IMODE(info.st_mode))
                self.assertEqual(
                    policy.data["receipt"]["normalizedMtimeNs"],
                    info.st_mtime_ns,
                )
                self.assertEqual(
                    {"kind": "fixture"},
                    json.loads(receipt_path.read_text(encoding="utf-8")),
                )
                with self.assertRaises(source_tool.SourceToolError):
                    native_wrapper_build_executor_tool._publish_build_receipt(  # noqa: SLF001
                        inputs,
                        {"kind": "replacement"},
                    )
            finally:
                for pinned in inputs.files.values():
                    if pinned.descriptor >= 0:
                        os.close(pinned.descriptor)
                        pinned.descriptor = -1
                os.close(workspace.descriptor)
                workspace.descriptor = -1


class WrapperLockedBuildLifecycleTest(unittest.TestCase):
    def run_fake_locked_build(self, return_code: int) -> dict[str, object]:
        workspace_descriptor = os.open(os.devnull, os.O_RDONLY)
        cgroup = SimpleNamespace(
            procs_fd=os.open(os.devnull, os.O_WRONLY),
            removed=False,
        )
        cgroup_procs_descriptor = cgroup.procs_fd
        reserve_pidfd = os.open(os.devnull, os.O_RDONLY)
        leader_pidfd = os.open(os.devnull, os.O_RDONLY)
        process = FakeProcess(b"runner stdout\n", b"runner stderr\n", return_code)
        inputs = SimpleNamespace(
            policy=loaded_policy(),
            directories={"workspace": SimpleNamespace(descriptor=workspace_descriptor)},
        )

        def remove_cgroup(handle: SimpleNamespace) -> None:
            handle.removed = True

        def terminate_process(
            _process: FakeProcess,
            _pidfd: int,
            handle: SimpleNamespace,
        ) -> None:
            handle.removed = True

        result = None
        error = None
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool,
                        "_assert_pinned_build_inputs",
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.stack_executor,
                        "_assert_host_resource_limits",
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.os,
                        "statvfs",
                        return_value=SimpleNamespace(
                            f_bavail=16 * 1024 * 1024 * 1024,
                            f_frsize=1,
                            f_favail=1_000_000,
                        ),
                        create=True,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.environment_tool,
                        "_prepare_installer_cgroup",
                        return_value=cgroup,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool,
                        "_build_process_arguments",
                        return_value=(["/fixture/runner"], (cgroup.procs_fd,)),
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.environment_tool,
                        "_cgroup_limit_violation",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.environment_tool,
                        "_cgroup_is_empty",
                        return_value=True,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.environment_tool,
                        "_remove_installer_cgroup",
                        side_effect=remove_cgroup,
                    )
                )
                terminate = stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool,
                        "_terminate_wrapper_build_process",
                        side_effect=terminate_process,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.native_executor_tool,
                        "_filesystem_violation",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.selectors,
                        "DefaultSelector",
                        side_effect=FakeSelector,
                    )
                )
                stack.enter_context(
                    patch.object(native_wrapper_build_executor_tool.os, "set_blocking")
                )
                popen = stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.subprocess,
                        "Popen",
                        return_value=process,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.os,
                        "pidfd_open",
                        side_effect=(reserve_pidfd, leader_pidfd),
                        create=True,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.signal,
                        "pidfd_send_signal",
                        create=True,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.signal,
                        "SIG_BLOCK",
                        0,
                        create=True,
                    )
                )
                stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.signal,
                        "SIG_SETMASK",
                        2,
                        create=True,
                    )
                )
                signal_mask = stack.enter_context(
                    patch.object(
                        native_wrapper_build_executor_tool.signal,
                        "pthread_sigmask",
                        return_value=set(),
                        create=True,
                    )
                )
                try:
                    result = native_wrapper_build_executor_tool._run_locked_build(  # noqa: SLF001
                        inputs
                    )
                except BaseException as caught:
                    error = caught
        finally:
            os.close(workspace_descriptor)
            if cgroup.procs_fd >= 0:
                os.close(cgroup.procs_fd)
            for descriptor in (reserve_pidfd, leader_pidfd):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
        return {
            "cgroup": cgroup,
            "cgroupProcsDescriptor": cgroup_procs_descriptor,
            "error": error,
            "leaderPidfd": leader_pidfd,
            "popen": popen,
            "process": process,
            "result": result,
            "signalMask": signal_mask,
            "terminate": terminate,
        }

    def test_success_records_parent_launch_and_exit_events(self) -> None:
        fixture = self.run_fake_locked_build(0)
        self.assertIsNone(fixture["error"])
        result = fixture["result"]
        assert isinstance(
            result,
            native_wrapper_build_executor_tool.WrapperBuildExecutionResult,
        )
        self.assertEqual(b"runner stdout\n", result.stdout)
        self.assertEqual(b"runner stderr\n", result.stderr)
        self.assertEqual(
            (
                native_wrapper_build_executor_tool.ParentRunnerEvent(
                    sequence=1,
                    event="runner-launched",
                    return_code=None,
                ),
                native_wrapper_build_executor_tool.ParentRunnerEvent(
                    sequence=2,
                    event="runner-exited",
                    return_code=0,
                ),
            ),
            result.parent_events,
        )
        self.assertTrue(result.cgroup_empty_after_exit)
        self.assertTrue(result.cgroup_removed)
        popen = fixture["popen"]
        assert isinstance(popen, Mock)
        popen.assert_called_once_with(
            ["/fixture/runner"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=native_wrapper_build_executor_tool.BUILD_ENVIRONMENT,
            close_fds=True,
            pass_fds=(fixture["cgroupProcsDescriptor"],),
            start_new_session=True,
        )
        terminate = fixture["terminate"]
        assert isinstance(terminate, Mock)
        terminate.assert_not_called()
        signal_mask = fixture["signalMask"]
        assert isinstance(signal_mask, Mock)
        self.assertEqual(2, signal_mask.call_count)

    def test_nonzero_exit_uses_wrapper_teardown_and_preserves_failure(self) -> None:
        fixture = self.run_fake_locked_build(7)
        self.assertIsNone(fixture["result"])
        error = fixture["error"]
        self.assertIsInstance(error, source_tool.SourceToolError)
        assert isinstance(error, source_tool.SourceToolError)
        self.assertEqual(source_tool.EXIT_INTEGRITY, error.exit_code)
        self.assertIn("exited 7", str(error))
        terminate = fixture["terminate"]
        assert isinstance(terminate, Mock)
        terminate.assert_called_once_with(
            fixture["process"],
            fixture["leaderPidfd"],
            fixture["cgroup"],
        )
        self.assertTrue(fixture["cgroup"].removed)


class WrapperExecutionOrderingTest(unittest.TestCase):
    def fake_admission(self):
        policy = loaded_policy()
        data = copy.deepcopy(policy.data)
        preparation = SimpleNamespace(
            composition_receipt_raw=b"composition",
            source_receipt_raw=b"source",
        )
        data["binding"]["preparationReceiptSha256"] = hashlib.sha256(b"preparation").hexdigest()
        data["binding"]["toolchainCompositionReceiptSha256"] = hashlib.sha256(b"composition").hexdigest()
        policy = replace(policy, data=data)
        inputs = SimpleNamespace(close=Mock())
        return policy, preparation, inputs

    def admission_patches(self, policy, preparation, inputs):
        return (
            patch.object(native_wrapper_build_executor_tool.native_build_tool, "_require_linux_root"),
            patch.object(
                native_wrapper_build_executor_tool,
                "load_build_execution_policy",
                return_value=policy,
            ),
            patch.object(
                native_wrapper_build_executor_tool.native_build_tool,
                "_resolved_build_workspace",
                return_value=(Path("/var/tmp"), Path("/var/tmp/workspace")),
            ),
            patch.object(
                native_wrapper_build_executor_tool.native_build_tool,
                "preflight",
                return_value=policy.profile,
            ),
            patch.object(
                native_wrapper_build_executor_tool.native_build_tool,
                "_snapshot_preparation_inputs",
                return_value=preparation,
            ),
            patch.object(
                native_wrapper_build_executor_tool.native_executor_tool,
                "_snapshot_probe_directories",
                return_value={},
            ),
            patch.object(
                native_wrapper_build_executor_tool.native_build_tool,
                "_verify_prepared_workspace",
                return_value=({}, b"preparation"),
            ),
            patch.object(native_wrapper_build_executor_tool.stack_executor, "_verify_io_device_scope"),
            patch.object(
                native_wrapper_build_executor_tool.stack_executor,
                "_verify_audit_tool",
                return_value=(Path("/audit"), b"audit"),
            ),
            patch.object(
                native_wrapper_build_executor_tool,
                "_pin_build_inputs",
                return_value=inputs,
            ),
        )

    def execute_with_contexts(self, patches):
        stack = contextlib.ExitStack()
        for item in patches:
            stack.enter_context(item)
        return stack

    def test_pre_marker_reverification_failure_never_consumes_or_runs(self) -> None:
        policy, preparation, inputs = self.fake_admission()
        error = source_tool.SourceToolError("tamper", source_tool.EXIT_INTEGRITY)
        publish = Mock()
        run = Mock()
        patches = self.admission_patches(policy, preparation, inputs)
        with self.execute_with_contexts(patches), patch.object(
            native_wrapper_build_executor_tool,
            "_reverify_build_inputs",
            side_effect=error,
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_publish_attempt_marker",
            publish,
        ), patch.object(native_wrapper_build_executor_tool, "_run_locked_build", run):
            with self.assertRaises(source_tool.SourceToolError):
                native_wrapper_build_executor_tool.execute(
                    *([Path("/fixture")] * 11)
                )
        publish.assert_not_called()
        run.assert_not_called()
        inputs.close.assert_called_once()

    def test_build_failure_never_publishes_receipt(self) -> None:
        policy, preparation, inputs = self.fake_admission()
        error = source_tool.SourceToolError("build failed", source_tool.EXIT_INTEGRITY)
        receipt_publisher = Mock()
        patches = self.admission_patches(policy, preparation, inputs)
        with self.execute_with_contexts(patches), patch.object(
            native_wrapper_build_executor_tool,
            "_reverify_build_inputs",
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_publish_attempt_marker",
            return_value=(b"marker", "a" * 64),
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_run_locked_build",
            side_effect=error,
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_publish_build_receipt",
            receipt_publisher,
        ):
            with self.assertRaises(source_tool.SourceToolError):
                native_wrapper_build_executor_tool.execute(
                    *([Path("/fixture")] * 11)
                )
        receipt_publisher.assert_not_called()
        inputs.close.assert_called_once()

    def test_post_build_audit_failure_closes_artifacts_without_receipt(self) -> None:
        policy, preparation, inputs = self.fake_admission()
        error = source_tool.SourceToolError("audit failed", source_tool.EXIT_INTEGRITY)
        artifact = SimpleNamespace(close=Mock())
        receipt_publisher = Mock()
        patches = self.admission_patches(policy, preparation, inputs)
        with self.execute_with_contexts(patches), patch.object(
            native_wrapper_build_executor_tool,
            "_reverify_build_inputs",
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_publish_attempt_marker",
            return_value=(b"marker", "a" * 64),
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_run_locked_build",
            return_value=successful_execution(),
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_pin_expected_artifacts",
            return_value=[artifact],
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_stage_artifacts",
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_audit_artifacts",
            side_effect=error,
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_publish_build_receipt",
            receipt_publisher,
        ):
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_wrapper_build_executor_tool.execute(
                    *([Path("/fixture")] * 11)
                )
        self.assertIs(error, raised.exception)
        artifact.close.assert_called_once()
        receipt_publisher.assert_not_called()
        inputs.close.assert_called_once()

    def test_success_path_preserves_closed_order_and_cleanup(self) -> None:
        policy, preparation, inputs = self.fake_admission()
        events: list[str] = []
        inputs.close.side_effect = lambda: events.append("inputs-close")
        artifact = SimpleNamespace(close=lambda: events.append("artifact-close"))
        execution = successful_execution(
            stdout=b"stdout",
            stderr=b"stderr",
            initial_free_bytes=10,
            final_free_bytes=9,
            initial_free_inodes=10,
            final_free_inodes=9,
        )
        receipt = {"artifacts": [{"library": "fixture"}]}
        patches = self.admission_patches(policy, preparation, inputs)

        def record(name, value=None):
            def side_effect(*_args, **_kwargs):
                events.append(name)
                return value

            return side_effect

        with self.execute_with_contexts(patches), patch.object(
            native_wrapper_build_executor_tool,
            "_reverify_build_inputs",
            side_effect=record("reverify"),
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_publish_attempt_marker",
            side_effect=record("marker", (b"marker", "a" * 64)),
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_run_locked_build",
            side_effect=record("run", execution),
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_pin_expected_artifacts",
            side_effect=record("pin-artifacts", [artifact]),
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_stage_artifacts",
            side_effect=record("stage"),
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_audit_artifacts",
            side_effect=record("audit", [{"abi": "fixture", "stubs": []}]),
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_reverify_staged_artifacts",
            side_effect=record("reverify-staged"),
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_build_receipt_data",
            side_effect=record("receipt-data", receipt),
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_publish_build_receipt",
            side_effect=record("receipt-publish", (b"receipt", "b" * 64)),
        ):
            result = native_wrapper_build_executor_tool.execute(
                *([Path("/fixture")] * 11)
            )
        self.assertIs(receipt, result)
        self.assertEqual(
            [
                "reverify",
                "marker",
                "reverify",
                "run",
                "reverify",
                "pin-artifacts",
                "stage",
                "audit",
                "reverify",
                "reverify-staged",
                "receipt-data",
                "artifact-close",
                "receipt-publish",
                "inputs-close",
            ],
            events,
        )

    def test_verify_inputs_pins_and_reverifies_without_consuming(self) -> None:
        policy, preparation, inputs = self.fake_admission()
        patches = self.admission_patches(policy, preparation, inputs)
        marker = Mock()
        run = Mock()
        stage = Mock()
        publish = Mock()
        with self.execute_with_contexts(patches), patch.object(
            native_wrapper_build_executor_tool,
            "_reverify_build_inputs",
        ) as reverify, patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_publish_attempt_marker",
            marker,
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_run_locked_build",
            run,
        ), patch.object(
            native_wrapper_build_executor_tool.stack_executor,
            "_stage_artifacts",
            stage,
        ), patch.object(
            native_wrapper_build_executor_tool,
            "_publish_build_receipt",
            publish,
        ):
            result = native_wrapper_build_executor_tool.verify_inputs(
                *([Path("/fixture")] * 11)
            )
        self.assertIs(policy, result)
        reverify.assert_called_once()
        self.assertIs(
            True,
            reverify.call_args.kwargs["require_prepared_workspace"],
        )
        marker.assert_not_called()
        run.assert_not_called()
        stage.assert_not_called()
        publish.assert_not_called()
        inputs.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
