#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 077

die() {
  echo "error: $*" >&2
  exit 4
}

if [[ "$#" -ne 1 \
  || "$1" != "--execute-locked-build" \
  || "${EUID}" -ne 0 \
  || "$$" -ne 1 ]]; then
  die "invalid native build runner invocation"
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
    [[ "${descriptor_path}" -ef /run/ziv-native-build-runner.bash ]] \
      || die "native build runner inherited an unexpected descriptor: ${descriptor}"
    (( script_descriptor_count += 1 ))
  fi
done
(( script_descriptor_count <= 1 )) \
  || die "native build runner has duplicate script descriptors"

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

environment_dump="$(/usr/bin/env)" || die "cannot inspect native build environment"
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
    || die "native build runner environment record is invalid"
  name="${environment_record%%=*}"
  case "${name}" in
    PATH|LC_ALL|LANG|LANGUAGE|TZ|SOURCE_DATE_EPOCH|PYTHONHASHSEED|PYTHONDONTWRITEBYTECODE|HOME|TMPDIR|XDG_CACHE_HOME|XDG_CONFIG_HOME|XDG_DATA_HOME|HOSTNAME|ANDROID_HOME|ANDROID_SDK_ROOT|ANDROID_NDK_ROOT|JAVA_HOME|PWD|SHLVL|_)
      ;;
    *)
      die "native build runner inherited an unexpected environment variable: ${name}"
      ;;
  esac
done

require_read_only_mount() {
  local target="$1"
  local options
  options="$(/usr/bin/findmnt --raw --noheadings --target "${target}" --output OPTIONS)" \
    || die "cannot inspect native build read-only mount: ${target}"
  [[ -n "${options}" && "${options}" != *$'\n'* ]] \
    || die "native build read-only mount record is invalid: ${target}"
  [[ ",${options}," == *,ro,* && ",${options}," != *,rw,* ]] \
    || die "native build mount is not read-only: ${target} (${options})"
}

require_read_only_mount /
require_read_only_mount /opt/zivplayer/toolchain

build_script=/build/source/buildscripts/buildall.sh
path_script=/build/source/buildscripts/include/path.sh
[[ -f "${build_script}" && ! -L "${build_script}" && -x "${build_script}" ]] \
  || die "locked build command is missing or unsafe"
[[ -f "${path_script}" && ! -L "${path_script}" && -x "${path_script}" ]] \
  || die "locked build environment helper is missing or unsafe"
[[ ! -e /build/source/buildscripts/prefix \
  && ! -L /build/source/buildscripts/prefix ]] \
  || die "native build prefix already exists"
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
"${build_script}" --arch arm64 mpv
[[ -d /build/source/buildscripts/prefix/arm64/lib ]] \
  || die "arm64 build did not create its library root"
printf 'build-command\tcomplete:arm64-v8a\n'

printf 'build-command\tstart:x86_64\n'
"${build_script}" --arch x86_64 mpv
[[ -d /build/source/buildscripts/prefix/x86_64/lib ]] \
  || die "x86_64 build did not create its library root"
printf 'build-command\tcomplete:x86_64\n'
printf 'build\tcommands-complete;staging-pending-parent-audit\n'
