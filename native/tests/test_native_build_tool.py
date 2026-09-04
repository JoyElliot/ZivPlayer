# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
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
COMMITTED_WRAPPER_PROFILE = (
    REPOSITORY_ROOT / "native" / "native-wrapper-build-profile.toml"
)
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"
COMMITTED_TOOLCHAIN_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"
LINUX_ROOT = sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0


def committed_data() -> dict[str, object]:
    with COMMITTED_PROFILE.open("rb") as stream:
        return tomllib.load(stream)


def committed_wrapper_data() -> dict[str, object]:
    with COMMITTED_WRAPPER_PROFILE.open("rb") as stream:
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

    def test_wrapper_profile_binds_read_only_contract_inputs_and_ten_libraries(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_WRAPPER_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        self.assertEqual(native_build_tool.WRAPPER_PROFILE_NAME, loaded.project["profile"])
        self.assertEqual(2, loaded.data["schemaVersion"])
        self.assertEqual(
            hashlib.sha256(COMMITTED_WRAPPER_PROFILE.read_bytes()).hexdigest(),
            loaded.sha256,
        )
        self.assertEqual(native_build_tool.WRAPPER_EXPECTED_LIBRARIES, tuple(loaded.build["expectedLibraries"]))
        self.assertEqual(
            native_build_tool.WRAPPER_EXPECTED_BUILT_LIBRARIES,
            tuple(loaded.build["builtLibraries"]),
        )
        self.assertEqual("pending-wrapper-inclusive-audit", loaded.build["jniWrapperStatus"])
        self.assertEqual("/build/wrapper", loaded.policy["wrapperInputMount"])
        self.assertIs(loaded.policy["wrapperInputReadOnly"], True)
        self.assertEqual(
            native_build_tool.WRAPPER_INPUT_DESTINATIONS,
            tuple(str(item["destination"]) for item in loaded.wrapper_inputs),
        )
        self.assertEqual(
            (
                "build",
                "build",
                "contract",
                "contract",
                "contract",
                "build-and-contract",
            ),
            tuple(str(item["role"]) for item in loaded.wrapper_inputs),
        )
        self.assertTrue(all(item["mode"] == 0o444 for item in loaded.wrapper_inputs))

    def test_wrapper_profile_reports_contract_parser_limits_as_integrity_failure(self) -> None:
        with patch.object(
            native_build_tool.wrapper_contract_core,
            "validate_sources",
            side_effect=ValueError("integer literal exceeds the parser limit"),
        ):
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool.load_profile(
                    COMMITTED_WRAPPER_PROFILE,
                    COMMITTED_SOURCE_MANIFEST,
                    COMMITTED_TOOLCHAIN_MANIFEST,
                )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("wrapper source contract differs", str(raised.exception))

    def test_wrapper_profile_schema_lists_roles_and_mounts_fail_closed(self) -> None:
        mutations = (
            (("schemaVersion",), 1, "schemaVersion"),
            (("policy", "wrapperInputMount"), "/build/source/wrapper", "wrapperInputMount"),
            (("policy", "wrapperInputReadOnly"), False, "wrapperInputReadOnly"),
            (("build", "target"), "mpv", "build.target"),
            (("build", "jniWrapperStatus"), "audited", "jniWrapperStatus"),
            (("wrapperInput", 0, "mode"), 0o644, "wrapperInput[0].mode"),
            (("wrapperInput", 0, "role"), "contract", "wrapperInput roles"),
        )
        for path, value, fragment in mutations:
            with self.subTest(path=path):
                data = committed_wrapper_data()
                target: object = data
                for component in path[:-1]:
                    if isinstance(component, int):
                        assert isinstance(target, list)
                        target = target[component]
                    else:
                        assert isinstance(target, dict)
                        target = target[component]
                assert isinstance(target, dict)
                final = path[-1]
                assert isinstance(final, str)
                target[final] = value
                self.assert_schema_error(data, fragment)

        data = committed_wrapper_data()
        build = data["build"]
        assert isinstance(build, dict)
        libraries = build["expectedLibraries"]
        assert isinstance(libraries, list)
        libraries.remove("libzivplayer_mpv.so")
        self.assert_schema_error(data, "build.expectedLibraries")

        data = committed_wrapper_data()
        wrapper_inputs = data["wrapperInput"]
        assert isinstance(wrapper_inputs, list)
        wrapper_inputs.reverse()
        self.assert_schema_error(data, "wrapperInput destinations")

    def test_wrapper_build_overlay_uses_only_locked_ndk_build_inputs(self) -> None:
        buildall = (
            REPOSITORY_ROOT
            / "native"
            / "overlays"
            / "mpv-android-api26-wrapper"
            / "buildscripts"
            / "buildall.sh"
        ).read_bytes()
        self.assertIn(b'local wrapper_input_root=/build/wrapper', buildall)
        self.assertIn(b'"$ANDROID_NDK_ROOT/ndk-build"', buildall)
        self.assertIn(b'NDK_PROJECT_PATH=null', buildall)
        self.assertIn(b'APP_PLATFORM=android-26', buildall)
        self.assertIn(b'APP_STL=c++_shared', buildall)
        self.assertIn(b'APP_OPTIM=release', buildall)
        self.assertIn(b'APP_SUPPORT_FLEXIBLE_PAGE_SIZES=true', buildall)
        self.assertIn(b'unset APP_ALLOW_MISSING_DEPS APP_WEAK_API_DEFS', buildall)
        self.assertIn(b'NDK_LIBS_OUT="$wrapper_build_root/libs"', buildall)
        self.assertIn(b'libzivplayer_mpv.so', buildall)
        self.assertNotIn(b"cmake ", buildall)
        self.assertNotIn(b"gradlew", buildall)
        self.assertNotIn(b"libplayer.so", buildall)

        profile = committed_wrapper_data()
        wrapper_inputs = profile["wrapperInput"]
        assert isinstance(wrapper_inputs, list)
        for wrapper_input in wrapper_inputs:
            assert isinstance(wrapper_input, dict)
            if wrapper_input["role"] not in {"build", "build-and-contract"}:
                continue
            expected = (
                "verify_wrapper_input "
                f"/build/wrapper/{wrapper_input['destination']} "
                f"{wrapper_input['size']} {wrapper_input['sha256']}"
            ).encode("ascii")
            self.assertIn(expected, buildall)

    def test_buildall_overlay_installs_only_the_locked_fail_closed_git_stub(self) -> None:
        buildall = (
            REPOSITORY_ROOT
            / "native"
            / "overlays"
            / "mpv-android-api26"
            / "buildscripts"
            / "buildall.sh"
        ).read_bytes()
        path_helper = (
            REPOSITORY_ROOT
            / "native"
            / "overlays"
            / "mpv-android-api26"
            / "buildscripts"
            / "include"
            / "path.sh"
        ).read_bytes()
        stub = b"#!/bin/sh\nexit 127\n"
        stub_sha256 = hashlib.sha256(stub).hexdigest().encode("ascii")

        self.assertIn(b"printf '#!/bin/sh\\nexit 127\\n'", buildall)
        self.assertEqual(1, buildall.count(stub_sha256))
        self.assertIn(b'500:0:0:1:19', buildall)
        self.assertIn(b'"$(command -v git)" != "$git_stub"', buildall)
        self.assertIn(b"loadarch \"$arch\"\ninstall_git_stub\nsetup_prefix", buildall)
        self.assertIn(b"$source_tool_bin:/usr/sbin:/usr/bin:/sbin:/bin", path_helper)

    def test_path_overlay_pins_recursive_install_to_locked_absolute_binary(self) -> None:
        path_helper = (
            REPOSITORY_ROOT
            / "native"
            / "overlays"
            / "mpv-android-api26"
            / "buildscripts"
            / "include"
            / "path.sh"
        ).read_bytes()

        self.assertIn(b"export INSTALL=/usr/bin/install\n", path_helper)
        self.assertNotIn(b"export INSTALL=install\n", path_helper)

    @unittest.skipUnless(sys.platform == "linux", "requires Meson on Linux")
    def test_fail_closed_git_stub_allows_an_optional_meson_probe(self) -> None:
        meson = shutil.which("meson")
        environment = dict(os.environ)
        if meson is not None:
            meson_command = [meson]
        else:
            site_packages = (
                native_build_tool.DEFAULT_SDK_ROOT / "python" / "site-packages"
            )
            try:
                locked_meson_available = site_packages.is_dir()
            except OSError as error:
                self.skipTest(f"the locked Meson package cannot be inspected: {error}")
            if not locked_meson_available:
                self.skipTest("the locked Meson package is unavailable")
            environment["PYTHONPATH"] = str(site_packages)
            meson_command = [
                sys.executable,
                "-B",
                "-c",
                (
                    "from mesonbuild.mesonmain import main; import sys; "
                    "sys.argv[0] = 'meson'; raise SystemExit(main())"
                ),
            ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            build = root / "build"
            tools = root / "tools"
            source.mkdir()
            tools.mkdir()
            (source / "meson.build").write_text(
                "project('ziv-git-probe')\n"
                "r = run_command('git', 'describe', check: false)\n"
                "assert(r.returncode() == 127, 'git stub did not fail closed')\n",
                encoding="utf-8",
            )
            git_stub = tools / "git"
            git_stub.write_bytes(b"#!/bin/sh\nexit 127\n")
            git_stub.chmod(0o500)
            locked_bin = native_build_tool.DEFAULT_APT_ROOT / "usr" / "bin"
            environment["PATH"] = f"{tools}:{locked_bin}:/usr/bin:/bin"
            completed = subprocess.run(
                [*meson_command, "setup", str(build), str(source)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            self.assertEqual(
                0,
                completed.returncode,
                (completed.stdout + completed.stderr).decode("utf-8", "replace"),
            )

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

        for command in ("prepare", "verify-preparation"):
            with self.subTest(command=command), patch.object(
                native_build_tool.sys,
                "platform",
                "win32",
            ):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    result = native_build_tool.main([command])
            self.assertEqual(source_tool.EXIT_SCHEMA, result)
            self.assertIn("requires Linux root", error.getvalue())

    def test_preparation_receipt_is_deterministic_and_explicitly_not_a_build(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        tree = {
            "format": native_build_tool.materialize_sources.TREE_DIGEST_FORMAT,
            "sha256": "1" * 64,
            "entryCount": 1,
            "fileCount": 1,
            "directoryCount": 0,
            "symlinkCount": 0,
        }
        snapshot = native_build_tool.TreePolicySnapshot(
            tree=tree,
            file_bytes=3,
            symlinks=(),
            identities={".": (1, 1), "file": (1, 2)},
        )
        source_receipt = {"linkMode": "preserve", "tree": tree}
        composition_receipt = {
            "composition": {"sha256": "2" * 64},
            "inputs": {"apt": {"tree": "apt"}, "sdk": {"tree": "sdk"}},
        }
        inputs = native_build_tool.PreparationInputs(
            profile=loaded,
            source_receipt=source_receipt,
            source_receipt_raw=native_build_tool._canonical_json(source_receipt),  # noqa: SLF001
            composition_receipt=composition_receipt,
            composition_receipt_raw=native_build_tool._canonical_json(  # noqa: SLF001
                composition_receipt
            ),
            overlay_raws=tuple(
                (REPOSITORY_ROOT / str(overlay["replacement"])).read_bytes()
                for overlay in loaded.overlays
            ),
            canonical_source=snapshot,
        )
        first = native_build_tool._preparation_receipt_data(inputs, snapshot)  # noqa: SLF001
        second = native_build_tool._preparation_receipt_data(inputs, snapshot)  # noqa: SLF001
        self.assertEqual(first, second)
        self.assertEqual(
            native_build_tool._canonical_json(first),  # noqa: SLF001
            native_build_tool._canonical_json(second),  # noqa: SLF001
        )
        self.assertEqual(native_build_tool.PREPARATION_RECEIPT_KIND, first["kind"])
        self.assertEqual("prepared", first["phase"])
        self.assertIs(first["buildExecuted"], False)
        self.assertIs(first["ready"], False)
        self.assertIs(first["releaseInput"], False)
        self.assertEqual(
            {
                "source": "/build/source",
                "output": "/build/output",
                "home": "/build/home",
                "temporary": "/build/tmp",
            },
            first["mounts"],
        )
        self.assertEqual(
            ["source", "output", "home", "tmp"],
            first["policy"]["freshPaths"],
        )
        raw = native_build_tool._canonical_json(first)  # noqa: SLF001
        self.assertNotIn(b"/var/tmp", raw)
        self.assertNotIn(str(REPOSITORY_ROOT).encode("utf-8"), raw)

    def test_wrapper_preparation_receipt_binds_the_separate_read_only_tree(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_WRAPPER_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        source_tree = {
            "format": native_build_tool.materialize_sources.TREE_DIGEST_FORMAT,
            "sha256": "1" * 64,
            "entryCount": 1,
            "fileCount": 1,
            "directoryCount": 0,
            "symlinkCount": 0,
        }
        wrapper_tree = {
            "format": native_build_tool.materialize_sources.TREE_DIGEST_FORMAT,
            "sha256": "2" * 64,
            "entryCount": len(loaded.wrapper_inputs),
            "fileCount": len(loaded.wrapper_inputs),
            "directoryCount": 0,
            "symlinkCount": 0,
        }
        source_snapshot = native_build_tool.TreePolicySnapshot(
            tree=source_tree,
            file_bytes=3,
            symlinks=(),
            identities={".": (1, 1), "file": (1, 2)},
        )
        wrapper_snapshot = native_build_tool.TreePolicySnapshot(
            tree=wrapper_tree,
            file_bytes=sum(int(item["size"]) for item in loaded.wrapper_inputs),
            symlinks=(),
            identities={
                ".": (1, 3),
                **{
                    str(item["destination"]): (1, index + 4)
                    for index, item in enumerate(loaded.wrapper_inputs)
                },
            },
        )
        source_receipt = {"linkMode": "preserve", "tree": source_tree}
        composition_receipt = {
            "composition": {"sha256": "3" * 64},
            "inputs": {"apt": {"tree": "apt"}, "sdk": {"tree": "sdk"}},
        }
        inputs = native_build_tool.PreparationInputs(
            profile=loaded,
            source_receipt=source_receipt,
            source_receipt_raw=native_build_tool._canonical_json(source_receipt),  # noqa: SLF001
            composition_receipt=composition_receipt,
            composition_receipt_raw=native_build_tool._canonical_json(  # noqa: SLF001
                composition_receipt
            ),
            overlay_raws=tuple(
                (REPOSITORY_ROOT / str(overlay["replacement"])).read_bytes()
                for overlay in loaded.overlays
            ),
            canonical_source=source_snapshot,
            wrapper_input_raws=tuple(
                (REPOSITORY_ROOT / str(item["source"])).read_bytes()
                for item in loaded.wrapper_inputs
            ),
        )
        receipt = native_build_tool._preparation_receipt_data(  # noqa: SLF001
            inputs,
            source_snapshot,
            wrapper_snapshot,
        )
        self.assertEqual(2, receipt["schemaVersion"])
        self.assertEqual(
            native_build_tool.WRAPPER_PREPARATION_RECEIPT_KIND,
            receipt["kind"],
        )
        self.assertEqual("prepared", receipt["phase"])
        self.assertIs(receipt["buildExecuted"], False)
        self.assertIs(receipt["ready"], False)
        self.assertIs(receipt["releaseInput"], False)
        self.assertEqual("/build/wrapper", receipt["mounts"]["wrapperInput"])
        self.assertEqual(
            "fresh-independent-read-only-copy",
            receipt["policy"]["wrapperInput"],
        )
        self.assertEqual(wrapper_tree, receipt["wrapperInput"]["tree"])
        self.assertIs(receipt["wrapperInput"]["readOnly"], True)
        self.assertEqual(loaded.build["commands"], receipt["commands"])
        self.assertEqual(
            [
                {
                    "destination": item["destination"],
                    "source": item["source"],
                    "role": item["role"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                    "mode": item["mode"],
                }
                for item in loaded.wrapper_inputs
            ],
            receipt["wrapperInput"]["files"],
        )
        self.assertNotIn(b"/mnt/", native_build_tool._canonical_json(receipt))  # noqa: SLF001

    def test_nested_mounts_are_rejected_and_never_recursively_cleaned(self) -> None:
        mount_record = b"/var/tmp/staging/source\text4\trw,bind"
        with patch.object(
            native_build_tool,
            "_mounts_below",
            return_value=mount_record,
        ), self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_tool._require_no_nested_mounts(  # noqa: SLF001
                Path("/var/tmp/staging"),
                "test workspace",
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("nested mount", str(raised.exception))

        with patch.object(
            native_build_tool,
            "_mounts_below",
            return_value=mount_record,
        ), patch.object(
            native_build_tool.materialize_sources,
            "_remove_tree",
        ) as remove_tree:
            cleanup_error = native_build_tool._remove_preparation_staging(  # noqa: SLF001
                Path("/var/tmp/staging"),
                (1, 2),
            )
        remove_tree.assert_not_called()
        self.assertIsNotNone(cleanup_error)
        self.assertIn("refusing recursive cleanup", cleanup_error)

    def test_mount_inventory_rejects_malformed_records(self) -> None:
        inventory_root = (
            Path("//var/tmp/root") if os.name == "nt" else Path("/var/tmp/root")
        )
        child_target = inventory_root.joinpath("child").as_posix()
        valid_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "filesystems": [
                            {"target": "/", "fstype": "ext4", "options": "rw"},
                            {
                                "target": child_target,
                                "fstype": "tmpfs",
                                "options": "rw,nosuid",
                            },
                        ]
                    }
                ).encode("utf-8"),
                "stderr": b"",
            },
        )()
        with patch.object(native_build_tool.subprocess, "run", return_value=valid_result):
            mounts = native_build_tool._mounts_below(inventory_root)  # noqa: SLF001
        self.assertEqual(f"{child_target}\ttmpfs\trw,nosuid".encode("utf-8"), mounts)

        malformed_items = (
            {"fstype": "ext4", "options": "rw"},
            {"target": 1, "fstype": "ext4", "options": "rw"},
            {"target": "/", "options": "rw"},
            {"target": "/", "fstype": "ext4", "options": None},
            {"target": "relative", "fstype": "ext4", "options": "rw"},
            {"target": "/bad\nmount", "fstype": "ext4", "options": "rw"},
            {"target": "/", "fstype": "ext4", "options": "rw", "extra": True},
            {"target": "/", "fstype": "ext4", "options": "rw", "children": None},
        )
        for item in malformed_items:
            with self.subTest(item=item):
                result = type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"filesystems": [item]}).encode("utf-8"),
                        "stderr": b"",
                    },
                )()
                with patch.object(native_build_tool.subprocess, "run", return_value=result):
                    with self.assertRaises(source_tool.SourceToolError) as raised:
                        native_build_tool._mounts_below(inventory_root)  # noqa: SLF001
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

        duplicate_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": (
                    b'{"filesystems":[{"target":"/","target":"/var",'
                    b'"fstype":"ext4","options":"rw"}]}'
                ),
                "stderr": b"",
            },
        )()
        with patch.object(native_build_tool.subprocess, "run", return_value=duplicate_result):
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool._mounts_below(inventory_root)  # noqa: SLF001
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

        recursion_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": b"{}",
                "stderr": b"",
            },
        )()
        with patch.object(
            native_build_tool.subprocess,
            "run",
            return_value=recursion_result,
        ), patch.object(
            native_build_tool.json,
            "loads",
            side_effect=RecursionError("maximum recursion depth exceeded"),
        ):
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool._mounts_below(inventory_root)  # noqa: SLF001
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_independent_copy_rejects_cross_path_identity_aliases(self) -> None:
        canonical = native_build_tool.TreePolicySnapshot(
            tree={},
            file_bytes=2,
            symlinks=(),
            identities={"a": (1, 10), "b": (1, 20)},
        )
        prepared = native_build_tool.TreePolicySnapshot(
            tree={},
            file_bytes=2,
            symlinks=(),
            identities={"a": (1, 20), "b": (1, 10)},
        )
        with self.assertRaises(source_tool.SourceToolError) as raised:
            native_build_tool._assert_independent_copy(canonical, prepared)  # noqa: SLF001
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
        self.assertIn("across paths", str(raised.exception))

    @unittest.skipUnless(LINUX_ROOT, "requires Linux root")
    def test_wrapper_input_tree_is_read_only_exact_and_tamper_evident(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_WRAPPER_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        wrapper_raws = tuple(
            (REPOSITORY_ROOT / str(item["source"])).read_bytes()
            for item in loaded.wrapper_inputs
        )
        with tempfile.TemporaryDirectory() as temporary:
            wrapper_root = Path(temporary) / "wrapper"
            snapshot = native_build_tool._create_wrapper_input_tree(  # noqa: SLF001
                wrapper_root,
                loaded,
                wrapper_raws,
            )
            self.assertIsNotNone(snapshot)
            self.assertEqual(0o555, stat.S_IMODE(wrapper_root.stat().st_mode))
            self.assertEqual(
                {".", *native_build_tool.WRAPPER_INPUT_DESTINATIONS},
                set(snapshot.identities),
            )
            for destination in native_build_tool.WRAPPER_INPUT_DESTINATIONS:
                self.assertEqual(
                    0o444,
                    stat.S_IMODE((wrapper_root / destination).stat().st_mode),
                )

            changed = wrapper_root / "Android.mk"
            changed.chmod(0o644)
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool._verify_wrapper_input_tree(  # noqa: SLF001
                    wrapper_root,
                    loaded,
                    wrapper_raws,
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

        def change_bytes(root: Path) -> None:
            (root / "Android.mk").write_bytes(b"changed wrapper bytes\n")

        def remove_file(root: Path) -> None:
            (root / "Application.mk").unlink()

        def add_file(root: Path) -> None:
            (root / "unexpected").write_bytes(b"unexpected\n")

        def replace_with_symlink(root: Path) -> None:
            (root / "CMakeLists.txt").unlink()
            (root / "CMakeLists.txt").symlink_to("Android.mk")

        def add_external_hardlink(root: Path) -> None:
            os.link(root / "Android.mk", root.parent / "wrapper-hardlink")

        def change_mtime(root: Path) -> None:
            changed = root / "MpvNativeBindings.kt"
            os.utime(
                changed,
                ns=(native_build_tool.NORMALIZED_MTIME_NS + 1,) * 2,
            )

        def change_owner(root: Path) -> None:
            os.chown(root / "jni-contract.toml", 1, 0)

        def add_nested_entry(root: Path) -> None:
            root.chmod(0o755)
            (root / "nested").mkdir()
            root.chmod(0o555)

        mutations = (
            ("bytes", change_bytes),
            ("missing", remove_file),
            ("extra", add_file),
            ("symlink", replace_with_symlink),
            ("hardlink", add_external_hardlink),
            ("mtime", change_mtime),
            ("owner", change_owner),
            ("nested", add_nested_entry),
            ("root-mode", lambda root: root.chmod(0o755)),
        )
        for label, mutate in mutations:
            with self.subTest(tamper=label), tempfile.TemporaryDirectory() as temporary:
                wrapper_root = Path(temporary) / "wrapper"
                native_build_tool._create_wrapper_input_tree(  # noqa: SLF001
                    wrapper_root,
                    loaded,
                    wrapper_raws,
                )
                mutate(wrapper_root)
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    native_build_tool._verify_wrapper_input_tree(  # noqa: SLF001
                        wrapper_root,
                        loaded,
                        wrapper_raws,
                    )
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

        with tempfile.TemporaryDirectory() as temporary:
            wrapper_root = Path(temporary) / "wrapper"
            native_build_tool._create_wrapper_input_tree(  # noqa: SLF001
                wrapper_root,
                loaded,
                wrapper_raws,
            )
            changed = wrapper_root / "zivplayer_mpv.cpp"
            try:
                os.setxattr(changed, "user.zivplayer-test", b"tamper")
            except (AttributeError, OSError):
                pass
            else:
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    native_build_tool._verify_wrapper_input_tree(  # noqa: SLF001
                        wrapper_root,
                        loaded,
                        wrapper_raws,
                    )
                self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    @unittest.skipUnless(LINUX_ROOT, "requires Linux root")
    def test_source_copy_is_independent_and_preserves_symlink_text(self) -> None:
        loaded = native_build_tool.load_profile(
            COMMITTED_PROFILE,
            COMMITTED_SOURCE_MANIFEST,
            COMMITTED_TOOLCHAIN_MANIFEST,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "buildscripts" / "include").mkdir(parents=True)
            original_buildall = b"#!/bin/sh\nold buildall\n"
            original_path = b"#!/bin/sh\nold path\n"
            buildall = source / "buildscripts" / "buildall.sh"
            path_script = source / "buildscripts" / "include" / "path.sh"
            buildall.write_bytes(original_buildall)
            path_script.write_bytes(original_path)
            (source / "target").write_bytes(b"payload")
            (source / "link").symlink_to("target")
            (source / "dangling").symlink_to("missing")
            (source / native_build_tool.materialize_sources.RECEIPT_NAME).write_bytes(b"{}\n")
            for directory in (
                source / "buildscripts" / "include",
                source / "buildscripts",
                source,
            ):
                os.chown(directory, 0, 0)
                os.chmod(directory, 0o755)
                os.utime(
                    directory,
                    ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2,
                )
            for file_path, mode in (
                (buildall, 0o755),
                (path_script, 0o755),
                (source / "target", 0o644),
                (source / native_build_tool.materialize_sources.RECEIPT_NAME, 0o644),
            ):
                os.chown(file_path, 0, 0)
                os.chmod(file_path, mode)
                os.utime(
                    file_path,
                    ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2,
                )
            for link in (source / "link", source / "dangling"):
                os.lchown(link, 0, 0)
                os.utime(
                    link,
                    ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2,
                    follow_symlinks=False,
                )

            canonical = native_build_tool._scan_policy_tree(  # noqa: SLF001
                source,
                "test canonical source",
                skip_materialization_receipt=True,
            )
            workspace = root / "workspace"
            workspace.mkdir(mode=0o700)
            copied_root = workspace / "source"
            native_build_tool._copy_source_tree(source, copied_root)  # noqa: SLF001
            copied = native_build_tool._scan_policy_tree(  # noqa: SLF001
                copied_root,
                "test copied source",
                skip_materialization_receipt=False,
            )
            self.assertEqual(canonical.tree, copied.tree)
            self.assertEqual(canonical.file_bytes, copied.file_bytes)
            self.assertEqual(canonical.symlinks, copied.symlinks)
            self.assertEqual("target", os.readlink(copied_root / "link"))
            self.assertEqual("missing", os.readlink(copied_root / "dangling"))
            self.assertFalse(
                (copied_root / native_build_tool.materialize_sources.RECEIPT_NAME).exists()
            )
            native_build_tool._assert_independent_copy(canonical, copied)  # noqa: SLF001

            replacement_buildall = b"#!/bin/sh\nnew buildall\n"
            replacement_path = b"#!/bin/sh\nnew path\n"
            overlays = (
                {
                    "destination": "buildscripts/buildall.sh",
                    "replacement": "unused/buildall.sh",
                    "originalSize": len(original_buildall),
                    "originalSha256": hashlib.sha256(original_buildall).hexdigest(),
                    "replacementSize": len(replacement_buildall),
                    "replacementSha256": hashlib.sha256(replacement_buildall).hexdigest(),
                    "originalMode": 0o755,
                    "replacementMode": 0o755,
                },
                {
                    "destination": "buildscripts/include/path.sh",
                    "replacement": "unused/path.sh",
                    "originalSize": len(original_path),
                    "originalSha256": hashlib.sha256(original_path).hexdigest(),
                    "replacementSize": len(replacement_path),
                    "replacementSha256": hashlib.sha256(replacement_path).hexdigest(),
                    "originalMode": 0o755,
                    "replacementMode": 0o755,
                },
            )
            test_profile = replace(loaded, overlays=overlays)
            replacement_raws = (replacement_buildall, replacement_path)
            with patch.object(
                native_build_tool,
                "_write_all",
                side_effect=OSError("injected overlay write failure"),
            ), self.assertRaises(source_tool.SourceToolError):
                native_build_tool._apply_overlays(  # noqa: SLF001
                    test_profile,
                    copied_root,
                    replacement_raws,
                )
            self.assertEqual(
                original_buildall,
                (copied_root / "buildscripts/buildall.sh").read_bytes(),
            )
            self.assertFalse(
                list((copied_root / "buildscripts").glob(".buildall.sh.*.overlay"))
            )
            native_build_tool._apply_overlays(  # noqa: SLF001
                test_profile,
                copied_root,
                replacement_raws,
            )
            native_build_tool._verify_applied_overlays(  # noqa: SLF001
                test_profile,
                copied_root,
                replacement_raws,
            )
            self.assertEqual(original_buildall, buildall.read_bytes())
            self.assertEqual(
                replacement_buildall,
                (copied_root / "buildscripts/buildall.sh").read_bytes(),
            )
            self.assertEqual(
                0o755,
                stat.S_IMODE((copied_root / "buildscripts/buildall.sh").stat().st_mode),
            )
            prepared = native_build_tool._scan_policy_tree(  # noqa: SLF001
                copied_root,
                "test prepared source after overlays",
                skip_materialization_receipt=False,
            )

            source_receipt = {"linkMode": "preserve", "tree": canonical.tree}
            composition_receipt = {
                "composition": {"sha256": "2" * 64},
                "inputs": {},
            }
            inputs = native_build_tool.PreparationInputs(
                profile=test_profile,
                source_receipt=source_receipt,
                source_receipt_raw=native_build_tool._canonical_json(  # noqa: SLF001
                    source_receipt
                ),
                composition_receipt=composition_receipt,
                composition_receipt_raw=native_build_tool._canonical_json(  # noqa: SLF001
                    composition_receipt
                ),
                overlay_raws=replacement_raws,
                canonical_source=canonical,
            )
            for name in ("output", "home", "tmp"):
                native_build_tool._create_empty_prepared_directory(  # noqa: SLF001
                    workspace / name
                )
            receipt = native_build_tool._preparation_receipt_data(  # noqa: SLF001
                inputs,
                prepared,
            )
            native_build_tool._write_preparation_receipt(  # noqa: SLF001
                workspace / native_build_tool.PREPARATION_RECEIPT_NAME,
                receipt,
            )
            os.chown(workspace, 0, 0)
            os.chmod(workspace, 0o700)
            os.utime(workspace, ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2)
            native_build_tool._clear_xattrs(workspace)  # noqa: SLF001
            native_build_tool.materialize_sources._fsync_workspace_directories(  # noqa: SLF001
                workspace
            )
            verified_receipt, verified_raw = (
                native_build_tool._verify_prepared_workspace(  # noqa: SLF001
                    workspace,
                    inputs,
                )
            )
            self.assertEqual(receipt, verified_receipt)
            self.assertEqual(
                native_build_tool._canonical_json(receipt),  # noqa: SLF001
                verified_raw,
            )

    @unittest.skipUnless(LINUX_ROOT, "requires Linux root")
    def test_policy_scan_rejects_hardlinked_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            first = source / "first"
            second = source / "second"
            first.write_bytes(b"same inode")
            os.link(first, second)
            for path, mode in ((first, 0o644), (source, 0o755)):
                os.chown(path, 0, 0)
                os.chmod(path, mode)
                os.utime(path, ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2)
            with self.assertRaises(source_tool.SourceToolError) as raised:
                native_build_tool._scan_policy_tree(  # noqa: SLF001
                    source,
                    "test source",
                    skip_materialization_receipt=False,
                )
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("hard-linked", str(raised.exception))

    @unittest.skipUnless(LINUX_ROOT, "requires Linux root")
    def test_failed_root_setup_closes_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            os.chown(source, 0, 0)
            os.chmod(source, 0o700)
            os.utime(source, ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2)
            before = len(os.listdir("/proc/self/fd"))
            for _attempt in range(8):
                with self.assertRaises(source_tool.SourceToolError):
                    native_build_tool._scan_policy_tree(  # noqa: SLF001
                        source,
                        "invalid-mode source",
                        skip_materialization_receipt=False,
                    )
            self.assertEqual(before, len(os.listdir("/proc/self/fd")))

            os.chmod(source, 0o755)
            before = len(os.listdir("/proc/self/fd"))
            for _attempt in range(8):
                with self.assertRaises(source_tool.SourceToolError):
                    native_build_tool._copy_source_tree(  # noqa: SLF001
                        source,
                        destination,
                    )
            self.assertEqual(before, len(os.listdir("/proc/self/fd")))

    @unittest.skipUnless(LINUX_ROOT, "requires Linux root")
    def test_staging_identity_is_rechecked_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            staging = parent / "staging"
            displaced = parent / "displaced"
            staging.mkdir(mode=0o700)
            os.chown(staging, 0, 0)
            os.utime(staging, ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2)
            identity = (staging.stat().st_dev, staging.stat().st_ino)
            staging.rename(displaced)
            staging.mkdir(mode=0o700)
            os.chown(staging, 0, 0)
            os.utime(staging, ns=(native_build_tool.NORMALIZED_MTIME_NS,) * 2)
            parent_fd = os.open(parent, native_build_tool._directory_flags())  # noqa: SLF001
            try:
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    native_build_tool._assert_staging_identity_at(  # noqa: SLF001
                        parent_fd,
                        staging.name,
                        identity,
                    )
            finally:
                os.close(parent_fd)
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertIn("identity changed", str(raised.exception))

    @unittest.skipUnless(LINUX_ROOT, "requires Linux root")
    def test_workspace_publication_is_no_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            staging = parent / "staging"
            destination = parent / "destination"
            staging.mkdir()
            destination.mkdir()
            parent_fd = os.open(parent, native_build_tool._directory_flags())  # noqa: SLF001
            try:
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    native_build_tool._rename_workspace_no_replace_at(  # noqa: SLF001
                        parent_fd,
                        staging.name,
                        destination.name,
                    )
            finally:
                os.close(parent_fd)
            self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)
            self.assertTrue(staging.is_dir())
            self.assertTrue(destination.is_dir())


if __name__ == "__main__":
    unittest.main()
