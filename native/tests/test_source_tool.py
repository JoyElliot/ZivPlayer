# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import http.client
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import source_tool  # noqa: E402


CONTENT = b"locked native source archive\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def valid_manifest() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "project": {
            "name": "ZivPlayer",
            "upstreamRelease": "2026-08-11",
            "upstreamRevision": "a" * 40,
            "minimumAndroidApi": 26,
            "nativeApi": 26,
            "abis": ["arm64-v8a", "x86_64"],
            "pageSizeBytes": 16384,
            "licenseProfile": "gpl-v3-compatible-full",
        },
        "toolchain": {
            "hostOs": "linux",
            "ndkVersion": "29.0.14206865",
            "sdkPlatform": 36,
            "sdkBuildTools": "36.0.0",
            "jdkMajor": 17,
            "pythonMinimum": "3.11",
            "mesonMinimum": "1.11.0",
            "ninjaMinimum": "1.8.2",
            "gnuMakeMinimum": "3.82",
            "buildSystems": ["make", "meson", "ninja"],
            "cmake": "not-used",
            "environmentStatus": "container-not-yet-locked",
        },
        "source": [
            {
                "id": "example",
                "version": "1.0.0",
                "kind": "git-snapshot",
                "url": "https://example.invalid/example.tar.gz",
                "archive": "example.tar.gz",
                "size": len(CONTENT),
                "sha256": DIGEST,
                "revision": "b" * 40,
                "license": "MIT",
                "licenseFiles": ["LICENSE"],
                "role": "test source",
                "linkage": "static",
                "buildSystem": "meson",
                "dependencies": [],
                "requiredForBuild": True,
            }
        ],
    }


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        content: bytes,
        final_url: str = "https://cdn.example.invalid/example.tar.gz",
    ) -> None:
        super().__init__(content)
        self.final_url = final_url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def geturl(self) -> str:
        return self.final_url


class IncompleteResponse(FakeResponse):
    def read(self, size: int = -1) -> bytes:
        raise http.client.IncompleteRead(b"partial", 100)


class InterruptingResponse(FakeResponse):
    def read(self, size: int = -1) -> bytes:
        raise KeyboardInterrupt


def partial_files(cache: Path) -> list[Path]:
    return [path for path in cache.iterdir() if path.name.endswith(".part")]


