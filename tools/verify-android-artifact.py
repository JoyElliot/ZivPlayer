#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify APK native bytes, 16-KiB ZIP alignment, and the actual DEX JNI descriptors."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tomllib
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "native/tools"))
import android_staging_tool as staging

require = staging.require


class Dex:
    """Only the standard DEX tables needed to check native method registration."""

    def __init__(self, raw: bytes):
        self.raw = raw
        require(raw[:4] == b"dex\n" and raw[7] == 0 and raw[4:7] in (b"035", b"037", b"038", b"039", b"040"),
                "unsupported DEX version")
        require(self.u32(32) == len(raw) and self.u32(40) == 0x12345678, "DEX header differs")
        self.string_count, self.string_offset = self.u32(56), self.u32(60)
        self.type_count, self.type_offset = self.u32(64), self.u32(68)
        self.proto_count, self.proto_offset = self.u32(72), self.u32(76)
        self.method_count, self.method_offset = self.u32(88), self.u32(92)
        self.class_count, self.class_offset = self.u32(96), self.u32(100)

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.raw, offset)[0]

    def uleb(self, offset: int) -> tuple[int, int]:
        value = 0
        for index in range(5):
            byte = self.raw[offset]
            offset += 1
            value |= (byte & 0x7f) << (index * 7)
            if byte < 0x80:
                return value, offset
        raise ValueError("oversized DEX uleb128")

    def string(self, index: int) -> str:
        require(0 <= index < self.string_count, "invalid DEX string index")
        _, offset = self.uleb(self.u32(self.string_offset + index * 4))
        end = self.raw.index(b"\0", offset)
        # JNI names and descriptors are ASCII; unrelated MUTF-8 literals aren't inspected.
        return self.raw[offset:end].decode("utf-8", errors="replace")

    def type(self, index: int) -> str:
        require(0 <= index < self.type_count, "invalid DEX type index")
        return self.string(self.u32(self.type_offset + index * 4))

    def descriptor(self, index: int) -> str:
        require(0 <= index < self.proto_count, "invalid DEX proto index")
        offset = self.proto_offset + index * 12
        returns, parameters = self.type(self.u32(offset + 4)), self.u32(offset + 8)
        arguments = ""
        if parameters:
            count = self.u32(parameters)
            require(count < 65536, "oversized DEX parameter list")
            arguments = "".join(self.type(struct.unpack_from("<H", self.raw, parameters + 4 + i * 2)[0])
                                for i in range(count))
        return f"({arguments}){returns}"

    def native_methods(self, class_data: int, class_type: int) -> dict[str, str]:
        if class_data == 0:
            return {}
        cursor, sizes = class_data, []
        for _ in range(4):
            size, cursor = self.uleb(cursor)
            sizes.append(size)
        for _ in range(sizes[0] + sizes[1]):
            _, cursor = self.uleb(cursor)
            _, cursor = self.uleb(cursor)
        natives = {}
        for count in sizes[2:]:
            method_index = 0
            for _ in range(count):
                delta, cursor = self.uleb(cursor)
                flags, cursor = self.uleb(cursor)
                code_offset, cursor = self.uleb(cursor)
                method_index += delta
                require(method_index < self.method_count, "invalid DEX method index")
                if flags & 0x100:
                    offset = self.method_offset + method_index * 8
                    require(struct.unpack_from("<H", self.raw, offset)[0] == class_type,
                            "native DEX method has a different declaring class")
                    require(code_offset == 0, "native DEX method has a code item")
                    proto = struct.unpack_from("<H", self.raw, offset + 2)[0]
                    name = self.string(self.u32(offset + 4))
                    require(name not in natives, "overloaded/repeated JNI method name")
                    natives[name] = self.descriptor(proto)
        return natives

    def find_binding(self, descriptor: str) -> list[dict[str, str]]:
        found = []
        for index in range(self.class_count):
            offset = self.class_offset + index * 32
            name = self.type(self.u32(offset))
            require(not name.startswith("Ldev/jdtech/mpv/") and not name.endswith("/BootstrapMpvClient;"),
                    f"bootstrap class still packaged: {name}")
            if name == descriptor:
                found.append(self.native_methods(self.u32(offset + 24), self.u32(offset)))
        return found


