# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent pinned VLC AAR and source-built seek bridge provenance for the experiment."""
import hashlib
import json
import os
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def elf_metadata(raw, abi, name):
    require(raw[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", raw, 16)[0] == 3 and
            struct.unpack_from("<H", raw, 18)[0] == {"arm64-v8a": 183, "x86_64": 62}[abi],
            f"VLC ELF type/machine differs: {name}")
    phoff = struct.unpack_from("<Q", raw, 32)[0]
    entry_size, count = struct.unpack_from("<HH", raw, 54)
    require(entry_size == 56 and 0 < count < 256, "invalid VLC program headers")
    headers = [struct.unpack_from("<IIQQQQQQ", raw, phoff + i * entry_size) for i in range(count)]
    loads = [h for h in headers if h[0] == 1]
    require(loads and all(h[7] >= 16384 and h[2] % h[7] == h[3] % h[7] for h in loads),
            f"VLC ELF LOAD alignment differs: {name}")
    dynamic = [h for h in headers if h[0] == 2]
    require(len(dynamic) == 1, "VLC requires one dynamic table")
    tags = []
    for offset in range(dynamic[0][2], dynamic[0][2] + dynamic[0][5], 16):
        tag, value = struct.unpack_from("<qQ", raw, offset)
        if tag == 0:
            break
        tags.append((tag, value))
    strings = [value for tag, value in tags if tag == 5]
    require(len(strings) == 1, "VLC dynamic strings differ")
    backed = [h for h in loads if h[3] <= strings[0] < h[3] + h[5]]
    require(len(backed) == 1, "VLC strings must be file-backed")
    base = backed[0][2] + strings[0] - backed[0][3]
    def string(value):
        start = base + value
        return raw[start:raw.index(b"\0", start)].decode("ascii")
    sonames = [string(v) for tag, v in tags if tag == 14]
    require(sonames == [name], f"VLC SONAME differs: {name}")
    return {"soname": name, "needed": [string(v) for tag, v in tags if tag == 1]}


def extensions(root, abis, receipt, apk):
    lock = json.loads((root / "tools/libvlc4-experiment-lock.json").read_text(encoding="utf-8"))
    require(lock["schemaVersion"] == 1 and lock["coordinate"] == "org.videolan.android:libvlc-all:4.0.0-eap29",
            "unsupported VLC experiment lock")
    require(set(lock["packagedAbis"]) == abis and set(lock["libraries"]) ==
            {f"{abi}/{name}" for abi in abis for name in ("libvlc.so", "libvlcjni.so", "libc++_shared.so")},
            "VLC AAR inventory differs")
    require(len(lock["bindings"]) == 14 and sum(map(len, lock["bindings"].values())) == 119,
            "VLC requires 14 JNI classes and 119 native methods")
    require(any(line.startswith(lock["coordinate"] + "=") for line in
                (root / "apps/android/gradle.lockfile").read_text(encoding="utf-8").splitlines()),
            "VLC application dependency is not locked")
    metadata = ET.parse(root / "gradle/verification-metadata.xml").getroot()
    for node in metadata.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    group, artifact, version = lock["coordinate"].split(":")
    aar_name = f"{artifact}-{version}.aar"
    sums = [node.get("value") for c in metadata.iter("component")
            if c.attrib == {"group": group, "name": artifact, "version": version}
            for a in c.findall("artifact") if a.get("name") == aar_name for node in a.findall("sha256")]
    require(sums == [lock["aarSha256"]], "VLC AAR verification checksum differs")
    cache = Path(os.environ.get("GRADLE_USER_HOME", str(Path.home() / ".gradle"))) / "caches/modules-2/files-2.1"
    accepted = [p for p in (cache / group / artifact / version).glob(f"*/{aar_name}")
                if hashlib.sha256(p.read_bytes()).hexdigest() == lock["aarSha256"]]
    require(accepted, "verified VLC AAR missing")
    result = {}
    with zipfile.ZipFile(accepted[0]) as archive:
        require(hashlib.sha256(archive.read("classes.jar")).hexdigest() == lock["classesJarSha256"],
                "VLC classes.jar differs")
        for relative, expected in lock["libraries"].items():
            raw = archive.read("jni/" + relative)
            abi, name = relative.split("/")
            require((hashlib.sha256(raw).hexdigest(), len(raw)) == (expected["sha256"], expected["sizeBytes"]),
                    f"VLC native bytes differ: {relative}")
            require(elf_metadata(raw, abi, name) == {k: expected[k] for k in ("soname", "needed")},
                    f"VLC dynamic dependencies differ: {relative}")
            path = "lib/" + relative
            if name == "libc++_shared.so":
                require(path in receipt and all(receipt[path][k] == expected[k] for k in ("sha256", "sizeBytes")),
                        f"VLC duplicate C++ runtime differs from the original receipt: {relative}")
            else:
                result[path] = dict(expected, source=lock["coordinate"], aarSha256=lock["aarSha256"])
    bridge = lock["bridge"]
    source_names = {"native/libvlc4-seek/CMakeLists.txt", "native/libvlc4-seek/vlc4_seek.c",
                    "platform/libmpv-android/src/main/kotlin/io/github/joyelliot/zivplayer/platform/libvlc/VlcSeekBridge.kt"}
    require(set(bridge["sources"]) == source_names, "VLC bridge source inventory differs")
    for source, sha in bridge["sources"].items():
        require(hashlib.sha256((root / source).read_bytes()).hexdigest() == sha, f"VLC bridge source changed: {source}")
    require(set(bridge["libraries"]) == {f"{abi}/libzivplayer_vlc4.so" for abi in abis}, "VLC bridge ABI inventory differs")
    with zipfile.ZipFile(apk) as archive:
        for relative, expected in bridge["libraries"].items():
            raw = archive.read("lib/" + relative)
            abi, name = relative.split("/")
            require((hashlib.sha256(raw).hexdigest(), len(raw)) == (expected["sha256"], expected["sizeBytes"]),
                    f"VLC bridge bytes differ: {relative}")
            require(elf_metadata(raw, abi, name) == {k: expected[k] for k in ("soname", "needed")},
                    f"VLC bridge dynamic dependencies differ: {relative}")
            result["lib/" + relative] = dict(expected, source="native/libvlc4-seek (NDK " + bridge["ndk"] + ")")
    require(not set(lock["bindings"]) & set(bridge["bindings"]), "overlapping VLC JNI descriptors")
    return result, lock["bindings"] | bridge["bindings"]
