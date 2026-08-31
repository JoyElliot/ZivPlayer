# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import os
import sys
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import native_build_tool  # noqa: E402
import source_tool  # noqa: E402


COMMITTED_PROFILE = REPOSITORY_ROOT / "native" / "native-build-profile.toml"
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"
COMMITTED_TOOLCHAIN_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"


def committed_data() -> dict[str, object]:
    with COMMITTED_PROFILE.open("rb") as stream:
        return tomllib.load(stream)


class NativeBuildProfileTest(unittest.TestCase):
    def assert_schema_error(self, data: dict[str, object], fragment: str) -> None:
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_tool.validate_profile_data(data)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)
        self.assertIn(fragment, str(raised.exception))

    def test_committed_profile_binds_manifests_and_overlay_bytes(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        self.assertEqual(native_build_tool.PROFILE_NAME, loaded.project["profile"])
        self.assertEqual(
            hashlib.sha256(COMMITTED_PROFILE.read_bytes()).hexdigest(),
            loaded.sha256,
        )
        self.assertEqual(
            ["arm64-v8a", "x86_64"],
            [str(abi["name"]) for abi in loaded.abis],
        )
        self.assertEqual(2, len(loaded.overlays))
        self.assertFalse(loaded.project["releaseReady"])
        self.assertEqual("pending-source-wrapper", loaded.build["jniWrapperStatus"])

    def test_top_level_and_nested_keys_are_exact(self) -> None:
        data = committed_data()
        data["unexpected"] = True
        self.assert_schema_error(data, "unknown unexpected")

        data = committed_data()
        project = data["project"]
        assert isinstance(project, dict)
        del project["nativeApi"]
        self.assert_schema_error(data, "missing nativeApi")

        data = committed_data()
        project = data["project"]
        assert isinstance(project, dict)
        project["sourceManifest"] = "native/copy.toml"
        self.assert_schema_error(data, "project.sourceManifest")

        data = committed_data()
        overlay = data["overlay"]
        assert isinstance(overlay, list) and isinstance(overlay[0], dict)
        overlay[0]["fuzz"] = 1
        self.assert_schema_error(data, "unknown fuzz")

    def test_release_and_network_boundaries_fail_closed(self) -> None:
        mutations = (
            (("project", "releaseReady"), True, "project.releaseReady"),
            (("project", "nativeApi"), 23, "project.nativeApi"),
            (("project", "pageSizeBytes"), 4096, "project.pageSizeBytes"),
            (("policy", "networkAtBuild"), "allowed", "policy.networkAtBuild"),
            (("policy", "allowGradle"), True, "policy.allowGradle"),
            (("policy", "allowNonfree"), True, "policy.allowNonfree"),
            (("policy", "canonicalSourceReadOnly"), False, "policy.canonicalSourceReadOnly"),
            (("policy", "inheritHostEnvironment"), True, "policy.inheritHostEnvironment"),
            (("policy", "jobs"), 8, "policy.jobs"),
            (("toolchain", "mount"), "/ambient/sdk", "toolchain.mount"),
        )
        for path, value, fragment in mutations:
            with self.subTest(path=path):
                data = committed_data()
                table = data[path[0]]
                assert isinstance(table, dict)
                table[path[1]] = value
                self.assert_schema_error(data, fragment)

    def test_commands_abis_and_output_partition_are_fixed(self) -> None:
        data = committed_data()
        build = data["build"]
        assert isinstance(build, dict)
        commands = build["commands"]
        assert isinstance(commands, list) and isinstance(commands[0], list)
        commands[0].append("--download")
        self.assert_schema_error(data, "build.commands")

        data = committed_data()
        abis = data["abi"]
        assert isinstance(abis, list)
        abis.reverse()
        self.assert_schema_error(data, "abi[0].name")

        data = committed_data()
        build = data["build"]
        assert isinstance(build, dict)
        built = build["builtLibraries"]
        assert isinstance(built, list)
        built.append("libplayer.so")
        self.assert_schema_error(data, "build.builtLibraries")

        data = committed_data()
        build = data["build"]
        assert isinstance(build, dict)
        runtime = build["runtimeLibraries"]
        assert isinstance(runtime, list)
        runtime[0] = "libstdc++.so"
        self.assert_schema_error(data, "build.runtimeLibraries")

        data = committed_data()
        abis = data["abi"]
        assert isinstance(abis, list) and isinstance(abis[0], dict)
        abis[0]["runtimeSize"] = 1
        self.assert_schema_error(data, "abi[0].runtimeSize")

    def test_overlay_paths_order_hashes_and_modes_are_strict(self) -> None:
        data = committed_data()
        overlays = data["overlay"]
        assert isinstance(overlays, list)
        overlays.reverse()
        self.assert_schema_error(data, "overlay destinations")

        data = committed_data()
        overlays = data["overlay"]
        assert isinstance(overlays, list) and isinstance(overlays[0], dict)
        overlays[0]["replacement"] = "../escape.sh"
        self.assert_schema_error(data, "normalized relative POSIX path")

        data = committed_data()
        overlays = data["overlay"]
        assert isinstance(overlays, list) and isinstance(overlays[0], dict)
        overlays[0]["replacementSha256"] = "A" * 64
        self.assert_schema_error(data, "lowercase SHA-256")

        data = committed_data()
        overlays = data["overlay"]
        assert isinstance(overlays, list) and isinstance(overlays[0], dict)
        overlays[0]["replacementMode"] = 0o777
        self.assert_schema_error(data, "overlay[0].replacementMode")

    def test_repository_paths_reject_intermediate_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "actual"
            actual.mkdir()
            (actual / "file.toml").write_text("value = 1\n", encoding="utf-8")
            linked = root / "linked"
            try:
                linked.symlink_to(actual, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks are unavailable: {error}")
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool._resolve_repository_file(  # noqa: SLF001
                    root,
                    "linked/file.toml",
                    "test file",
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("symlinks or reparse points", str(raised.exception))

            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool._resolve_repository_file(  # noqa: SLF001
                    linked,
                    "file.toml",
                    "test file",
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("must not resolve through a symlink", str(raised.exception))

    def test_load_profile_detects_manifest_and_overlay_drift(self) -> None:
        raw = COMMITTED_PROFILE.read_text(encoding="utf-8")
        declared = str(committed_data()["project"]["sourceManifestSha256"])
        with tempfile.TemporaryDirectory() as temporary:
            changed_profile = Path(temporary) / "profile.toml"
            changed_profile.write_text(
                raw.replace(declared, "0" * 64, 1),
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool.load_profile(
                    changed_profile,
                    COMMITTED_SOURCE_MANIFEST,
                    COMMITTED_TOOLCHAIN_MANIFEST,
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("source manifest digest", str(raised.exception))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "native"
            native.mkdir()
            (native / "source-manifest.toml").write_bytes(COMMITTED_SOURCE_MANIFEST.read_bytes())
            (native / "toolchain-manifest.toml").write_bytes(COMMITTED_TOOLCHAIN_MANIFEST.read_bytes())
            (native / "native-build-profile.toml").write_bytes(COMMITTED_PROFILE.read_bytes())
            for entry in committed_data()["overlay"]:
                assert isinstance(entry, dict)
                source = REPOSITORY_ROOT / str(entry["replacement"])
                destination = root / str(entry["replacement"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            first = committed_data()["overlay"][0]
            assert isinstance(first, dict)
            tampered = root / str(first["replacement"])
            tampered.write_bytes(tampered.read_bytes() + b"# drift\n")
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool.load_profile(
                    native / "native-build-profile.toml",
                    native / "source-manifest.toml",
                    native / "toolchain-manifest.toml",
                    repository_root=root,
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("overlay replacement size differs", str(raised.exception))

    def test_preflight_verifies_source_before_composition(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        calls: list[str] = []

        def verify_source(*_args: object, **kwargs: object) -> None:
            self.assertIs(kwargs["require_preserve"], True)
            calls.append("source")

        def verify_origins(*_args: object, **_kwargs: object) -> None:
            calls.append("origins")

        def verify_runtime(*_args: object, **_kwargs: object) -> None:
            calls.append("runtime")

        def verify_composition(*_args: object, **kwargs: object) -> None:
            self.assertIs(kwargs["announce"], False)
            calls.append("composition")

        with (
            patch.object(native_build_tool, "_require_linux_root"),
            patch.object(native_build_tool, "load_profile", return_value=loaded),
            patch.object(
                native_build_tool.materialize_sources,
                "verify_materialized_workspace",
                side_effect=verify_source,
            ),
            patch.object(native_build_tool.rootfs_tool, "_require_ext4"),
            patch.object(native_build_tool, "_verify_overlay_origins", side_effect=verify_origins),
            patch.object(native_build_tool, "_verify_runtime_libraries", side_effect=verify_runtime),
            patch.object(
                native_build_tool.composition_tool,
                "verify_composition",
                side_effect=verify_composition,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            native_build_tool.preflight(
                COMMITTED_PROFILE,
                COMMITTED_SOURCE_MANIFEST,
                COMMITTED_TOOLCHAIN_MANIFEST,
                Path("source-cache"),
                Path("toolchain-cache"),
                Path("source-workspace"),
                Path("apt-root"),
                Path("sdk-root"),
                Path("composition.json"),
            )
        self.assertEqual(["source", "origins", "runtime", "composition"], calls)

    def test_runtime_library_bytes_are_checked_for_each_abi(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        payloads = (b"arm64 runtime", b"x86_64 runtime")
        test_abis = tuple(
            {
                **abi,
                "runtimeSize": len(payload),
                "runtimeSha256": hashlib.sha256(payload).hexdigest(),
            }
            for abi, payload in zip(loaded.abis, payloads, strict=True)
        )
        test_profile = replace(loaded, abis=test_abis)
        with tempfile.TemporaryDirectory() as temporary:
            sdk_root = Path(temporary)
            for abi, payload in zip(test_profile.abis, payloads, strict=True):
                runtime = (
                    sdk_root
                    / "android-sdk"
                    / "ndk"
                    / str(test_profile.toolchain["ndkVersion"])
                    / "toolchains"
                    / "llvm"
                    / "prebuilt"
                    / "linux-x86_64"
                    / "sysroot"
                    / "usr"
                    / "lib"
                    / str(abi["ndkRuntimeDirectory"])
                    / "libc++_shared.so"
                )
                runtime.parent.mkdir(parents=True, exist_ok=True)
                runtime.write_bytes(payload)

            native_build_tool._verify_runtime_libraries(test_profile, sdk_root)  # noqa: SLF001

            changed = (
                sdk_root
                / "android-sdk"
                / "ndk"
                / str(test_profile.toolchain["ndkVersion"])
                / "toolchains"
                / "llvm"
                / "prebuilt"
                / "linux-x86_64"
                / "sysroot"
                / "usr"
                / "lib"
                / str(test_profile.abis[0]["ndkRuntimeDirectory"])
                / "libc++_shared.so"
            )
            changed.write_bytes(b"drift")
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool._verify_runtime_libraries(test_profile, sdk_root)  # noqa: SLF001
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("runtime library size differs", str(raised.exception))

    def test_cli_validate_and_platform_gate_have_stable_exit_codes(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = native_build_tool.main(["validate"])
        self.assertEqual(source_tool.EXIT_OK, result)
        self.assertIn("native build profile valid", output.getvalue())

        with patch.object(native_build_tool.sys, "platform", "win32"):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                result = native_build_tool.main(["preflight"])
        self.assertEqual(source_tool.EXIT_SCHEMA, result)
        self.assertIn("requires Linux root", error.getvalue())


if __name__ == "__main__":
    unittest.main()
