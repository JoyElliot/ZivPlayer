#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 077

die() {
  echo "error: $*" >&2
  exit 4
}

if [[ "$#" -ne 1 || "$1" != "--namespace-probe" || "${EUID}" -ne 0 || "$$" -ne 1 ]]; then
  die "invalid native wrapper probe invocation"
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

script_descriptor_count=0
for descriptor_path in /proc/self/fd/*; do
  descriptor="${descriptor_path##*/}"
  if [[ ! -e "${descriptor_path}" && ! -L "${descriptor_path}" ]]; then
    continue
  fi
  if [[ "${descriptor}" =~ ^[0-9]+$ ]] && (( descriptor > 2 )); then
    [[ "${descriptor_path}" -ef /run/ziv-native-build-wrapper-probe.bash ]] \
      || die "native wrapper probe inherited an unexpected descriptor: ${descriptor}"
    (( script_descriptor_count += 1 ))
  fi
done
(( script_descriptor_count <= 1 )) \
  || die "native wrapper probe has duplicate script descriptors"

[[ "${PATH}" == '/opt/zivplayer/toolchain/bin:/opt/zivplayer/toolchain/android-sdk/build-tools/36.0.0:/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin:/usr/sbin:/usr/bin:/sbin:/bin' ]]
[[ "${LC_ALL}" == C && "${LANG}" == C && "${LANGUAGE}" == C && "${TZ}" == UTC ]]
[[ "${SOURCE_DATE_EPOCH}" == 946684800 ]]
[[ "${PYTHONHASHSEED}" == 0 && "${PYTHONDONTWRITEBYTECODE}" == 1 ]]
[[ "${HOME}" == /build/home && "${TMPDIR}" == /build/tmp ]]
[[ "${XDG_CACHE_HOME}" == /build/home/.cache ]]
[[ "${XDG_CONFIG_HOME}" == /build/home/.config ]]
[[ "${XDG_DATA_HOME}" == /build/home/.local/share ]]
[[ "${HOSTNAME}" == zivplayer-builder ]]
[[ "${ANDROID_HOME}" == /opt/zivplayer/toolchain/android-sdk ]]
[[ "${ANDROID_SDK_ROOT}" == /opt/zivplayer/toolchain/android-sdk ]]
[[ "${ANDROID_NDK_ROOT}" == /opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865 ]]
[[ "${JAVA_HOME}" == /usr/lib/jvm/java-17-openjdk-amd64 ]]

environment_dump="$(/usr/bin/env)" || die "cannot inspect native wrapper environment"
remaining_environment="${environment_dump}"
while [[ -n "${remaining_environment}" ]]; do
  if [[ "${remaining_environment}" == *$'\n'* ]]; then
    environment_record="${remaining_environment%%$'\n'*}"
    remaining_environment="${remaining_environment#*$'\n'}"
  else
    environment_record="${remaining_environment}"
    remaining_environment=""
  fi
  [[ "${environment_record}" == *=* ]] \
    || die "native wrapper probe environment record is invalid"
  name="${environment_record%%=*}"
  case "${name}" in
    PATH|LC_ALL|LANG|LANGUAGE|TZ|SOURCE_DATE_EPOCH|PYTHONHASHSEED|PYTHONDONTWRITEBYTECODE|HOME|TMPDIR|XDG_CACHE_HOME|XDG_CONFIG_HOME|XDG_DATA_HOME|HOSTNAME|ANDROID_HOME|ANDROID_SDK_ROOT|ANDROID_NDK_ROOT|JAVA_HOME|PWD|SHLVL|_)
      ;;
    *)
      die "native wrapper probe inherited an unexpected environment variable: ${name}"
      ;;
  esac
done

if [[ -w /usr ]]; then
  die "native wrapper root is reported writable"
fi
if [[ -w /opt/zivplayer/toolchain ]]; then
  die "native wrapper SDK is reported writable"
fi
if [[ -w /build/wrapper ]]; then
  die "native wrapper input is reported writable"
