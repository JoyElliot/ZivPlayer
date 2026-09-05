# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import android_staging_tool as staging


class AndroidStagingToolTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stage = self.root / "stage"
        self.library = self.stage / "jniLibs/arm64-v8a/libexample.so"
        self.library.parent.mkdir(parents=True)
        self.library.write_bytes(b"audited library bytes")
        (self.stage / staging.RECEIPT_NAME).write_text("{}", encoding="utf-8")
        self.artifact = {"abi": "arm64-v8a", "library": "libexample.so",
                         "sha256": hashlib.sha256(self.library.read_bytes()).hexdigest(),
                         "sizeBytes": self.library.stat().st_size}

    def verify_file_tree(self):
        # Receipt semantics are independently checked against the accepted execution
        # receipt in the integration gate; these tests isolate filesystem tampering.
        with patch.object(staging, "check_receipt", return_value=[self.artifact]):
            staging.verify(self.stage, {})

    def test_verified_tree_and_same_size_byte_tampering(self):
        self.verify_file_tree()
        self.library.write_bytes(b"Audited library bytes")
        with self.assertRaisesRegex(staging.StagingError, "staged library differs"):
            self.verify_file_tree()

    def test_missing_library_is_rejected(self):
        self.library.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify_file_tree()

    def test_extra_library_or_directory_is_rejected(self):
        extra = self.library.parent / "libunexpected.so"
        extra.write_bytes(b"unexpected")
        with self.assertRaisesRegex(staging.StagingError, "missing/extra"):
            self.verify_file_tree()
        extra.unlink()
        (self.stage / "unused").mkdir()
        with self.assertRaisesRegex(staging.StagingError, "missing/extra"):
            self.verify_file_tree()

    def test_hard_link_cannot_masquerade_as_an_independent_copy(self):
        os.link(self.library, self.root / "alias.so")
        with self.assertRaisesRegex(staging.StagingError, "hard links"):
            self.verify_file_tree()

    def test_changed_receipt_is_rejected_before_its_claims_are_used(self):
        lock = {"kind": "ziv-android-inspection-staging-v1", "schemaVersion": 1,
                "ready": False, "releaseInput": False, "policySha256": "0" * 64,
                "profileSha256": "0" * 64, "receiptSha256": "0" * 64, "receiptSizeBytes": 2}
        with self.assertRaisesRegex(staging.StagingError, "receipt differs from accepted lock"):
            staging.check_receipt({}, b"{}", lock)

    def test_duplicate_json_fields_are_rejected(self):
        receipt = self.stage / staging.RECEIPT_NAME
        receipt.write_text('{"ready": false, "ready": true}', encoding="utf-8")
        with self.assertRaisesRegex(staging.StagingError, "duplicate JSON field"):
            staging.read_json(receipt)

    def test_export_requires_explicit_matching_receipt_sha_before_creating_destination(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        (workspace / staging.RECEIPT_NAME).write_text(json.dumps({"artifacts": []}), encoding="utf-8")
        destination = self.root / "new-stage"
        with self.assertRaisesRegex(staging.StagingError, "accepted execution receipt SHA differs"):
            staging.export(workspace, destination, self.root / "lock.json", "0" * 64)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
