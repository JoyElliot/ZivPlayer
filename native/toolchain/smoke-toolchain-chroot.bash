#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 077

if [[ "$#" -ne 1 || "$1" != "--chroot-smoke" || "${EUID}" -ne 0 || "$$" -ne 1 ]]; then
  echo "error: invalid chroot smoke invocation" >&2
  exit 4
fi

status_value() {
  /usr/bin/awk -v key="$1" '$1 == key { print $2 }' /proc/self/status
}

[[ "$(status_value CapInh:)" == 0000000000000000 ]]
[[ "$(status_value CapPrm:)" == 0000000000000000 ]]
[[ "$(status_value CapEff:)" == 0000000000000000 ]]
[[ "$(status_value CapBnd:)" == 0000000000000000 ]]
[[ "$(status_value CapAmb:)" == 0000000000000000 ]]
[[ "$(status_value NoNewPrivs:)" == 1 ]]
[[ "$(status_value Seccomp:)" == 2 ]]
[[ "$(status_value Seccomp_filters:)" =~ ^[0-9]+$ ]]
(( $(status_value Seccomp_filters:) >= 1 ))

for descriptor_path in /proc/self/fd/*; do
  descriptor="${descriptor_path##*/}"
  if [[ "${descriptor}" =~ ^[0-9]+$ ]] && (( descriptor > 2 )); then
    eval "exec ${descriptor}>&-"
  fi
done

if ( : > /opt/zivplayer/toolchain/.ziv-write-probe ) 2>/dev/null; then
  echo "error: SDK projection accepted a write" >&2
  exit 4
fi
[[ ! -e /opt/zivplayer/toolchain/.ziv-write-probe ]]
if ( : > /usr/.ziv-root-write-probe ) 2>/dev/null; then
  echo "error: APT root accepted a write" >&2
  exit 4
fi
[[ ! -e /usr/.ziv-root-write-probe ]]

/usr/bin/python3.12 -I -S -B -c '
import socket
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except PermissionError:
    pass
else:
    raise SystemExit(4)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.close()
'
if /usr/bin/unshare --mount /bin/true >/dev/null 2>&1; then
  echo "error: nested mount namespace creation was allowed" >&2
  exit 4
fi

python_version="$(/usr/bin/python3.12 --version 2>&1)"
[[ "${python_version}" == "Python 3.12.3" ]]
printf "python\t%s\n" "${python_version}"

meson_version="$(/opt/zivplayer/toolchain/bin/meson --version)"
[[ "${meson_version}" == 1.11.0 ]]
printf "meson\t%s\n" "${meson_version}"
/opt/zivplayer/toolchain/bin/meson setup --help >/dev/null

meson_import="$(/usr/bin/python3.12 -I -S -B -c '
import sys
sys.path.insert(0, "/opt/zivplayer/toolchain/python/site-packages")
import mesonbuild
print(mesonbuild.__file__)
')"
[[ "${meson_import}" == /opt/zivplayer/toolchain/python/site-packages/mesonbuild/__init__.py ]]
printf "meson_import\t%s\n" "${meson_import}"
[[ -z "$(/usr/bin/find /opt/zivplayer/toolchain -name '*.pyc' -print -quit)" ]]

ninja_version="$(/usr/bin/ninja --version)"
[[ "${ninja_version}" == 1.11.1 ]]
printf "ninja\t%s\n" "${ninja_version}"

pkg_config_version="$(/usr/bin/pkg-config --version)"
[[ "${pkg_config_version}" == 1.8.1 ]]
printf "pkg_config\t%s\n" "${pkg_config_version}"

java_version="$(/usr/bin/java -version 2>&1 | /usr/bin/sed -n '1p')"
[[ "${java_version}" =~ ^openjdk\ version\ \"17\.0\.[0-9]+\" ]]
printf "java\t%s\n" "${java_version}"

javac_version="$(/usr/bin/javac -version 2>&1)"
[[ "${javac_version}" =~ ^javac\ 17\.0\.[0-9]+$ ]]
printf "javac\t%s\n" "${javac_version}"

aapt2_version="$(/opt/zivplayer/toolchain/android-sdk/build-tools/36.0.0/aapt2 version 2>&1)"
[[ "${aapt2_version}" == Android\ Asset\ Packaging\ Tool* ]]
printf "aapt2\t%s\n" "${aapt2_version}"

/usr/bin/grep -qx "Pkg.Revision=36.0.0" \
  /opt/zivplayer/toolchain/android-sdk/build-tools/36.0.0/source.properties
/usr/bin/grep -qx "Pkg.Revision=2" \
  /opt/zivplayer/toolchain/android-sdk/platforms/android-36/source.properties
/usr/bin/grep -qx "AndroidVersion.ApiLevel=36" \
  /opt/zivplayer/toolchain/android-sdk/platforms/android-36/source.properties
/usr/bin/grep -qx "Pkg.Revision = 29.0.14206865" \
  /opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/source.properties
printf "android_packages\tbuild-tools=36.0.0;platform=36-r02;ndk=29.0.14206865\n"

arm64_clang=/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang
x86_clang=/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android26-clang
arm64_version="$(${arm64_clang} --version | /usr/bin/sed -n '1p')"
x86_version="$(${x86_clang} --version | /usr/bin/sed -n '1p')"
[[ "${arm64_version}" == *"clang version 21.0.0"* ]]
[[ "${x86_version}" == *"clang version 21.0.0"* ]]
printf "clang_arm64\t%s\n" "${arm64_version}"
printf "clang_x86_64\t%s\n" "${x86_version}"

printf "%s\n" "int ziv_smoke(void) { return 26; }" > /tmp/ziv-smoke.c
"${arm64_clang}" -fPIC -shared -Wl,-z,max-page-size=16384 \
  /tmp/ziv-smoke.c -o /tmp/ziv-smoke-arm64.so
"${x86_clang}" -fPIC -shared -Wl,-z,max-page-size=16384 \
  /tmp/ziv-smoke.c -o /tmp/ziv-smoke-x86_64.so

arm64_machine="$(/usr/bin/readelf -hW /tmp/ziv-smoke-arm64.so \
  | /usr/bin/awk -F: '/^[[:space:]]*Machine:/ { gsub(/^[[:space:]]+/, "", $2); print $2 }')"
x86_machine="$(/usr/bin/readelf -hW /tmp/ziv-smoke-x86_64.so \
  | /usr/bin/awk -F: '/^[[:space:]]*Machine:/ { gsub(/^[[:space:]]+/, "", $2); print $2 }')"
[[ "${arm64_machine}" == AArch64 ]]
[[ "${x86_machine}" == "Advanced Micro Devices X86-64" ]]

for binary in /tmp/ziv-smoke-arm64.so /tmp/ziv-smoke-x86_64.so; do
  /usr/bin/readelf -lW "${binary}" | /usr/bin/awk '
    $1 == "LOAD" { count++; if ($NF != "0x4000") bad++ }
    END { if (count == 0 || bad != 0) exit 4 }
  '
done
printf "elf_arm64\t%s;load-align=0x4000\n" "${arm64_machine}"
printf "elf_x86_64\t%s;load-align=0x4000\n" "${x86_machine}"

/usr/bin/rm -f \
  /tmp/ziv-smoke.c \
  /tmp/ziv-smoke-arm64.so \
  /tmp/ziv-smoke-x86_64.so
[[ -z "$(/usr/bin/find /opt/zivplayer/toolchain -name '*.pyc' -print -quit)" ]]
printf "isolation\tprivate-namespaces;loopback-only;seccomp;no-caps;read-only-inputs\n"
