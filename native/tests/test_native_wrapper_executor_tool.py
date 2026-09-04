# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import native_build_tool  # noqa: E402
import native_executor_tool  # noqa: E402
import source_tool  # noqa: E402


COMMITTED_POLICY = REPOSITORY_ROOT / "native" / "native-wrapper-executor-policy.toml"
COMMITTED_PROFILE = REPOSITORY_ROOT / "native" / "native-wrapper-build-profile.toml"
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"
COMMITTED_TOOLCHAIN_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"
LOCKED_WRAPPER_POLICY_SHA256 = "e38767eb0e8e095364d13040a9ce3f49479f9147e1a2740d4f81860c496d1647"
LOCKED_WRAPPER_PROFILE_SHA256 = "d6cf2a360b4c8f159e49fc9a9872faf4a21e3dc5e8a225905d3b3ccaf4fe42ce"
LOCKED_WRAPPER_TRANSCRIPT_SHA256 = "e182d70ac1a76b00f7f3a622835c23621ba3df4654a339b945f66094f959dc9f"
HISTORICAL_HELPERS = {
    "native-executor-policy.toml": (
        REPOSITORY_ROOT / "native" / "native-executor-policy.toml",
        "24a42f725bc174d41a7434d57c7078dd1f02a2cc27963202a8638909cb7276ce",
    ),
    "native-build-namespace.bash": (
        REPOSITORY_ROOT / "native" / "toolchain" / "native-build-namespace.bash",
        "8a5177f0664b2909dd75e922843c3f430b36f495c7ea9ef38e85d54173485959",
    ),
    "native-build-probe.bash": (
        REPOSITORY_ROOT / "native" / "toolchain" / "native-build-probe.bash",
        "3efa8bffa6dbe38c7264a1ae3b1aa8ebf0f00a1e07bbebf5df0172e58a5b4485",
    ),
    "native-executor-child.py": (
        REPOSITORY_ROOT / "native" / "toolchain" / "native-executor-child.py",
        "02bdfc1357fbf533364f12829caa7f763f422c0053b8841499ae63166c272e3a",
    ),
}
WRAPPER_HELPERS = {
    "native-build-wrapper-namespace.bash": (
        REPOSITORY_ROOT / "native" / "toolchain" / "native-build-wrapper-namespace.bash",
        "da4e3795ee350a2a41b80b51069d2eb65eb6e9d7377d8445c8d9d91649df5554",
    ),
    "native-build-wrapper-probe.bash": (
        REPOSITORY_ROOT / "native" / "toolchain" / "native-build-wrapper-probe.bash",
        "e63067cd5316c17067fdc298f9f68c8121a314b92584ac19cc467b93499da9e0",
    ),
    "native-wrapper-executor-child.py": (
        REPOSITORY_ROOT / "native" / "toolchain" / "native-wrapper-executor-child.py",
        "1264e5b7d40c3451ef82f42cb1145dfdbf7da65e91be20a34d185a6a084a37bb",
    ),
    "install-seccomp.pl": (
        REPOSITORY_ROOT / "native" / "toolchain" / "install-seccomp.pl",
        "ab2e6d21a2768a585b2bc53a2495c78f46ab9a4dbac32d09bf58e311091c597d",
    ),
}
LOCKED_WRAPPER_INPUTS = (
    (
        "Android.mk",
        "native/wrapper/Android.mk",
        "build",
        1360,
        "0dbe6408ea5fa0e4ad21d2ef2efda5ce718f7bcc82a211b0e8d9721ab750f0f8",
        0o444,
    ),
    (
        "Application.mk",
        "native/wrapper/Application.mk",
        "build",
        361,
        "4c7f0bda74ef74b385c877f28508318fe6af060f4cf8a4bbeb561adc8e1800d2",
        0o444,
    ),
    (
        "CMakeLists.txt",
        "native/wrapper/CMakeLists.txt",
        "contract",
        4050,
        "1cf728263640a19959a5a5c2963317add3ece57e0eb5ce06b5e58ebfc55c1c38",
        0o444,
    ),
    (
        "MpvNativeBindings.kt",
        "platform/libmpv-android/src/main/kotlin/io/github/joyelliot/zivplayer/platform/libmpv/MpvNativeBindings.kt",
        "contract",
        8874,
        "cf46bcba19529af8d561d8f36b820937bf6c0002fd33a5b7060ec24a8de9dcbb",
        0o444,
    ),
    (
        "jni-contract.toml",
        "native/wrapper/jni-contract.toml",
        "contract",
        3970,
        "e12677fcdda11a5c64dfea5e23e8474b407b8163f23fc5ea5d19faf17def9389",
        0o444,
    ),
    (
        "zivplayer_mpv.cpp",
        "native/wrapper/zivplayer_mpv.cpp",
        "build-and-contract",
        40906,
        "50d1d90247ec659c12bac74178d3971c2941697d4b6f398fd29cc4ca57a04179",
        0o444,
    ),
)