class SourceManifestTest(unittest.TestCase):
    def test_committed_manifest_is_valid(self) -> None:
        _, sources = source_tool.load_manifest(REPOSITORY_ROOT / "native" / "source-manifest.toml")
        self.assertEqual(23, len(sources))
        self.assertEqual(
            source_tool.CANONICAL_SOURCE_IDS,
            tuple(str(source["id"]) for source in sources),
        )

    def test_rejects_duplicate_source_ids(self) -> None:
        manifest = valid_manifest()
        manifest["source"].append(deepcopy(manifest["source"][0]))
        with self.assertRaisesRegex(source_tool.SourceToolError, "source ids must be unique") as raised:
            source_tool.validate_manifest_data(manifest)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_mutable_git_revision(self) -> None:
        manifest = valid_manifest()
        manifest["source"][0]["revision"] = "main"
        with self.assertRaisesRegex(source_tool.SourceToolError, "mutable ref") as raised:
            source_tool.validate_manifest_data(manifest)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_unsafe_archive_name(self) -> None:
        manifest = valid_manifest()
        manifest["source"][0]["archive"] = "../example.tar.gz"
        with self.assertRaisesRegex(source_tool.SourceToolError, "safe basename") as raised:
            source_tool.validate_manifest_data(manifest)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_noncanonical_relative_paths(self) -> None:
        for path in ("./LICENSE", "licenses//LICENSE", "licenses/./LICENSE", "licenses/"):
            with self.subTest(path=path):
                manifest = valid_manifest()
                manifest["source"][0]["licenseFiles"] = [path]
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "safe POSIX relative path"
                ) as raised:
                    source_tool.validate_manifest_data(manifest)
                self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_nonportable_archive_names(self) -> None:
        for archive in ("NUL", "foo.", "foo:bar", "a?b.tar.gz"):
            with self.subTest(archive=archive):
                manifest = valid_manifest()
                manifest["source"][0]["archive"] = archive
                with self.assertRaisesRegex(
                    source_tool.SourceToolError, "portable ASCII file name"
                ) as raised:
                    source_tool.validate_manifest_data(manifest)
                self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_nonportable_source_ids(self) -> None:
        for source_id in ("con", "a" * 201):
            with self.subTest(source_id=source_id):
                manifest = valid_manifest()
                manifest["source"][0]["id"] = source_id
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    source_tool.validate_manifest_data(manifest)
                self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_oversized_source_archive(self) -> None:
        manifest = valid_manifest()
        manifest["source"][0]["size"] = source_tool.MAX_SOURCE_ARCHIVE_BYTES + 1
        with self.assertRaisesRegex(source_tool.SourceToolError, "must be <=") as raised:
            source_tool.validate_manifest_data(manifest)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_case_insensitive_archive_collision(self) -> None:
        manifest = valid_manifest()
        first = manifest["source"][0]
        first["id"] = "a"
        first["archive"] = "Example.tar.gz"
        second = deepcopy(first)
        second["id"] = "b"
        second["archive"] = "example.tar.gz"
        second["revision"] = "c" * 40
        manifest["source"].append(second)
        with self.assertRaisesRegex(
            source_tool.SourceToolError, "case-insensitive filesystems"
        ) as raised:
            source_tool.validate_manifest_data(manifest)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_non_https_url(self) -> None:
        manifest = valid_manifest()
        manifest["source"][0]["url"] = "http://example.invalid/example.tar.gz"
        with self.assertRaisesRegex(source_tool.SourceToolError, "credential-free HTTPS") as raised:
            source_tool.validate_manifest_data(manifest)
        self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_malformed_url_and_port_as_schema_errors(self) -> None:
        for url in (
            "https://[bad]/example.tar.gz",
            "https://example.invalid/a b.tar.gz",
            "https://example.invalid/a\u00a0b.tar.gz",
            "https://example.invalid:not-a-port/example.tar.gz",
            "https://example.invalid:99999/example.tar.gz",
        ):
            with self.subTest(url=url):
                manifest = valid_manifest()
                manifest["source"][0]["url"] = url
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    source_tool.validate_manifest_data(manifest)
                self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_rejects_dependency_and_parent_cycles(self) -> None:
        for relation in ("dependencies", "parent"):
            with self.subTest(relation=relation):
                manifest = valid_manifest()
                first = manifest["source"][0]
                first["id"] = "a"
                second = deepcopy(first)
                second["id"] = "b"
                second["archive"] = "b.tar.gz"
                second["url"] = "https://example.invalid/b.tar.gz"
                second["revision"] = "c" * 40
                if relation == "dependencies":
                    first["dependencies"] = ["b"]
                    second["dependencies"] = ["a"]
                else:
                    first["parent"] = "b"
                    first["destination"] = "submodules/a"
                    second["parent"] = "a"
                    second["destination"] = "submodules/b"
                manifest["source"].append(second)
                with self.assertRaisesRegex(source_tool.SourceToolError, "contains a cycle") as raised:
                    source_tool.validate_manifest_data(manifest)
                self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_redirect_handler_rejects_https_downgrade(self) -> None:
        handler = source_tool.HTTPSOnlyRedirectHandler()
        request = source_tool.urllib.request.Request("https://example.invalid/source.tar.gz")
        with self.assertRaises(source_tool.SourceToolError) as raised:
            handler.redirect_request(request, None, 302, "Found", {}, "http://example.invalid/file")
        self.assertEqual(source_tool.EXIT_NETWORK, raised.exception.exit_code)

    def test_verify_cache_reports_missing_without_network(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory, patch("source_tool._open_https") as open_https:
            with self.assertRaises(source_tool.SourceToolError) as raised:
                source_tool.verify_cache(sources, Path(directory))
        self.assertEqual(source_tool.EXIT_MISSING, raised.exception.exit_code)
        open_https.assert_not_called()

    def test_verify_cache_rejects_hash_mismatch(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "example.tar.gz").write_bytes(b"x" * len(CONTENT))
            with self.assertRaises(source_tool.SourceToolError) as raised:
                source_tool.verify_cache(sources, cache)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_verify_cache_does_not_modify_valid_file(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            archive = cache / "example.tar.gz"
            archive.write_bytes(CONTENT)
            before = archive.stat().st_mtime_ns
            source_tool.verify_cache(sources, cache)
            self.assertEqual(CONTENT, archive.read_bytes())
            self.assertEqual(before, archive.stat().st_mtime_ns)

    def test_open_verified_archive_yields_stable_snapshot(self) -> None:
        source = source_tool.validate_manifest_data(valid_manifest())[0]
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "example.tar.gz"
            archive.write_bytes(CONTENT)
            with source_tool.open_verified_archive(source, archive) as snapshot:
                archive.write_bytes(b"x" * len(CONTENT))
                self.assertEqual(CONTENT, snapshot.read())
            self.assertEqual(b"x" * len(CONTENT), archive.read_bytes())

    def test_fetch_uses_atomic_part_and_verifies_result(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory, patch(
            "source_tool._open_https", return_value=FakeResponse(CONTENT)
        ):
            cache = Path(directory)
            source_tool.fetch_sources(sources, cache, timeout=1.0)
            self.assertEqual(CONTENT, (cache / "example.tar.gz").read_bytes())
            self.assertEqual([], partial_files(cache))

    def test_fetch_does_not_replace_concurrent_valid_winner(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory, patch(
            "source_tool._open_https", return_value=FakeResponse(CONTENT)
        ), patch("source_tool._publish_download_no_replace") as publish:
            cache = Path(directory)

            def concurrent_winner(
                part: Path, archive: Path, expected_identity: tuple[int, int]
            ) -> bool:
                archive.write_bytes(CONTENT)
                return False

            publish.side_effect = concurrent_winner
            source_tool.fetch_sources(sources, cache, timeout=1.0)
            self.assertEqual(CONTENT, (cache / "example.tar.gz").read_bytes())
            self.assertEqual([], partial_files(cache))

    def test_fetch_cleans_part_after_integrity_failure(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory, patch(
            "source_tool._open_https", return_value=FakeResponse(b"corrupt")
        ):
            cache = Path(directory)
            with self.assertRaises(source_tool.SourceToolError) as raised:
                source_tool.fetch_sources(sources, cache, timeout=1.0)
            self.assertEqual([], partial_files(cache))
            self.assertFalse((cache / "example.tar.gz").exists())
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_fetch_cleans_part_after_incomplete_read(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory, patch(
            "source_tool._open_https", return_value=IncompleteResponse(CONTENT)
        ):
            cache = Path(directory)
            with self.assertRaises(source_tool.SourceToolError) as raised:
                source_tool.fetch_sources(sources, cache, timeout=1.0)
            self.assertEqual([], partial_files(cache))
            self.assertFalse((cache / "example.tar.gz").exists())
        self.assertEqual(source_tool.EXIT_NETWORK, raised.exception.exit_code)

    def test_fetch_cleans_part_after_keyboard_interrupt(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory, patch(
            "source_tool._open_https", return_value=InterruptingResponse(CONTENT)
        ):
            cache = Path(directory)
            with self.assertRaises(KeyboardInterrupt):
                source_tool.fetch_sources(sources, cache, timeout=1.0)
            self.assertEqual([], partial_files(cache))
            self.assertFalse((cache / "example.tar.gz").exists())

    def test_fetch_rejects_credentialed_final_url(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        response = FakeResponse(CONTENT, "https://user:secret@example.invalid/file.tar.gz?token=ok")
        with tempfile.TemporaryDirectory() as directory, patch(
            "source_tool._open_https", return_value=response
        ):
            cache = Path(directory)
            with self.assertRaises(source_tool.SourceToolError) as raised:
                source_tool.fetch_sources(sources, cache, timeout=1.0)
            self.assertEqual([], partial_files(cache))
        self.assertEqual(source_tool.EXIT_NETWORK, raised.exception.exit_code)

    def test_fetch_does_not_overwrite_corrupt_existing_cache(self) -> None:
        sources = source_tool.validate_manifest_data(valid_manifest())
        with tempfile.TemporaryDirectory() as directory, patch("source_tool._open_https") as open_https:
            cache = Path(directory)
            archive = cache / "example.tar.gz"
            corrupt = b"x" * len(CONTENT)
            archive.write_bytes(corrupt)
            with self.assertRaises(source_tool.SourceToolError) as raised:
                source_tool.fetch_sources(sources, cache, timeout=1.0)
            self.assertEqual(corrupt, archive.read_bytes())
            open_https.assert_not_called()
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_fetch_rejects_non_finite_timeout_as_schema_error(self) -> None:
        manifest = REPOSITORY_ROOT / "native" / "source-manifest.toml"
        with tempfile.TemporaryDirectory() as directory:
            with redirect_stderr(io.StringIO()):
                result = source_tool.main(
                    ["--manifest", str(manifest), "fetch", "--cache", directory, "--timeout", "nan"]
                )
        self.assertEqual(source_tool.EXIT_SCHEMA, result)


if __name__ == "__main__":
    unittest.main()
