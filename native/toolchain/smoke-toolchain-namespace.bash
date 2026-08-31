#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 077

die() {
  echo "error: $*" >&2
  exit 4
}

if [[ "$#" -ne 16 || "${EUID}" -ne 0 || "$$" -ne 1 ]]; then
  die "invalid composition namespace invocation"
fi

apt_root="$1"
sdk_root="$2"
apt_fd="$3"
sdk_fd="$4"
chroot_helper_fd="$5"
seccomp_helper_fd="$6"
apt_identity="$7"
sdk_identity="$8"
chroot_helper_sha256="$9"
seccomp_helper_sha256="${10}"
shift 10

for value in "${apt_fd}" "${sdk_fd}" "${chroot_helper_fd}" "${seccomp_helper_fd}"; do
  [[ "${value}" =~ ^[0-9]+$ ]] || die "input descriptor is not numeric"
  (( ${#value} <= 9 && 10#${value} >= 3 )) \
    || die "input descriptor is outside the allowed range"
done
[[ "${chroot_helper_sha256}" =~ ^[0-9a-f]{64}$ ]] \
  || die "chroot helper digest is invalid"
[[ "${seccomp_helper_sha256}" =~ ^[0-9a-f]{64}$ ]] \
  || die "seccomp helper digest is invalid"
[[ "${apt_root}" == /* && "${sdk_root}" == /* ]] \
  || die "composition inputs must be absolute"

for namespace_name in mnt net pid uts ipc; do
  expected="$1"
  shift
  current="$(/usr/bin/readlink -- "/proc/self/ns/${namespace_name}")"
  namespace_pattern="^${namespace_name}:\\[[0-9]+\\]$"
  if [[ ! "${expected}" =~ ${namespace_pattern} \
    || ! "${current}" =~ ${namespace_pattern} \
    || "${current}" == "${expected}" ]]; then
    die "composition did not enter a private ${namespace_name} namespace"
  fi
done
[[ "$1" == "ziv-native-toolchain-smoke-v1" ]] \
  || die "unknown composition smoke profile"

apt_source="/proc/self/fd/${apt_fd}"
sdk_source="/proc/self/fd/${sdk_fd}"
chroot_helper_source="/proc/self/fd/${chroot_helper_fd}"
seccomp_helper_source="/proc/self/fd/${seccomp_helper_fd}"
[[ "$(/usr/bin/stat -Lc '%d:%i' -- "${apt_source}")" == "${apt_identity}" ]] \
  || die "APT root descriptor identity changed"
[[ "$(/usr/bin/stat -Lc '%d:%i' -- "${sdk_source}")" == "${sdk_identity}" ]] \
  || die "SDK root descriptor identity changed"
[[ "$(/usr/bin/stat -Lc '%d:%i' -- "${apt_root}")" == "${apt_identity}" ]] \
  || die "APT root path identity changed"
[[ "$(/usr/bin/stat -Lc '%d:%i' -- "${sdk_root}")" == "${sdk_identity}" ]] \
  || die "SDK root path identity changed"
[[ "$(/usr/bin/sha256sum -- "${chroot_helper_source}")" \
    == "${chroot_helper_sha256}  ${chroot_helper_source}" ]] \
  || die "chroot helper differs from its pinned descriptor"
[[ "$(/usr/bin/sha256sum -- "${seccomp_helper_source}")" \
    == "${seccomp_helper_sha256}  ${seccomp_helper_source}" ]] \
  || die "seccomp helper differs from its pinned descriptor"

require_real_directory() {
  local path="$1"
  local expected_mode="$2"
  [[ -d "${path}" && ! -L "${path}" ]] \
    || die "composition mountpoint is not a real directory: ${path}"
  [[ "$(/usr/bin/readlink -e -- "${path}")" == "${path}" ]] \
    || die "composition mountpoint traverses a symbolic link: ${path}"
  [[ "$(/usr/bin/stat -c '%a %u %g' -- "${path}")" == "${expected_mode} 0 0" ]] \
    || die "composition mountpoint metadata differs: ${path}"
}

require_real_directory "${apt_root}" 700
require_real_directory "${sdk_root}" 700
for entry in \
  'opt 755' 'mnt 755' 'proc 755' 'dev 755' 'run 755' \
  'tmp 1777' 'var/tmp 1777' 'var/log 755'; do
  read -r relative mode <<< "${entry}"
  require_real_directory "${apt_root}/${relative}" "${mode}"
  [[ "$(/usr/bin/stat -c '%d' -- "${apt_root}/${relative}")" \
      == "$(/usr/bin/stat -c '%d' -- "${apt_root}")" ]] \
    || die "composition mountpoint crosses a filesystem boundary: ${relative}"
done
[[ -z "$(/usr/bin/find "${apt_root}/opt" -mindepth 1 -maxdepth 1 -print -quit)" ]] \
  || die "APT /opt must be empty before ephemeral composition"
[[ -z "$(/usr/bin/find "${apt_root}/mnt" -mindepth 1 -maxdepth 1 -print -quit)" ]] \
  || die "APT /mnt must be empty before pivot_root"
if /usr/bin/findmnt --noheadings --mountpoint "${apt_root}" >/dev/null 2>&1 \
  || /usr/bin/findmnt --noheadings --mountpoint "${sdk_root}" >/dev/null 2>&1; then
  die "composition input unexpectedly starts as a mountpoint"
fi

/usr/bin/mount --make-rprivate /
/usr/bin/mount --bind "${apt_source}" "${apt_root}"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev "${apt_root}"

/usr/bin/mount -t tmpfs tmpfs "${apt_root}/opt" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=16m
/usr/bin/install -d -m 0755 \
  "${apt_root}/opt/zivplayer" \
  "${apt_root}/opt/zivplayer/toolchain"
/usr/bin/mount --bind "${sdk_source}" \
  "${apt_root}/opt/zivplayer/toolchain"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,exec \
  "${apt_root}/opt/zivplayer/toolchain"

/usr/bin/mount -t proc proc "${apt_root}/proc" \
  -o ro,nosuid,nodev,noexec,hidepid=2
/usr/bin/mount -t tmpfs tmpfs "${apt_root}/dev" \
  -o rw,nosuid,noexec,mode=0755,size=16m
/usr/bin/install -d -m 1777 "${apt_root}/dev/shm"
for device in 'null 1 3 666' 'zero 1 5 666' 'random 1 8 666' 'urandom 1 9 666'; do
  read -r name major minor mode <<< "${device}"
  /usr/bin/mknod -m "${mode}" "${apt_root}/dev/${name}" c "${major}" "${minor}"
done
/usr/bin/mknod -m 0666 "${apt_root}/dev/tty" c 5 0
/usr/bin/ln -s /proc/self/fd "${apt_root}/dev/fd"
/usr/bin/ln -s /proc/self/fd/0 "${apt_root}/dev/stdin"
/usr/bin/ln -s /proc/self/fd/1 "${apt_root}/dev/stdout"
/usr/bin/ln -s /proc/self/fd/2 "${apt_root}/dev/stderr"
/usr/bin/mount -t tmpfs tmpfs "${apt_root}/dev/shm" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=64m
/usr/bin/mount -o remount,ro,nosuid,noexec "${apt_root}/dev"
/usr/bin/mount -t tmpfs tmpfs "${apt_root}/run" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=64m
/usr/bin/install -d -m 0755 "${apt_root}/run/lock"
/usr/bin/install -m 0400 "${chroot_helper_source}" \
  "${apt_root}/run/ziv-smoke-chroot.bash"
[[ "$(/usr/bin/sha256sum -- "${apt_root}/run/ziv-smoke-chroot.bash")" \
    == "${chroot_helper_sha256}  ${apt_root}/run/ziv-smoke-chroot.bash" ]] \
  || die "copied chroot helper digest changed"
/usr/bin/install -m 0444 /dev/null "${apt_root}/run/ziv-empty-proc-keys"
/usr/bin/mount --bind "${apt_root}/run/ziv-empty-proc-keys" \
  "${apt_root}/proc/keys"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec \
  "${apt_root}/proc/keys"
/usr/bin/mount -t tmpfs tmpfs "${apt_root}/tmp" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=512m
/usr/bin/mount -t tmpfs tmpfs "${apt_root}/var/tmp" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=512m
/usr/bin/mount -t tmpfs tmpfs "${apt_root}/var/log" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=64m
/usr/bin/hostname zivplayer-builder

eval "exec ${apt_fd}>&-"
eval "exec ${sdk_fd}>&-"
eval "exec ${chroot_helper_fd}>&-"
cd -- "${apt_root}"
/usr/sbin/pivot_root . mnt
cd /
/usr/bin/umount -R -l /mnt
if /usr/bin/grep -Eq '[[:space:]]/mnt(/|[[:space:]])' /proc/self/mountinfo \
  || /usr/bin/findmnt --noheadings --mountpoint /mnt >/dev/null 2>&1; then
  die "old host root remains visible after pivot_root"
fi

require_mount_policy() {
  local target="$1"
  local expected_filesystem="$2"
  local required="$3"
  local forbidden="$4"
  local filesystem options propagation option
  local -a required_options forbidden_options
  read -r filesystem options propagation \
    < <(/usr/bin/findmnt --raw --noheadings --mountpoint "${target}" \
      --output FSTYPE,OPTIONS,PROPAGATION)
  [[ "${filesystem}" == "${expected_filesystem}" && "${propagation}" == private ]] \
    || die "mount identity mismatch at ${target}: ${filesystem} ${propagation}"
  IFS=',' read -r -a required_options <<< "${required}"
  for option in "${required_options[@]}"; do
    [[ ",${options}," == *",${option},"* ]] \
      || die "mount policy at ${target} lacks ${option}: ${options}"
  done
  IFS=',' read -r -a forbidden_options <<< "${forbidden}"
  for option in "${forbidden_options[@]}"; do
    [[ -z "${option}" || ",${options}," != *",${option},"* ]] \
      || die "mount policy at ${target} exposes ${option}: ${options}"
  done
}

declare -A seen_mounts=()
while read -r target filesystem; do
  case "${target}:${filesystem}" in
    /:ext4|/opt:tmpfs|/opt/zivplayer/toolchain:ext4|/proc:proc|/proc/keys:tmpfs|/dev:tmpfs|/dev/shm:tmpfs|/run:tmpfs|/tmp:tmpfs|/var/tmp:tmpfs|/var/log:tmpfs)
      [[ -z "${seen_mounts["${target}"]+present}" ]] \
        || die "duplicate composition mount remains: ${target}"
      seen_mounts["${target}"]="${filesystem}"
      ;;
    *)
      die "unexpected composition mount remains: ${target} (${filesystem})"
      ;;
  esac
done < <(/usr/bin/findmnt --raw --noheadings --output TARGET,FSTYPE)
[[ "${#seen_mounts[@]}" -eq 11 ]] || die "composition mount set is incomplete"

require_mount_policy / ext4 'ro,nosuid,nodev' 'rw,noexec'
require_mount_policy /opt tmpfs 'rw,nosuid,nodev,noexec' 'ro,exec'
require_mount_policy /opt/zivplayer/toolchain ext4 'ro,nosuid,nodev' 'rw,noexec'
require_mount_policy /proc proc 'ro,nosuid,nodev,noexec' 'rw'
proc_options="$(/usr/bin/findmnt --raw --noheadings --mountpoint /proc --output OPTIONS)"
if [[ ",${proc_options}," != *,hidepid=invisible,* \
  && ",${proc_options}," != *,hidepid=2,* ]]; then
  die "proc mount does not enforce hidepid=2: ${proc_options}"
fi
require_mount_policy /proc/keys tmpfs 'ro,nosuid,nodev,noexec' 'rw'
require_mount_policy /dev tmpfs 'ro,nosuid,noexec' 'rw,nodev'
require_mount_policy /dev/shm tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /run tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /tmp tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /var/tmp tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /var/log tmpfs 'rw,nosuid,nodev,noexec' 'ro'

require_tmpfs_geometry() {
  local target="$1"
  local expected_size="$2"
  local expected_mode="$3"
  [[ "$(/usr/bin/findmnt --bytes --raw --noheadings \
      --mountpoint "${target}" --output SIZE)" == "${expected_size}" ]] \
    || die "tmpfs size differs at ${target}"
  [[ "$(/usr/bin/stat -c '%a' -- "${target}")" == "${expected_mode}" ]] \
    || die "tmpfs mode differs at ${target}"
}

require_tmpfs_geometry /opt 16777216 755
require_tmpfs_geometry /dev 16777216 755
require_tmpfs_geometry /dev/shm 67108864 1777
require_tmpfs_geometry /run 67108864 755
require_tmpfs_geometry /tmp 536870912 1777
require_tmpfs_geometry /var/tmp 536870912 1777
require_tmpfs_geometry /var/log 67108864 755

if ! /usr/bin/awk -F: '
  NR <= 2 { next }
  NF != 2 { exit 2 }
  {
    name=$1
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
    if (name == "lo") loopback++
    else extra++
  }
  END { if (loopback != 1 || extra != 0) exit 2 }
' /proc/net/dev; then
  die "composition network namespace is not loopback-only"
fi
if ! /usr/bin/awk '
  NR == 1 { if ($1 != "Iface") exit 2; next }
  { exit 2 }
' /proc/net/route; then
  die "composition network namespace exposes an IPv4 route"
fi
if ! /usr/bin/awk 'NF != 10 || $10 != "lo" { exit 2 }' /proc/net/ipv6_route; then
  die "composition network namespace exposes an IPv6 route"
fi

apt_seccomp_helper='/usr/share/zivplayer/toolchain-evidence/apt-stage/helpers/native/toolchain/install-seccomp.pl'
[[ -f "${apt_seccomp_helper}" && ! -L "${apt_seccomp_helper}" \
  && "$(/usr/bin/stat -c '%a %u %g %h' -- "${apt_seccomp_helper}")" == '644 0 0 1' ]] \
  || die "locked seccomp helper is unavailable"
[[ "$(/usr/bin/sha256sum -- "${apt_seccomp_helper}")" \
    == "${seccomp_helper_sha256}  ${apt_seccomp_helper}" ]] \
  || die "APT seccomp helper differs from the composition snapshot"
[[ "$(/usr/bin/sha256sum -- "${seccomp_helper_source}")" \
    == "${seccomp_helper_sha256}  ${seccomp_helper_source}" ]] \
  || die "pinned seccomp helper changed before execution"

exec /usr/bin/setpriv \
  --reuid=0 --regid=0 --clear-groups \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all \
  --no-new-privs \
  /usr/bin/env -i \
    PATH=/opt/zivplayer/toolchain/bin:/opt/zivplayer/toolchain/android-sdk/build-tools/36.0.0:/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LC_ALL=C LANG=C LANGUAGE=C TZ=UTC SOURCE_DATE_EPOCH=946684800 \
    PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp TMPDIR=/tmp HOSTNAME=zivplayer-builder \
    ANDROID_HOME=/opt/zivplayer/toolchain/android-sdk \
    ANDROID_SDK_ROOT=/opt/zivplayer/toolchain/android-sdk \
    ANDROID_NDK_ROOT=/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    /usr/bin/perl "${seccomp_helper_source}" \
    /bin/bash --noprofile --norc \
    /run/ziv-smoke-chroot.bash --chroot-smoke
