# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import copy
import gzip
import hashlib
import io
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import source_tool  # noqa: E402
import toolchain_tool  # noqa: E402


COMMITTED_MANIFEST = REPOSITORY_ROOT / "native" / "toolchain-manifest.toml"
COMMITTED_SOURCE_MANIFEST = REPOSITORY_ROOT / "native" / "source-manifest.toml"


def committed_data() -> dict[str, object]:
    with COMMITTED_MANIFEST.open("rb") as stream:
        return tomllib.load(stream)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def set_locked_payload(entry: dict[str, object], path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    entry["size"] = len(payload)
    if "digest" in entry:
        entry["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    else:
        entry["sha256"] = hashlib.sha256(payload).hexdigest()
        if "publishedSha1" in entry:
            entry["publishedSha1"] = hashlib.sha1(
                payload,
                usedforsecurity=False,
            ).hexdigest()


def make_complete_cache(
    data: dict[str, object],
    cache: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    artifacts = data["artifact"]
    oci_objects = data["ociObject"]
    assert isinstance(artifacts, list)
    assert isinstance(oci_objects, list)
    cache.mkdir()
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        set_locked_payload(
            artifact,
            cache / str(artifact["archive"]),
            f"artifact:{artifact['id']}\n".encode("ascii"),
        )

    by_id = {
        str(entry["id"]): entry
        for entry in oci_objects
        if isinstance(entry, dict)
    }
    config = by_id["ubuntu-config-amd64"]
    layer = by_id["ubuntu-layer-amd64"]
    manifest = by_id["ubuntu-manifest-amd64"]
    index = by_id["ubuntu-index"]
    uncompressed_layer = b"locked-rootfs-layer\n"
    set_locked_payload(
        layer,
        cache / str(layer["archive"]),
        gzip.compress(uncompressed_layer, mtime=0),
    )
    set_locked_payload(
        config,
        cache / str(config["archive"]),
        canonical_json(
            {
                "architecture": "amd64",
                "os": "linux",
                "rootfs": {
                    "type": "layers",
                    "diff_ids": [
                        "sha256:" + hashlib.sha256(uncompressed_layer).hexdigest()
                    ],
                },
            }
        ),
    )
    manifest_payload = canonical_json(
        {
            "schemaVersion": 2,
            "mediaType": manifest["mediaType"],
            "config": {
                "mediaType": config["mediaType"],
                "digest": config["digest"],
                "size": config["size"],
            },
            "layers": [
                {
                    "mediaType": layer["mediaType"],
                    "digest": layer["digest"],
                    "size": layer["size"],
                }
            ],
        }
    )
    set_locked_payload(
        manifest,
        cache / str(manifest["archive"]),
        manifest_payload,
    )
    index_payload = canonical_json(
        {
            "schemaVersion": 2,
            "mediaType": index["mediaType"],
            "manifests": [
                {
                    "mediaType": manifest["mediaType"],
                    "digest": manifest["digest"],
                    "size": manifest["size"],
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    )
    set_locked_payload(
        index,
        cache / str(index["archive"]),
        index_payload,
    )
    base = data["baseImage"]
    assert isinstance(base, dict)
    base["indexDigest"] = index["digest"]
    base["manifestDigest"] = manifest["digest"]
    base["buildReference"] = "docker.io/library/ubuntu@" + str(manifest["digest"])
    return artifacts, oci_objects


class FakeResponse:
    def __init__(self, payload: bytes, url: str) -> None:
        self._payload = io.BytesIO(payload)
        self._url = url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._payload.read(size)

    def geturl(self) -> str:
        return self._url


class ToolchainToolTest(unittest.TestCase):
    def test_committed_manifest_and_source_binding_are_valid(self) -> None:
        data, artifacts, oci_objects, source_data = toolchain_tool.load_manifest(
            COMMITTED_MANIFEST,
            source_manifest_path=COMMITTED_SOURCE_MANIFEST,
        )
        self.assertEqual(1, data["schemaVersion"])
        self.assertEqual(5, len(artifacts))
        self.assertEqual(4, len(oci_objects))
        self.assertEqual(
            "source-locked-container-pending",
            source_data["toolchain"]["environmentStatus"],
        )

    def test_manifest_rejects_unknown_keys_and_boolean_sizes(self) -> None:
        data = committed_data()
        data["project"]["unexpected"] = True
        with self.assertRaisesRegex(source_tool.SourceToolError, "unknown keys"):
            toolchain_tool.validate_manifest_data(data)

        data = committed_data()
        data["artifact"][0]["size"] = True
        with self.assertRaisesRegex(source_tool.SourceToolError, "integer"):
            toolchain_tool.validate_manifest_data(data)

    def test_manifest_rejects_unknown_or_case_changed_pending_status(self) -> None:
        for status in ("ROOTS-LOCKED-CLOSURE-PENDING", "ready", ""):
            with self.subTest(status=status):
                data = committed_data()
                data["project"]["environmentStatus"] = status
                with self.assertRaises(source_tool.SourceToolError) as raised:
                    toolchain_tool.validate_manifest_data(data)
                self.assertEqual(source_tool.EXIT_SCHEMA, raised.exception.exit_code)

    def test_manifest_rejects_unsafe_and_colliding_install_paths(self) -> None:
        data = committed_data()
        data["artifact"][0]["installPath"] = "android-sdk/../escape"
        with self.assertRaisesRegex(source_tool.SourceToolError, "safe POSIX"):
            toolchain_tool.validate_manifest_data(data)

        data = committed_data()
        data["artifact"][1]["installPath"] = "android-sdk/BUILD-TOOLS/36.0.0"
        with self.assertRaisesRegex(source_tool.SourceToolError, "unique after NFKC"):
            toolchain_tool.validate_manifest_data(data)

    def test_manifest_rejects_noncanonical_unicode_path(self) -> None:
        data = committed_data()
        data["artifact"][0]["installPath"] = "android-sdk／escape"
        with self.assertRaisesRegex(source_tool.SourceToolError, "NFKC"):
            toolchain_tool.validate_manifest_data(data)

    def test_manifest_accepts_locked_transport_name_with_underscores(self) -> None:
        _, artifacts, _ = toolchain_tool.validate_manifest_data(committed_data())
        archives = [str(artifact["archive"]) for artifact in artifacts]
        self.assertIn("build-tools_r36_linux.zip", archives)

    def test_manifest_rejects_floating_build_reference(self) -> None:
        data = committed_data()
        data["baseImage"]["buildReference"] = data["baseImage"]["reference"]
        with self.assertRaisesRegex(source_tool.SourceToolError, "digest-qualified"):
            toolchain_tool.validate_manifest_data(data)

    def test_manifest_rejects_base_and_oci_digest_divergence(self) -> None:
        data = committed_data()
        data["baseImage"]["indexDigest"] = "sha256:" + ("0" * 64)
        with self.assertRaisesRegex(source_tool.SourceToolError, "OCI index"):
            toolchain_tool.validate_manifest_data(data)

    def test_source_manifest_binding_rejects_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source-manifest.toml"
            source_path.write_bytes(COMMITTED_SOURCE_MANIFEST.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                source_tool.SourceToolError,
                "source manifest SHA-256",
            ) as raised:
                toolchain_tool.load_manifest(
                    COMMITTED_MANIFEST,
                    source_manifest_path=source_path,
                )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_source_binding_rejects_tuple_divergence(self) -> None:
        data = committed_data()
        project, artifacts, _ = toolchain_tool.validate_manifest_data(data)
        with COMMITTED_SOURCE_MANIFEST.open("rb") as stream:
            source_data = tomllib.load(stream)
        source_data["project"]["nativeApi"] = 25
        with self.assertRaisesRegex(source_tool.SourceToolError, "disagree") as raised:
            toolchain_tool._validate_source_binding_tuple(
                project,
                artifacts,
                source_data,
            )
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_verify_cache_reports_all_missing_without_network(self) -> None:
        data = committed_data()
        _, artifacts, oci_objects = toolchain_tool.validate_manifest_data(data)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            toolchain_tool,
            "_open_https",
            side_effect=AssertionError("offline verifier attempted network"),
        ):
            with self.assertRaisesRegex(
                source_tool.SourceToolError,
                "missing 9 input",
            ) as raised:
                toolchain_tool.verify_roots(
                    data,
                    artifacts,
                    oci_objects,
                    Path(directory) / "cache",
                )
        self.assertEqual(source_tool.EXIT_MISSING, raised.exception.exit_code)

    def test_verify_cache_checks_sha256_published_sha1_and_oci_graph(self) -> None:
        data = committed_data()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            artifacts, oci_objects = make_complete_cache(data, cache)
            toolchain_tool.validate_manifest_data(data)
            toolchain_tool.verify_roots(data, artifacts, oci_objects, cache)

            artifacts[0]["publishedSha1"] = "0" * 40
            with self.assertRaisesRegex(
                source_tool.SourceToolError,
                "published SHA-1 mismatch",
            ) as raised:
                toolchain_tool.verify_roots(data, artifacts, oci_objects, cache)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_verify_cache_rejects_corrupt_bytes(self) -> None:
        data = committed_data()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            artifacts, oci_objects = make_complete_cache(data, cache)
            path = cache / str(artifacts[0]["archive"])
            path.write_bytes(b"corrupt")
            with self.assertRaises(source_tool.SourceToolError) as raised:
                toolchain_tool.verify_roots(data, artifacts, oci_objects, cache)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_verify_cache_rejects_duplicate_oci_json_keys(self) -> None:
        data = committed_data()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            artifacts, oci_objects = make_complete_cache(data, cache)
            index = next(entry for entry in oci_objects if entry["kind"] == "index")
            duplicate = b'{"schemaVersion":2,"schemaVersion":2}'
            set_locked_payload(index, cache / str(index["archive"]), duplicate)
            data["baseImage"]["indexDigest"] = index["digest"]
            with self.assertRaisesRegex(
                source_tool.SourceToolError,
                "duplicate JSON key",
            ) as raised:
                toolchain_tool.verify_roots(data, artifacts, oci_objects, cache)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_verify_cache_rejects_oci_descriptor_divergence(self) -> None:
        data = committed_data()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            artifacts, oci_objects = make_complete_cache(data, cache)
            by_id = {str(entry["id"]): entry for entry in oci_objects}
            index = by_id["ubuntu-index"]
            manifest = by_id["ubuntu-manifest-amd64"]
            bad_index = canonical_json(
                {
                    "schemaVersion": 2,
                    "mediaType": index["mediaType"],
                    "manifests": [
                        {
                            "mediaType": manifest["mediaType"],
                            "digest": "sha256:" + ("0" * 64),
                            "size": manifest["size"],
                            "platform": {"architecture": "amd64", "os": "linux"},
                        }
                    ],
                }
            )
            set_locked_payload(index, cache / str(index["archive"]), bad_index)
            data["baseImage"]["indexDigest"] = index["digest"]
            with self.assertRaisesRegex(
                source_tool.SourceToolError,
                "does not match",
            ) as raised:
                toolchain_tool.verify_roots(data, artifacts, oci_objects, cache)
        self.assertEqual(source_tool.EXIT_INTEGRITY, raised.exception.exit_code)

    def test_check_lock_fails_closed_after_all_declared_bytes_verify(self) -> None:
        data = committed_data()
        with COMMITTED_SOURCE_MANIFEST.open("rb") as stream:
            source_data = tomllib.load(stream)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            artifacts, oci_objects = make_complete_cache(data, cache)
            with patch.object(toolchain_tool, "verify_cache") as verifier:
                with self.assertRaisesRegex(
                    source_tool.SourceToolError,
                    "toolchain environment status is pending",
                ) as raised:
                    toolchain_tool.check_lock(
                        data,
                        artifacts,
                        oci_objects,
                        source_data,
                        cache,
                        cache / "apt-proof",
                    )
                verifier.assert_called_once_with(
                    data,
                    artifacts,
                    oci_objects,
                    cache,
                    cache / "apt-proof",
                )
        self.assertEqual(source_tool.EXIT_MISSING, raised.exception.exit_code)

    def test_registry_object_download_is_hash_checked_and_published(self) -> None:
        payload = b"registry-object\n"
        entry: dict[str, object] = {
            "id": "test-config",
            "kind": "config",
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "archive": "test-config.json",
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            toolchain_tool,
            "_open_registry_https",
            return_value=FakeResponse(
                payload,
                "https://registry-1.docker.io/v2/library/ubuntu/blobs/digest",
            ),
        ) as opened:
            cache = Path(directory)
            toolchain_tool._download_registry_object(
                entry,
                "library/ubuntu",
                "token",
                cache,
                1.0,
            )
            self.assertEqual(payload, (cache / "test-config.json").read_bytes())
            request = opened.call_args.args[0]
            self.assertEqual("Bearer token", request.headers["Authorization"])

    def test_manifest_rejects_any_other_apt_snapshot(self) -> None:
        data = committed_data()
        data["apt"]["snapshot"] = "20260812T000000Z"
        with self.assertRaisesRegex(source_tool.SourceToolError, "locked 20260811"):
            toolchain_tool.validate_manifest_data(data)

    def test_debian_version_comparator_matches_locked_edge_cases(self) -> None:
        vectors = (
            ("1:1.0-1", "1.0-99", 1),
            ("01:1.0", "1:1.0", 0),
            ("1.0~rc1", "1.0", -1),
            ("1.0~~", "1.0~~a", -1),
            ("1.0", "1.0-0", 0),
            ("1.9", "1.10", -1),
            ("13.3.0-6ubuntu2~24.04.1", "13.3.0-6ubuntu2.24.04.1", -1),
            ("4:13.2.0-7ubuntu1", "13.3.0-6ubuntu2~24.04.1", 1),
        )
        for left, right, expected in vectors:
            with self.subTest(left=left, right=right):
                self.assertEqual(expected, toolchain_tool._compare_debian_versions(left, right))
        for invalid in ("1.0_bad", "1:2:3", "alpha", "1.0-", ("9" * 5000) + ":1.0"):
            with self.subTest(invalid=invalid), self.assertRaises(
                source_tool.SourceToolError
            ):
                toolchain_tool._debian_version_parts(invalid)

    def test_inrelease_parser_accepts_noble_updates_codename(self) -> None:
        digest = "a" * 64
        raw = (
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA512\n\n"
            "Origin: Ubuntu\n"
            "Suite: noble-updates\n"
            "Codename: noble\n"
            "Architectures: amd64\n"
            "Components: main universe\n"
            "SHA256:\n"
            f" {digest} 7 main/binary-amd64/Packages\n"
            "-----BEGIN PGP SIGNATURE-----\n"
            "fake\n"
            "-----END PGP SIGNATURE-----\n"
        ).encode("ascii")
        published, release = toolchain_tool._parse_inrelease(raw, "noble-updates")
        self.assertEqual((digest, 7), published["main/binary-amd64/Packages"])
        self.assertEqual("noble", release["Codename"])
        oversized = raw.replace(b" 7 main/", b" " + (b"9" * 5000) + b" main/")
        with self.assertRaisesRegex(source_tool.SourceToolError, "bounded ASCII decimal"):
            toolchain_tool._parse_inrelease(oversized, "noble-updates")

    def test_exact_regular_names_rejects_non_file_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "locked").write_bytes(b"ok")
            (root / "extra-directory").mkdir()
            with self.assertRaisesRegex(source_tool.SourceToolError, "non-regular"):
                toolchain_tool._exact_regular_names(root, "test cache")

    def test_dependency_closure_resolves_required_virtual_provider(self) -> None:
        base = [
            {
                "Package": "mawk",
                "Architecture": "amd64",
                "Version": "1.3.4.20240123-1",
                "Provides": "awk",
            }
        ]
        selected = [
            {
                "Package": "root-package",
                "Architecture": "amd64",
                "Version": "1.0-1",
                "Pre-Depends": "awk",
            }
        ]
        self.assertEqual(
            1,
            toolchain_tool._verify_dependency_closure(
                base,
                selected,
                ["root-package"],
            ),
        )

    def test_dependency_any_requires_multi_arch_allowed(self) -> None:
        root = {
            "Package": "root-package",
            "Architecture": "amd64",
            "Version": "1.0-1",
            "Depends": "perl:any",
        }
        perl = {
            "Package": "perl",
            "Architecture": "amd64",
            "Version": "5.38.2-1",
            "Multi-Arch": "allowed",
        }
        self.assertEqual(
            1,
            toolchain_tool._verify_dependency_closure([], [root, perl], ["root-package"]),
        )
        perl["Multi-Arch"] = "foreign"
        with self.assertRaisesRegex(source_tool.SourceToolError, "unsatisfied"):
            toolchain_tool._verify_dependency_closure([], [root, perl], ["root-package"])

    def test_versioned_provides_must_satisfy_requested_version(self) -> None:
        root = {
            "Package": "root-package",
            "Architecture": "amd64",
            "Version": "1.0-1",
            "Depends": "virtual-api (>= 2.0)",
        }
        provider = {
            "Package": "provider",
            "Architecture": "amd64",
            "Version": "3.0-1",
            "Provides": "virtual-api (= 2.1)",
        }
        self.assertEqual(
            1,
            toolchain_tool._verify_dependency_closure(
                [],
                [root, provider],
                ["root-package"],
            ),
        )
        provider["Provides"] = "virtual-api (= 1.9)"
        with self.assertRaisesRegex(source_tool.SourceToolError, "unsatisfied"):
            toolchain_tool._verify_dependency_closure([], [root, provider], ["root-package"])

    def test_registry_redirect_strips_bearer_cross_origin(self) -> None:
        request = toolchain_tool.urllib.request.Request(
            "https://registry-1.docker.io/v2/library/ubuntu/blobs/sha256:test",
            headers={"Authorization": "Bearer secret", "Cookie": "session=secret"},
        )
        redirected = toolchain_tool.RegistryRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://production.cloudflare.docker.com/object?token=signed",
        )
        self.assertIsNotNone(redirected)
        assert redirected is not None
        headers = {name.lower(): value for name, value in redirected.header_items()}
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)

    def test_main_rejects_non_finite_timeout(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = toolchain_tool.main(["fetch", "--timeout", "nan"])
        self.assertEqual(source_tool.EXIT_SCHEMA, result)
        self.assertIn("finite", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