def platform_libraries(abis: set[str]) -> dict[str, dict]:
    """Every AndroidX native helper is bound to its exact locked and verified AAR."""
    helpers = (
        ("androidx.graphics", "graphics-path", "1.0.1", "graphics-path-1.0.1.aar", "libandroidx.graphics.path.so"),
        ("androidx.datastore", "datastore-core-android", "1.2.1", "datastore-core.aar", "libdatastore_shared_counter.so"),
    )
    dependency_lock = (ROOT / "apps/android/gradle.lockfile").read_text(encoding="utf-8")
    metadata = ET.parse(ROOT / "gradle/verification-metadata.xml").getroot()
    for node in metadata.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    cache = Path(os.environ.get("GRADLE_USER_HOME", str(Path.home() / ".gradle")))
    result = {}
    for group, artifact, version, aar_name, library in helpers:
        coordinate = f"{group}:{artifact}:{version}"
        require(any(line.startswith(coordinate + "=") for line in dependency_lock.splitlines()),
                f"AndroidX helper is not in the application dependency lock: {coordinate}")
        checksums = [node.attrib["value"] for component in metadata.iter("component")
                     if component.attrib == {"group": group, "name": artifact, "version": version}
                     for entry in component.findall("artifact") if entry.get("name") == aar_name
                     for node in entry.findall("sha256")]
        require(len(checksums) == 1, f"AndroidX helper needs one accepted AAR checksum: {coordinate}")
        candidates = list((cache / "caches/modules-2/files-2.1" / group / artifact / version).glob(f"*/{aar_name}"))
        accepted = [path for path in candidates if staging.digest(path)[0] == checksums[0]]
        require(bool(accepted), f"Verified AndroidX helper AAR is missing: {coordinate}")
        with zipfile.ZipFile(accepted[0]) as archive:
            for abi in abis:
                name = f"lib/{abi}/{library}"
                raw = archive.read(f"jni/{abi}/{library}")
                require(raw[:6] == b"\x7fELF\x02\x01", f"AndroidX helper is not little-endian ELF64: {name}")
                require(struct.unpack_from("<H", raw, 18)[0] == {"arm64-v8a": 183, "x86_64": 62}[abi],
                        f"AndroidX helper machine differs: {name}")
                phoff = struct.unpack_from("<Q", raw, 32)[0]
                entry_size, count = struct.unpack_from("<HH", raw, 54)
                require(entry_size == 56 and 0 < count < 256, f"Invalid helper program headers: {name}")
                loads = [phoff + index * entry_size for index in range(count)
                         if struct.unpack_from("<I", raw, phoff + index * entry_size)[0] == 1]
                require(bool(loads) and all(struct.unpack_from("<Q", raw, offset + 48)[0] >= 16384 for offset in loads),
                        f"AndroidX helper ELF LOAD alignment is below 16 KiB: {name}")
                result[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "sizeBytes": len(raw),
                                "source": coordinate, "aarSha256": checksums[0]}
    return result


def verify(apk: Path) -> dict:
    lock = staging.read_json(staging.DEFAULT_LOCK)[0]
    staging.verify(staging.DEFAULT_STAGE, lock)
    receipt = staging.read_json(staging.DEFAULT_STAGE / staging.RECEIPT_NAME)[0]
    expected = {f"lib/{item['abi']}/{item['library']}": item for item in receipt["artifacts"]}
    platform = platform_libraries({item["abi"] for item in receipt["artifacts"]})
    expected.update(platform)
    contract = tomllib.loads((ROOT / "native/wrapper/jni-contract.toml").read_text(encoding="utf-8"))
    expected_methods = {item["name"]: item["descriptor"] for item in contract["methods"]}
    require(contract["schemaVersion"] == 1 and contract["kind"] == "zivplayer-libmpv-jni-contract" and
            contract["methodCount"] == 16 and
            len(contract["methods"]) == len(expected_methods) == 16,
            "JNI contract schema, cardinality or method uniqueness differs")
    native_records, bindings = [], []
    with zipfile.ZipFile(apk) as archive, apk.open("rb") as raw_apk:
        names = archive.namelist()
        require(len(names) == len(set(names)), "APK has duplicate ZIP entry names")
        require({name for name in names if name.endswith(".so")} == set(expected),
                "APK native inventory differs from the twenty audited libraries and four locked AndroidX helpers")
        for name, item in expected.items():
            info = archive.getinfo(name)
            require(info.compress_type == zipfile.ZIP_STORED, f"native library is compressed: {name}")
            raw_apk.seek(info.header_offset)
            header = raw_apk.read(30)
            require(header[:4] == b"PK\x03\x04", f"invalid ZIP local header: {name}")
            name_bytes, extra_bytes = struct.unpack_from("<HH", header, 26)
            data_offset = info.header_offset + 30 + name_bytes + extra_bytes
            require(data_offset % 16384 == 0, f"native ZIP data isn't 16-KiB aligned: {name}")
            sha = hashlib.sha256()
            with archive.open(info) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(chunk)
            require((sha.hexdigest(), info.file_size) == (item["sha256"], item["sizeBytes"]),
                    f"packaged native bytes differ from execution audit: {name}")
            native_records.append({"path": name, "sha256": sha.hexdigest(),
                                   "sizeBytes": info.file_size, "dataOffset": data_offset,
                                   "source": platform[name]["source"] if name in platform else "native-execution-receipt"})
        for name in names:
            if re.fullmatch(r"classes(?:[0-9]+)?\.dex", name):
                bindings.extend(Dex(archive.read(name)).find_binding(f"L{contract['bindingsClass']};"))
    require(bindings == [expected_methods], "APK DEX JNI class or sixteen native method descriptors differ")
    apk_sha, apk_size = staging.digest(apk)
    return {"schemaVersion": 1, "apk": apk.name, "apkSha256": apk_sha, "apkSizeBytes": apk_size,
            "nativeReceiptSha256": lock["receiptSha256"], "artifacts": native_records,
            "bindingsClass": contract["bindingsClass"], "dexNativeMethods": expected_methods,
            "runtimeRegistrationEvidence": "pending-device-art-smoke", "releaseInput": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.apk)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"verified APK: {report['apk']}; 20 audited libraries + 4 locked AndroidX helpers, 16-KiB ZIP alignment, 16 DEX JNI methods")
        print(f"SHA-256: {report['apkSha256']}")
        return 0
    except (OSError, ValueError, KeyError, IndexError, struct.error, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
