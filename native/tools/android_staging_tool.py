#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy/verify inspection artifacts for local Android builds.

The checked-in lock pins an accepted execution receipt, not a fresh self-reported
manifest. Hash-identical libraries retain that receipt's ELF/API/JNI audit; this
tool does not claim to repeat the Linux symbol audit or prove ART registration.
It never changes the execution workspace or promotes an input to release-ready.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "native"
DEFAULT_STAGE = NATIVE / "out/android"
DEFAULT_LOCK = NATIVE / "android-staging-lock.json"
RECEIPT_NAME = "ziv-native-wrapper-build-receipt.json"


class StagingError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StagingError(message)


def plain(path: Path, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ), f"links/reparse points are not accepted: {path}")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            f"unexpected file type: {path}")
    if not directory:
        require(info.st_nlink == 1, f"hard links are not accepted: {path}")
    return info


def plain_ancestors(path: Path) -> None:
    for parent in [path, *path.parents]:
        plain(parent, directory=True)


def digest(path: Path) -> tuple[str, int]:
    before = plain(path)
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino),
                f"file changed while opening: {path}")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
        after = os.fstat(stream.fileno())
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
            and (after.st_dev, after.st_ino) == (path.stat().st_dev, path.stat().st_ino),
            f"file changed while hashing: {path}")
    return sha.hexdigest(), before.st_size


def read_json(path: Path, maximum: int = 4 * 1024 * 1024) -> tuple[dict, bytes]:
    require(plain(path).st_size <= maximum, f"oversized JSON: {path}")
    raw = path.read_bytes()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON field: {key}")
            result[key] = value
        return result

    data = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(data, dict), f"expected JSON object: {path}")
    return data, raw


def configuration() -> tuple[dict, dict, str, str]:
    policy_raw = (NATIVE / "native-wrapper-build-executor-policy.toml").read_bytes()
    profile_raw = (NATIVE / "native-wrapper-build-profile.toml").read_bytes()
    return (tomllib.loads(policy_raw.decode()), tomllib.loads(profile_raw.decode()),
            hashlib.sha256(policy_raw).hexdigest(), hashlib.sha256(profile_raw).hexdigest())


def check_receipt(receipt: dict, raw: bytes, lock: dict) -> list[dict]:
    policy, profile, policy_sha, profile_sha = configuration()
    require(set(lock) == {"kind", "schemaVersion", "receiptSha256", "receiptSizeBytes",
                          "policySha256", "profileSha256", "ready", "releaseInput"},
            "staging lock fields differ")
    require(lock["kind"] == "ziv-android-inspection-staging-v1" and lock["schemaVersion"] == 1
            and lock["ready"] is False and lock["releaseInput"] is False,
            "staging lock must remain inspection-only")
    require(hashlib.sha256(raw).hexdigest() == lock["receiptSha256"]
            and len(raw) == lock["receiptSizeBytes"], "receipt differs from accepted lock")
    require(lock["policySha256"] == receipt["policySha256"] == policy_sha
            and lock["profileSha256"] == receipt["profileSha256"] == profile_sha,
            "policy/profile differs from accepted receipt")
    for key in ("kind", "schemaVersion", "buildExecuted", "artifactStaged", "artifactAudited",
                "ready", "releaseInput", "pendingReleaseBlockers"):
        require(receipt[key] == policy["receipt"][key], f"receipt status differs: {key}")
    require(receipt["profileName"] == profile["project"]["profile"], "profile name differs")
    command = receipt["commandEvidence"]
    require(command["runnerExitCode"] == 0 and command["commands"] == profile["build"]["commands"]
            and command["parentEvents"] == [
                {"event": "runner-launched", "returnCode": None, "sequence": 1},
                {"event": "runner-exited", "returnCode": 0, "sequence": 2}],
            "receipt lacks successful execution evidence")
    cgroup = receipt["resourceOutcome"]["cgroup"]
    require(cgroup["emptyAfterExit"] is True and cgroup["removed"] is True
            and cgroup["violations"] == [], "receipt has an unclean resource outcome")
    contract = receipt["wrapperContract"]
    for key, value in contract.items():
        if key != "sourceInputs":
            require(value == policy["contract"][key], f"JNI contract differs: {key}")
    expected_sources = {record["source"]: record for record in profile["wrapperInput"]}
    require({record["source"] for record in contract["sourceInputs"]} == set(expected_sources),
            "JNI source input set differs")
    for record in contract["sourceInputs"]:
        expected = expected_sources[record["source"]]
        require((record["sha256"], record["sizeBytes"]) == (expected["sha256"], expected["size"])
                == digest(ROOT / record["source"]), f"JNI source changed: {record['source']}")
    libraries = profile["build"]["expectedLibraries"]
    abis = {abi["name"]: abi for abi in profile["abi"]}
    expected_order = [(abi, lib) for abi in abis for lib in libraries]
    artifacts = receipt["artifacts"]
    require([(item["abi"], item["library"]) for item in artifacts] == expected_order,
            "receipt must contain exactly the two ABI / twenty library inventory")
    for item in artifacts:
        abi, library, audit = item["abi"], item["library"], item["audit"]
        require(item["outputRelativePath"] == f"output/{abi}/{library}", "artifact path differs")
        require(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None
                and type(item["sizeBytes"]) is int and item["sizeBytes"] > 0, "invalid artifact identity")
        require(audit["soname"] == library and audit["elfClass"] == 64
                and audit["elfMachine"] == abis[abi]["elfMachine"], "ELF identity differs")
        require(audit["loadSegments"] and all(
            segment["alignmentBytes"] == 16384
            and segment["offsetBytes"] % 16384 == segment["virtualAddress"] % 16384
            for segment in audit["loadSegments"]), "ELF page alignment differs")
        require(set(audit["needed"]) <= set(libraries) | set(policy["audit"]["platformSonameAllowlist"]),
                "ELF dependency closure differs")
    return artifacts