def fixture_policy() -> native_executor_tool.LoadedExecutorPolicy:
    return native_executor_tool.load_execution_policy(
        COMMITTED_POLICY,
        COMMITTED_PROFILE,
        COMMITTED_SOURCE_MANIFEST,
        COMMITTED_TOOLCHAIN_MANIFEST,
    )


def fixture_argument_inputs() -> SimpleNamespace:
    policy = fixture_policy()
    helper_descriptors = iter((11, 12, 13, 14))
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
        "wrapper": SimpleNamespace(descriptor=10, identity=(2096, 105), label="wrapper"),
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


def fixture_stack_argument_inputs() -> SimpleNamespace:
    policy = native_executor_tool.load_execution_policy(
        REPOSITORY_ROOT / "native" / "native-executor-policy.toml",
        REPOSITORY_ROOT / "native" / "native-build-profile.toml",
        COMMITTED_SOURCE_MANIFEST,
        COMMITTED_TOOLCHAIN_MANIFEST,
    )
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


class NativeWrapperExecutorPolicyTest(unittest.TestCase):
    def test_committed_policy_binds_wrapper_profile_and_helpers(self) -> None:
        loaded = fixture_policy()
        self.assertIs(loaded.contract, native_executor_tool.WRAPPER_EXECUTOR_CONTRACT)
        self.assertEqual(
            LOCKED_WRAPPER_POLICY_SHA256,
            hashlib.sha256(COMMITTED_POLICY.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            LOCKED_WRAPPER_POLICY_SHA256,
            loaded.sha256,
        )
        self.assertEqual(
            LOCKED_WRAPPER_PROFILE_SHA256,
            hashlib.sha256(COMMITTED_PROFILE.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            LOCKED_WRAPPER_PROFILE_SHA256,
            loaded.profile.sha256,
        )
        self.assertEqual(native_build_tool.WRAPPER_PROFILE_NAME, loaded.profile.project["profile"])
        self.assertEqual(6, len(loaded.profile.wrapper_inputs))
        binding = loaded.data["binding"]
        gate = loaded.data["gate"]
        namespace = loaded.data["namespace"]
        assert isinstance(binding, dict)
        assert isinstance(gate, dict)
        assert isinstance(namespace, dict)
        self.assertEqual("/build/wrapper", binding["wrapperInputMount"])
        self.assertIs(binding["wrapperInputReadOnly"], True)
        self.assertEqual(native_build_tool.WRAPPER_PREPARATION_RECEIPT_KIND, binding["preparationReceiptKind"])
        self.assertEqual("namespace-probe", gate["phase"])
        for key in ("buildCommands", "artifactStaging", "buildReceipt", "ready", "releaseInput"):
            self.assertIs(gate[key], False)
        self.assertEqual("ext4:ro,nosuid,nodev,noexec", namespace["wrapper"])
        self.assertEqual(16, namespace["expectedMountCount"])
        actual_wrapper_inputs = tuple(
            (
                str(item["destination"]),
                str(item["source"]),
                str(item["role"]),
                int(item["size"]),
                str(item["sha256"]),
                int(item["mode"]),
            )
            for item in loaded.profile.wrapper_inputs
        )
        self.assertEqual(LOCKED_WRAPPER_INPUTS, actual_wrapper_inputs)
        for _destination, source, _role, size, digest, _mode in LOCKED_WRAPPER_INPUTS:
            raw = (REPOSITORY_ROOT / source).read_bytes()
            self.assertEqual(size, len(raw))
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_wrapper_policy_schema_is_exact_and_type_strict(self) -> None:
        cases: list[tuple[dict[str, object], str]] = []
        missing_wrapper = copy.deepcopy(native_executor_tool.WRAPPER_EXPECTED_POLICY)
        del missing_wrapper["namespace"]["wrapper"]  # type: ignore[index]
        cases.append((missing_wrapper, "missing wrapper"))
        writable_wrapper = copy.deepcopy(native_executor_tool.WRAPPER_EXPECTED_POLICY)
        writable_wrapper["binding"]["wrapperInputReadOnly"] = False  # type: ignore[index]
        cases.append((writable_wrapper, "wrapperInputReadOnly"))
        wrong_mount_count = copy.deepcopy(native_executor_tool.WRAPPER_EXPECTED_POLICY)
        wrong_mount_count["namespace"]["expectedMountCount"] = 15  # type: ignore[index]
        cases.append((wrong_mount_count, "expectedMountCount"))
        for gate_name in (
            "buildCommands",
            "artifactStaging",
            "buildReceipt",
            "ready",
            "releaseInput",
        ):
            opened_gate = copy.deepcopy(native_executor_tool.WRAPPER_EXPECTED_POLICY)
            opened_gate["gate"][gate_name] = True  # type: ignore[index]
            cases.append((opened_gate, gate_name))
        stack_kind = copy.deepcopy(native_executor_tool.WRAPPER_EXPECTED_POLICY)
        stack_kind["kind"] = native_executor_tool.POLICY_KIND
        cases.append((stack_kind, "binding keys"))
        for data, fragment in cases:
            with self.subTest(fragment=fragment), self.assertRaises(
                source_tool.SourceToolError
            ) as raised:
                native_executor_tool.validate_policy_data(data)
            self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)
            self.assertIn(fragment, str(raised.exception))

    def test_wrapper_policy_rejects_stack_profile(self) -> None:
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool.load_execution_policy(
                COMMITTED_POLICY,
                REPOSITORY_ROOT / "native" / "native-build-profile.toml",
                COMMITTED_SOURCE_MANIFEST,
                COMMITTED_TOOLCHAIN_MANIFEST,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("not bound", str(raised.exception))

        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool.load_execution_policy(
                REPOSITORY_ROOT / "native" / "native-executor-policy.toml",
                COMMITTED_PROFILE,
                COMMITTED_SOURCE_MANIFEST,
                COMMITTED_TOOLCHAIN_MANIFEST,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("not bound", str(raised.exception))

    def test_helper_hashes_and_historical_bytes_are_locked(self) -> None:
        loaded = fixture_policy()
        helpers = loaded.data["helpers"]
        assert isinstance(helpers, dict)
        expected_helper_binding = {
            "launcherPath": "native/toolchain/native-wrapper-executor-child.py",
            "launcherSha256": WRAPPER_HELPERS["native-wrapper-executor-child.py"][1],
            "namespacePath": "native/toolchain/native-build-wrapper-namespace.bash",
            "namespaceSha256": WRAPPER_HELPERS["native-build-wrapper-namespace.bash"][1],
            "probePath": "native/toolchain/native-build-wrapper-probe.bash",
            "probeSha256": WRAPPER_HELPERS["native-build-wrapper-probe.bash"][1],
            "seccompPath": "native/toolchain/install-seccomp.pl",
            "seccompSha256": WRAPPER_HELPERS["install-seccomp.pl"][1],
        }
        self.assertEqual(expected_helper_binding, helpers)
        for path_key, digest_key in native_executor_tool.HELPER_KEYS:
            relative = str(helpers[path_key])
            raw = (REPOSITORY_ROOT / relative).read_bytes()
            self.assertEqual(helpers[digest_key], hashlib.sha256(raw).hexdigest())
            self.assertEqual(raw, loaded.helper_raws[relative])
        for name, (path, digest) in HISTORICAL_HELPERS.items():
            with self.subTest(historical=name):
                self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        for name, (path, digest) in WRAPPER_HELPERS.items():
            with self.subTest(wrapper=name):
                self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_wrapper_helpers_lock_descriptor_mount_and_no_build_contract(self) -> None:
        child = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-wrapper-executor-child.py"
        ).read_bytes()
        namespace = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-build-wrapper-namespace.bash"
        ).read_bytes()
        probe = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-build-wrapper-probe.bash"
        ).read_bytes()
        self.assertIn(b'"$#" -ne 31', namespace)
        self.assertIn(b"shift 25", namespace)
        self.assertIn(b"ziv-native-build-wrapper-namespace-probe-v1", namespace)
        self.assertIn(b'"${new_root}/build/wrapper"', namespace)
        self.assertIn(b"remount,bind,ro,nosuid,nodev,noexec", namespace)
        self.assertIn(b"/build/wrapper:ext4", namespace)
        self.assertIn(b'"${#seen_mounts[@]}" -eq 16', namespace)
        self.assertIn(b'$5 != "/build/wrapper"', namespace)
        self.assertIn(b"/proc/self/mountinfo", namespace)
        self.assertIn(b'EXPECTED_PRESERVED_DESCRIPTORS = 11', child)
        self.assertIn(b'ziv-native-wrapper-executor-child-v1', child)
        self.assertIn(b"read-only-input;root-0555;files-0444;exact-six", probe)
        self.assertEqual(1, probe.count(b"/build/source/buildscripts/buildall.sh"))
        self.assertNotIn(b"--arch", namespace + probe)
        self.assertNotIn(b"ndk-build", namespace + probe)
        expected_mount_inventory = (
            b"/:overlay|/build:tmpfs|/build/source:ext4|/build/output:ext4|"
            b"/build/home:ext4|/build/tmp:ext4|/build/wrapper:ext4|"
            b"/opt/zivplayer/toolchain:ext4|/proc:proc|/proc/keys:tmpfs|"
            b"/dev:tmpfs|/dev/shm:tmpfs|/run:tmpfs|/tmp:tmpfs|"
            b"/var/tmp:tmpfs|/var/log:tmpfs"
        )
        self.assertIn(expected_mount_inventory, namespace)
        expected_mount_policies = (
            b"require_mount_policy / overlay 'ro,nosuid,nodev' 'rw,noexec'",
            b"require_mount_policy /build tmpfs 'ro,nosuid,nodev,noexec' 'rw,exec'",
            b"require_mount_policy /build/source ext4 'rw,nosuid,nodev' 'ro,noexec'",
            b"require_mount_policy /build/output ext4 'rw,nosuid,nodev,noexec' 'ro,exec'",
            b"require_mount_policy /build/home ext4 'rw,nosuid,nodev,noexec' 'ro,exec'",
            b"require_mount_policy /build/tmp ext4 'rw,nosuid,nodev' 'ro,noexec'",
            b"require_mount_policy /build/wrapper ext4 'ro,nosuid,nodev,noexec' 'rw,exec'",
            b"require_mount_policy /opt/zivplayer/toolchain ext4 'ro,nosuid,nodev' 'rw,noexec'",
            b"require_mount_policy /proc proc 'ro,nosuid,nodev,noexec' 'rw'",
            b"require_mount_policy /proc/keys tmpfs 'ro,nosuid,nodev,noexec' 'rw'",
            b"require_mount_policy /dev tmpfs 'ro,nosuid,noexec' 'rw,nodev'",
            b"require_mount_policy /dev/shm tmpfs 'rw,nosuid,nodev,noexec' 'ro'",
            b"require_mount_policy /run tmpfs 'rw,nosuid,nodev,noexec' 'ro'",
            b"require_mount_policy /tmp tmpfs 'rw,nosuid,nodev,noexec' 'ro'",
            b"require_mount_policy /var/tmp tmpfs 'rw,nosuid,nodev,noexec' 'ro'",
            b"require_mount_policy /var/log tmpfs 'rw,nosuid,nodev,noexec' 'ro'",
        )
        for expected in expected_mount_policies:
            self.assertEqual(1, namespace.count(expected))
        for forbidden in (b"clang ", b"meson ", b"readelf ", b"ninja "):
            self.assertNotIn(forbidden, namespace + probe)

        profile = tomllib.loads(COMMITTED_PROFILE.read_text(encoding="utf-8"))
        expected_inputs = {
            str(item["destination"]): (int(item["size"]), str(item["sha256"]))
            for item in profile["wrapperInput"]
        }
        probe_inputs = {
            match.group("name").decode("ascii"): (
                int(match.group("size")),
                match.group("sha").decode("ascii"),
            )
            for match in re.finditer(
                rb"^require_wrapper_file (?P<name>[^ ]+) (?P<size>[0-9]+) (?P<sha>[0-9a-f]{64})$",
                probe,
                re.MULTILINE,
            )
        }
        self.assertEqual(expected_inputs, probe_inputs)

    @unittest.skipUnless(sys.platform == "linux", "requires the locked Linux awk")
    def test_wrapper_mountinfo_awk_accepts_only_one_private_read_only_mount(self) -> None:
        namespace = (
            REPOSITORY_ROOT / "native" / "toolchain" / "native-build-wrapper-namespace.bash"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"if ! /usr/bin/awk '\n(?P<program>.*?)\n' /proc/self/mountinfo; then",
            namespace,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        program = match.group("program")
        valid = "36 25 8:1 /wrapper /build/wrapper ro,nosuid,nodev,noexec - ext4 /dev/sda rw\n"
        accepted = subprocess.run(
            ["/usr/bin/awk", program],
            input=valid,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        invalid_rows = (
            "",
            valid + valid,
            valid.replace("ro,nosuid,nodev,noexec", "rw,nosuid,nodev,noexec"),
            valid.replace("ro,nosuid,nodev,noexec", "ro,nosuid,nodev"),
            valid.replace(" - ext4", " shared:7 - ext4"),
            valid.replace(" - ext4", " - tmpfs"),
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                rejected = subprocess.run(
                    ["/usr/bin/awk", program],
                    input=row,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, rejected.returncode)

    def test_probe_transcript_is_wrapper_specific_and_exact(self) -> None:
        self.assertEqual(
            LOCKED_WRAPPER_TRANSCRIPT_SHA256,
            hashlib.sha256(
                native_executor_tool.WRAPPER_EXPECTED_PROBE_TRANSCRIPT
            ).hexdigest(),
        )
        parsed = native_executor_tool._parse_probe_transcript(  # noqa: SLF001
            native_executor_tool.WRAPPER_EXPECTED_PROBE_TRANSCRIPT,
            native_executor_tool.WRAPPER_EXECUTOR_CONTRACT,
        )
        self.assertEqual(dict(native_executor_tool.WRAPPER_PROBE_RECORDS), parsed)
        self.assertIn("wrapper-ro", parsed["mounts"])
        self.assertIn("exact-six", parsed["wrapper"])
        for changed in (
            native_executor_tool.EXPECTED_PROBE_TRANSCRIPT,
            native_executor_tool.WRAPPER_EXPECTED_PROBE_TRANSCRIPT[:-1],
            native_executor_tool.WRAPPER_EXPECTED_PROBE_TRANSCRIPT.replace(
                b"wrapper-ro", b"wrapper-rw", 1
            ),
            native_executor_tool.WRAPPER_EXPECTED_PROBE_TRANSCRIPT + b"build\texecuted\n",
        ):
            with self.subTest(changed=changed[-32:]), self.assertRaises(
                source_tool.SourceToolError
            ) as raised:
                native_executor_tool._parse_probe_transcript(  # noqa: SLF001
                    changed,
                    native_executor_tool.WRAPPER_EXECUTOR_CONTRACT,
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_wrapper_probe_arguments_are_31_11_and_13(self) -> None:
        inputs = fixture_argument_inputs()
        with patch(
            "native_executor_tool.composition_tool._namespace_id",
            side_effect=lambda name: f"{name}:[1]",
        ):
            argv, pass_fds = native_executor_tool._probe_process_arguments(inputs, 15)  # noqa: SLF001
        self.assertEqual(native_executor_tool.WRAPPER_CHILD_PROFILE, argv[5])
        self.assertEqual("--", argv[13])
        self.assertEqual(13, len(pass_fds))
        self.assertEqual(len(pass_fds), len(set(pass_fds)))
        preserved = tuple(int(value) for value in argv[12].split(","))
        self.assertEqual(11, len(preserved))
        self.assertEqual((3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14), preserved)
        namespace_arguments = argv[28:]
        self.assertEqual(31, len(namespace_arguments))
        expected_namespace_arguments = [
            "/var/tmp/zivplayer-toolchain-apt",
            "/var/tmp/zivplayer-sdk-projection",
            "/var/tmp/zivplayer-native-build-wrapper-d6cf2a36-20260905-a1",
            *(str(descriptor) for descriptor in preserved),
            "2096:200",
            "2096:201",
            "2096:100",
            "2096:101",
            "2096:102",
            "2096:103",
            "2096:104",
            "2096:105",
            hashlib.sha256(inputs.files["helper:native/toolchain/native-build-wrapper-namespace.bash"].raw).hexdigest(),
            hashlib.sha256(inputs.files["helper:native/toolchain/native-build-wrapper-probe.bash"].raw).hexdigest(),
            hashlib.sha256(inputs.files["helper:native/toolchain/install-seccomp.pl"].raw).hexdigest(),
            "mnt:[1]",
            "net:[1]",
            "pid:[1]",
            "uts:[1]",
            "ipc:[1]",
            native_executor_tool.WRAPPER_NAMESPACE_PROFILE,
        ]
        self.assertEqual(expected_namespace_arguments, namespace_arguments)
        self.assertEqual(
            (3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 11, 15),
            pass_fds,
        )
        self.assertEqual(
            native_executor_tool.WRAPPER_NAMESPACE_PROFILE,
            namespace_arguments[-1],
        )
        self.assertEqual("2096:105", namespace_arguments[21])
        self.assertNotIn("buildall.sh", " ".join(argv))

    def test_stack_probe_argument_contract_remains_29_10_and_12(self) -> None:
        inputs = fixture_stack_argument_inputs()
        with patch(
            "native_executor_tool.composition_tool._namespace_id",
            side_effect=lambda name: f"{name}:[1]",
        ):
            argv, pass_fds = native_executor_tool._probe_process_arguments(inputs, 14)  # noqa: SLF001
        self.assertEqual(native_executor_tool.CHILD_PROFILE, argv[5])
        preserved = tuple(int(value) for value in argv[12].split(","))
        self.assertEqual((3, 4, 5, 6, 7, 8, 9, 11, 12, 13), preserved)
        self.assertEqual((3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 10, 14), pass_fds)
        namespace_arguments = argv[28:]
        self.assertEqual(29, len(namespace_arguments))
        self.assertEqual(
            [
                "/var/tmp/zivplayer-toolchain-apt",
                "/var/tmp/zivplayer-sdk-projection",
                "/var/tmp/zivplayer-native-build",
                *(str(descriptor) for descriptor in preserved),
                "2096:200",
                "2096:201",
                "2096:100",
                "2096:101",
                "2096:102",
                "2096:103",
                "2096:104",
                hashlib.sha256(inputs.files["helper:native/toolchain/native-build-namespace.bash"].raw).hexdigest(),
                hashlib.sha256(inputs.files["helper:native/toolchain/native-build-probe.bash"].raw).hexdigest(),
                hashlib.sha256(inputs.files["helper:native/toolchain/install-seccomp.pl"].raw).hexdigest(),
                "mnt:[1]",
                "net:[1]",
                "pid:[1]",
                "uts:[1]",
                "ipc:[1]",
                native_executor_tool.NAMESPACE_PROFILE,
            ],
            namespace_arguments,
        )

    def test_wrapper_probe_argument_descriptors_reject_duplicate_and_reserved(self) -> None:
        duplicate = fixture_argument_inputs()
        duplicate.directories["wrapper"].descriptor = 9
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._probe_process_arguments(duplicate, 15)  # noqa: SLF001
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        reserved = fixture_argument_inputs()
        reserved.directories["wrapper"].descriptor = 255
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._probe_process_arguments(reserved, 15)  # noqa: SLF001
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        oversized = fixture_argument_inputs()
        oversized.directories["wrapper"].descriptor = 10_000_000_000
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._probe_process_arguments(oversized, 15)  # noqa: SLF001
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        cgroup_reserved = fixture_argument_inputs()
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._probe_process_arguments(cgroup_reserved, 255)  # noqa: SLF001
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        cgroup_oversized = fixture_argument_inputs()
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_executor_tool._probe_process_arguments(  # noqa: SLF001
                cgroup_oversized,
                10_000_000_000,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_directory_snapshot_includes_wrapper_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "canonical"
            workspace = root / "workspace"
            source.mkdir()
            workspace.mkdir()
            for name in ("source", "output", "home", "tmp", "wrapper"):
                (workspace / name).mkdir()
            stack = native_executor_tool._snapshot_probe_directories(source, workspace)  # noqa: SLF001
            wrapper = native_executor_tool._snapshot_probe_directories(  # noqa: SLF001
                source,
                workspace,
                include_wrapper=True,
            )
        self.assertNotIn("wrapper", stack)
        self.assertEqual({*stack, "wrapper"}, set(wrapper))

    def test_cli_validates_wrapper_policy_without_spawning_process(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        forbidden_process = AssertionError("static validation launched a process")
        arguments = [
            "--policy",
            str(COMMITTED_POLICY),
            "--profile",
            str(COMMITTED_PROFILE),
            "validate",
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(
            subprocess,
            "Popen",
            side_effect=forbidden_process,
        ), patch.object(subprocess, "run", side_effect=forbidden_process), patch.object(
            os,
            "system",
            side_effect=forbidden_process,
        ):
            result = native_executor_tool.main(arguments)
        self.assertEqual(source_tool.EXIT_OK, result)
        self.assertEqual("", stderr.getvalue())
        self.assertIn("buildCommands=false", stdout.getvalue())
        self.assertIn(native_executor_tool.WRAPPER_EXPECTED_POLICY_SHA256, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
