#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 077

die() {
  echo "error: $*" >&2
  exit 4
}

if [[ "$#" -ne 1 \
  || "$1" != "--execute-locked-wrapper-build" \
  || "${EUID}" -ne 0 \
  || "$$" -ne 1 ]]; then
  die "invalid native wrapper build runner invocation"
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
    [[ "${descriptor_path}" -ef /run/ziv-native-wrapper-build-runner.bash ]] \
      || die "native wrapper build runner inherited an unexpected descriptor: ${descriptor}"
    (( script_descriptor_count += 1 ))
  fi
done
(( script_descriptor_count <= 1 )) \
  || die "native wrapper build runner has duplicate script descriptors"

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

environment_dump="$(/usr/bin/env)" || die "cannot inspect native wrapper build environment"
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
    || die "native wrapper build environment record is invalid"
  name="${environment_record%%=*}"
  case "${name}" in
    PATH|LC_ALL|LANG|LANGUAGE|TZ|SOURCE_DATE_EPOCH|PYTHONHASHSEED|PYTHONDONTWRITEBYTECODE|HOME|TMPDIR|XDG_CACHE_HOME|XDG_CONFIG_HOME|XDG_DATA_HOME|HOSTNAME|ANDROID_HOME|ANDROID_SDK_ROOT|ANDROID_NDK_ROOT|JAVA_HOME|PWD|SHLVL|_)
      ;;
    *)
      die "native wrapper build inherited an unexpected environment variable: ${name}"
      ;;
  esac
done

require_mount_options() {
  local target="$1"
  local required="$2"
  local forbidden="$3"
  local options
  options="$(/usr/bin/findmnt --raw --noheadings --mountpoint "${target}" --output OPTIONS)" \
    || die "cannot inspect native wrapper build mount: ${target}"
  [[ -n "${options}" && "${options}" != *$'\n'* ]] \
    || die "native wrapper build mount record is invalid: ${target}"
  local option
  IFS=',' read -r -a required_options <<< "${required}"
  for option in "${required_options[@]}"; do
    [[ ",${options}," == *",${option},"* ]] \
      || die "native wrapper build mount lacks ${option}: ${target} (${options})"
  done
  IFS=',' read -r -a forbidden_options <<< "${forbidden}"
  for option in "${forbidden_options[@]}"; do
    [[ ",${options}," != *",${option},"* ]] \
      || die "native wrapper build mount unexpectedly has ${option}: ${target} (${options})"
  done
}

require_mount_options / 'ro' 'rw'
require_mount_options /opt/zivplayer/toolchain 'ro' 'rw'
require_mount_options /build/wrapper 'ro,nosuid,nodev,noexec' 'rw,exec'

expected_wrapper_entries=$'Android.mk\nApplication.mk\nCMakeLists.txt\nMpvNativeBindings.kt\njni-contract.toml\nzivplayer_mpv.cpp'
actual_wrapper_entries="$(/usr/bin/find /build/wrapper -mindepth 1 -maxdepth 1 -printf '%P\n' | /usr/bin/sort)"
[[ "${actual_wrapper_entries}" == "${expected_wrapper_entries}" ]] \
  || die "native wrapper build input entry set is not exact"
[[ "$(( $(/usr/bin/find /build/wrapper -mindepth 2 -printf . | /usr/bin/wc -c) ))" -eq 0 ]] \
  || die "native wrapper build input contains nested entries"
[[ "$(/usr/bin/stat -c '%a %u %g' -- /build/wrapper)" == '555 0 0' ]] \
  || die "native wrapper build input root metadata differs"

require_wrapper_file() {
  local name="$1"
  local expected_size="$2"
  local expected_sha256="$3"
  local path="/build/wrapper/${name}"
  [[ -f "${path}" && ! -L "${path}" ]] \
    || die "native wrapper build input is not a regular file: ${name}"
  [[ "$(/usr/bin/stat -c '%a %u %g %h %s' -- "${path}")" \
      == "444 0 0 1 ${expected_size}" ]] \
    || die "native wrapper build input metadata differs: ${name}"
  [[ "$(/usr/bin/sha256sum -- "${path}")" == "${expected_sha256}  ${path}" ]] \
    || die "native wrapper build input digest differs: ${name}"
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

build_script=/build/source/buildscripts/buildall.sh
path_script=/build/source/buildscripts/include/path.sh
[[ -f "${build_script}" && ! -L "${build_script}" && -x "${build_script}" ]] \
  || die "locked wrapper build command is missing or unsafe"
[[ -f "${path_script}" && ! -L "${path_script}" && -x "${path_script}" ]] \
  || die "locked wrapper build environment helper is missing or unsafe"
[[ ! -e /build/source/buildscripts/prefix \
  && ! -L /build/source/buildscripts/prefix ]] \
  || die "native wrapper build prefix already exists"
[[ -z "$(/usr/bin/find /build/output -mindepth 1 -print -quit)" ]]
[[ -z "$(/usr/bin/find /build/home -mindepth 1 -print -quit)" ]]
[[ -z "$(/usr/bin/find /build/tmp -mindepth 1 -print -quit)" ]]

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

printf 'build-command\tstart:arm64-v8a\n'
"${build_script}" --arch arm64 mpv+zivplayer_mpv
[[ -s /build/source/buildscripts/prefix/arm64/lib/libzivplayer_mpv.so ]] \
  || die "arm64 wrapper build did not create libzivplayer_mpv.so"
printf 'build-command\tcomplete:arm64-v8a\n'

printf 'build-command\tstart:x86_64\n'
"${build_script}" --arch x86_64 mpv+zivplayer_mpv
[[ -s /build/source/buildscripts/prefix/x86_64/lib/libzivplayer_mpv.so ]] \
  || die "x86_64 wrapper build did not create libzivplayer_mpv.so"
printf 'build-command\tcomplete:x86_64\n'
printf 'build\tcommands-complete;staging-pending-parent-audit\n'