def exact_tree(root: Path, expected_files: set[str], expected_dirs: set[str]) -> None:
    plain_ancestors(root)
    files, dirs = set(), set()
    for parent, names, filenames in os.walk(root, followlinks=False):
        for name in names:
            child = Path(parent) / name
            plain(child, directory=True)
            dirs.add(child.relative_to(root).as_posix())
        for name in filenames:
            child = Path(parent) / name
            plain(child)
            files.add(child.relative_to(root).as_posix())
    require(files == expected_files and dirs == expected_dirs, f"missing/extra staging entries: {root}")


def verify(stage: Path, lock: dict) -> None:
    receipt, raw = read_json(stage / RECEIPT_NAME)
    artifacts = check_receipt(receipt, raw, lock)
    expected_files = {RECEIPT_NAME}
    expected_dirs = {"jniLibs"}
    for item in artifacts:
        relative = f"jniLibs/{item['abi']}/{item['library']}"
        expected_files.add(relative)
        expected_dirs.add(f"jniLibs/{item['abi']}")
        require(digest(stage / relative) == (item["sha256"], item["sizeBytes"]),
                f"staged library differs: {relative}")
    exact_tree(stage, expected_files, expected_dirs)


def export(workspace: Path, stage: Path, lock_path: Path, accepted_sha: str) -> None:
    plain_ancestors(workspace)
    receipt, raw = read_json(workspace / RECEIPT_NAME)
    require(re.fullmatch(r"[0-9a-f]{64}", accepted_sha) is not None
            and hashlib.sha256(raw).hexdigest() == accepted_sha, "accepted execution receipt SHA differs")
    lock = {"kind": "ziv-android-inspection-staging-v1", "schemaVersion": 1,
            "receiptSha256": accepted_sha, "receiptSizeBytes": len(raw),
            "policySha256": receipt["policySha256"], "profileSha256": receipt["profileSha256"],
            "ready": False, "releaseInput": False}
    artifacts = check_receipt(receipt, raw, lock)
    exact_tree(workspace / "output", {f"{a['abi']}/{a['library']}" for a in artifacts},
               {a["abi"] for a in artifacts})
    require(not stage.exists() and not lock_path.exists(), "export never replaces an existing stage/lock")
    stage.parent.mkdir(parents=True, exist_ok=True)
    plain_ancestors(stage.parent)
    stage.mkdir()
    for item in artifacts:
        source = workspace / item["outputRelativePath"]
        require(digest(source) == (item["sha256"], item["sizeBytes"]), f"build output differs: {source}")
        target = stage / "jniLibs" / item["abi"] / item["library"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
    (stage / RECEIPT_NAME).write_bytes(raw)
    verify(stage, lock)
    with lock_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(lock, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "export"))
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--build-workspace", type=Path)
    parser.add_argument("--receipt-sha256")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify(args.staging.absolute(), read_json(args.lock)[0])
        else:
            require(args.build_workspace is not None and args.receipt_sha256 is not None,
                    "export requires --build-workspace and --receipt-sha256")
            export(args.build_workspace.absolute(), args.staging.absolute(), args.lock.absolute(),
                   args.receipt_sha256)
        print(f"verified Android inspection staging: {args.staging} (20 libraries; releaseInput=false)")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