fi

[[ -x /build/source/buildscripts/buildall.sh ]]
[[ -x /build/source/buildscripts/include/path.sh ]]
[[ -z "$(/usr/bin/find /build/output -mindepth 1 -print -quit)" ]]
[[ -z "$(/usr/bin/find /build/home -mindepth 1 -print -quit)" ]]
[[ -z "$(/usr/bin/find /build/tmp -mindepth 1 -print -quit)" ]]

expected_wrapper_entries=$'Android.mk\nApplication.mk\nCMakeLists.txt\nMpvNativeBindings.kt\njni-contract.toml\nzivplayer_mpv.cpp'
actual_wrapper_entries="$(/usr/bin/find /build/wrapper -mindepth 1 -maxdepth 1 -printf '%P\n' | /usr/bin/sort)"
[[ "${actual_wrapper_entries}" == "${expected_wrapper_entries}" ]] \
  || die "native wrapper input entry set is not exact"
[[ "$(( $(/usr/bin/find /build/wrapper -mindepth 2 -printf . | /usr/bin/wc -c) ))" -eq 0 ]] \
  || die "native wrapper input contains nested entries"
[[ "$(/usr/bin/stat -c '%a %u %g' -- /build/wrapper)" == '555 0 0' ]] \
  || die "native wrapper input root metadata differs"

require_wrapper_file() {
  local name="$1"
  local expected_size="$2"
  local expected_sha256="$3"
  local path="/build/wrapper/${name}"
  [[ -f "${path}" && ! -L "${path}" ]] \
    || die "native wrapper input is not a regular file: ${name}"
  [[ "$(/usr/bin/stat -c '%a %u %g %h %s' -- "${path}")" \
      == "444 0 0 1 ${expected_size}" ]] \
    || die "native wrapper input metadata differs: ${name}"
  [[ "$(/usr/bin/sha256sum -- "${path}")" == "${expected_sha256}  ${path}" ]] \
    || die "native wrapper input digest differs: ${name}"
}

require_wrapper_file Android.mk 1360 0dbe6408ea5fa0e4ad21d2ef2efda5ce718f7bcc82a211b0e8d9721ab750f0f8
require_wrapper_file Application.mk 361 4c7f0bda74ef74b385c877f28508318fe6af060f4cf8a4bbeb561adc8e1800d2
require_wrapper_file CMakeLists.txt 4050 1cf728263640a19959a5a5c2963317add3ece57e0eb5ce06b5e58ebfc55c1c38
require_wrapper_file MpvNativeBindings.kt 8874 cf46bcba19529af8d561d8f36b820937bf6c0002fd33a5b7060ec24a8de9dcbb
require_wrapper_file jni-contract.toml 3970 e12677fcdda11a5c64dfea5e23e8474b407b8163f23fc5ea5d19faf17def9389
require_wrapper_file zivplayer_mpv.cpp 40906 50d1d90247ec659c12bac74178d3971c2941697d4b6f398fd29cc4ca57a04179

/usr/bin/python3.12 -I -S -B -c '
import os
from pathlib import Path

root = Path("/build/wrapper")
if os.listxattr(root):
    raise SystemExit(4)
for entry in root.iterdir():
    if os.listxattr(entry):
        raise SystemExit(4)
'

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
  die "nested mount namespace creation was allowed"
fi

printf 'namespace\tprivate:mnt,net,pid,uts,ipc\n'
printf 'mounts\toverlay-root-ro;apt-lower-ro;sdk-ro;source-rw;output-rw;home-rw;tmp-rw;wrapper-ro\n'
printf 'environment\tempty-inheritance;fixed-build-variables\n'
printf 'network\tloopback-device-only;no-external-routes;af-inet-denied\n'
printf 'sandbox\tno-caps;no-new-privs;seccomp;no-host-fds\n'
printf 'workspace\tmounted-unmodified;build-not-executed\n'
printf 'wrapper\tread-only-input;root-0555;files-0444;exact-six\n'
