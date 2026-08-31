#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 077

if [[ "$#" -ne 6 || "$1" != /* || "${EUID}" -ne 0 || "$$" -ne 1 ]]; then
  echo "error: invalid namespace installer invocation" >&2
  exit 2
fi
rootfs="$1"
shift
for namespace_name in mnt net pid uts ipc; do
  expected="$1"
  shift
  current="$(/usr/bin/readlink -- "/proc/self/ns/${namespace_name}")"
  namespace_pattern="^${namespace_name}:\\[[0-9]+\\]$"
  if [[ ! "${expected}" =~ ${namespace_pattern} \
    || ! "${current}" =~ ${namespace_pattern} \
    || "${current}" == "${expected}" ]]; then
    echo "error: namespace installer did not enter a private ${namespace_name} namespace" >&2
    exit 4
  fi
done
input="${rootfs}/ziv-apt-input"
if [[ ! -d "${rootfs}" || -L "${rootfs}" || ! -d "${input}" || -L "${input}" ]]; then
  echo "error: namespace installer root/input is missing" >&2
  exit 3
fi

require_real_directory() {
  local path="$1"
  local resolved
  if [[ ! -d "${path}" || -L "${path}" ]]; then
    echo "error: installer mountpoint is not a real directory: ${path}" >&2
    exit 4
  fi
  resolved="$(/usr/bin/readlink -e -- "${path}")"
  if [[ "${resolved}" != "${path}" ]]; then
    echo "error: installer mountpoint traverses a symbolic link: ${path}" >&2
    exit 4
  fi
}

for mountpoint in \
  "${rootfs}" "${rootfs}/proc" "${rootfs}/dev" "${rootfs}/run" \
  "${rootfs}/tmp" "${rootfs}/var" "${rootfs}/var/tmp" \
  "${rootfs}/var/log" "${input}"; do
  require_real_directory "${mountpoint}"
done
root_device="$(/usr/bin/stat -c '%d' -- "${rootfs}")"
if [[ "$(/usr/bin/stat -c '%d' -- "${input}")" != "${root_device}" ]] \
  || /usr/bin/findmnt --noheadings --mountpoint "${rootfs}" >/dev/null 2>&1 \
  || /usr/bin/findmnt --noheadings --mountpoint "${input}" >/dev/null 2>&1; then
  echo "error: installer root/input is not a fresh same-filesystem tree" >&2
  exit 4
fi
policy="${rootfs}/usr/sbin/policy-rc.d"
if /usr/bin/findmnt --noheadings --mountpoint "${policy}" >/dev/null 2>&1; then
  echo "error: locked policy-rc.d must not already be a mountpoint" >&2
  exit 4
fi
if [[ ! -f "${policy}" || -L "${policy}" \
  || "$(/usr/bin/stat -c '%d' -- "${policy}")" != "${root_device}" \
  || "$(/usr/bin/readlink -e -- "${policy}")" != "${policy}" \
  || "$(/usr/bin/stat -c '%a %u %g %h' -- "${policy}")" != "755 0 0 1" \
  || "$(/usr/bin/sha256sum -- "${policy}")" \
    != "c2bcd9decf63ff2c0d9f473f38bc3607900530aad80f99139855d56678456230  ${policy}" ]]; then
  echo "error: locked policy-rc.d is missing or changed" >&2
  exit 4
fi
if [[ -e "${rootfs}/.ziv-old-root" || -L "${rootfs}/.ziv-old-root" ]]; then
  echo "error: pivot directory already exists in the prepared rootfs" >&2
  exit 4
fi

/usr/bin/mount --make-rprivate /
/usr/bin/mount --bind "${rootfs}" "${rootfs}"
/usr/bin/mount -o remount,bind,rw,nosuid,nodev "${rootfs}"
/usr/bin/mount --bind "${input}" "${input}"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec "${input}"
/usr/bin/mount --bind "${policy}" "${policy}"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev "${policy}"
/usr/bin/mkdir -m 0755 -- "${rootfs}/.ziv-old-root"

/usr/bin/mount -t proc proc "${rootfs}/proc" \
  -o ro,nosuid,nodev,noexec,hidepid=2
/usr/bin/mount -t tmpfs tmpfs "${rootfs}/dev" \
  -o rw,nosuid,noexec,mode=0755,size=16m
/usr/bin/install -d -m 1777 "${rootfs}/dev/shm"
for device in 'null 1 3 666' 'zero 1 5 666' 'random 1 8 666' 'urandom 1 9 666'; do
  read -r name major minor mode <<< "${device}"
  /usr/bin/mknod -m "${mode}" "${rootfs}/dev/${name}" c "${major}" "${minor}"
done
/usr/bin/mknod -m 0666 "${rootfs}/dev/tty" c 5 0
/usr/bin/ln -s /proc/self/fd "${rootfs}/dev/fd"
/usr/bin/ln -s /proc/self/fd/0 "${rootfs}/dev/stdin"
/usr/bin/ln -s /proc/self/fd/1 "${rootfs}/dev/stdout"
/usr/bin/ln -s /proc/self/fd/2 "${rootfs}/dev/stderr"
/usr/bin/mount -t tmpfs tmpfs "${rootfs}/dev/shm" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=64m
/usr/bin/mount -o remount,ro,nosuid,noexec "${rootfs}/dev"
/usr/bin/mount -t tmpfs tmpfs "${rootfs}/run" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=64m
/usr/bin/install -d -m 0755 "${rootfs}/run/lock"
/usr/bin/install -m 0444 /dev/null "${rootfs}/run/ziv-empty-proc-keys"
/usr/bin/mount --bind "${rootfs}/run/ziv-empty-proc-keys" "${rootfs}/proc/keys"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec "${rootfs}/proc/keys"
/usr/bin/mount -t tmpfs tmpfs "${rootfs}/tmp" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=512m
/usr/bin/mount -t tmpfs tmpfs "${rootfs}/var/tmp" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=512m
/usr/bin/mount -t tmpfs tmpfs "${rootfs}/var/log" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=64m
/usr/bin/hostname zivplayer-builder

cd -- "${rootfs}"
/usr/sbin/pivot_root . .ziv-old-root
cd /
/usr/bin/umount -R -l /.ziv-old-root
if /usr/bin/grep -Fq '/.ziv-old-root' /proc/self/mountinfo; then
  echo "error: old host root remains mounted after pivot_root" >&2
  exit 4
fi
/usr/bin/rmdir /.ziv-old-root
if /usr/bin/findmnt --raw --noheadings --output TARGET \
  | /usr/bin/grep -Eq '^/mnt(/|$)'; then
  echo "error: host mount remains visible below /mnt" >&2
  exit 4
fi
if [[ "$(/usr/bin/findmnt --noheadings --output FSTYPE --target / | /usr/bin/tr -d '[:space:]')" != "ext4" ]]; then
  echo "error: pivoted installer root is not ext4" >&2
  exit 4
fi
mount_count=0
while read -r target filesystem; do
  case "${target}:${filesystem}" in
    /:ext4|/proc:proc|/proc/keys:tmpfs|/dev:tmpfs|/dev/shm:tmpfs|/run:tmpfs|/tmp:tmpfs|/var/tmp:tmpfs|/var/log:tmpfs|/ziv-apt-input:ext4|/usr/sbin/policy-rc.d:ext4)
      mount_count=$((mount_count + 1))
      ;;
    *)
      echo "error: unexpected mount remains visible: ${target} (${filesystem})" >&2
      exit 4
      ;;
  esac
done < <(/usr/bin/findmnt --raw --noheadings --output TARGET,FSTYPE)
if [[ "${mount_count}" -ne 11 ]]; then
  echo "error: installer mount namespace does not contain the exact mount set" >&2
  exit 4
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
  if [[ "${filesystem}" != "${expected_filesystem}" || "${propagation}" != "private" ]]; then
    echo "error: mount policy identity mismatch at ${target}: ${filesystem} ${propagation}" >&2
    exit 4
  fi
  IFS=',' read -r -a required_options <<< "${required}"
  for option in "${required_options[@]}"; do
    if [[ ",${options}," != *",${option},"* ]]; then
      echo "error: mount policy at ${target} lacks ${option}: ${options}" >&2
      exit 4
    fi
  done
  IFS=',' read -r -a forbidden_options <<< "${forbidden}"
  for option in "${forbidden_options[@]}"; do
    if [[ -n "${option}" && ",${options}," == *",${option},"* ]]; then
      echo "error: mount policy at ${target} exposes ${option}: ${options}" >&2
      exit 4
    fi
  done
}

require_mount_policy / ext4 'rw,nosuid,nodev' 'ro,noexec'
require_mount_policy /proc proc 'ro,nosuid,nodev,noexec' 'rw'
require_mount_policy /proc/keys tmpfs 'ro,nosuid,nodev,noexec' 'rw'
proc_options="$(/usr/bin/findmnt --raw --noheadings --mountpoint /proc --output OPTIONS)"
if [[ ",${proc_options}," != *,hidepid=invisible,* \
  && ",${proc_options}," != *,hidepid=2,* ]]; then
  echo "error: proc mount does not enforce hidepid=2: ${proc_options}" >&2
  exit 4
fi
require_mount_policy /dev tmpfs 'ro,nosuid,noexec' 'rw,nodev'
require_mount_policy /dev/shm tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /run tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /tmp tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /var/tmp tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /var/log tmpfs 'rw,nosuid,nodev,noexec' 'ro'
require_mount_policy /ziv-apt-input ext4 'ro,nosuid,nodev,noexec' 'rw'
require_mount_policy /usr/sbin/policy-rc.d ext4 'ro,nosuid,nodev' 'rw,noexec'
require_tmpfs_geometry() {
  local target="$1"
  local expected_size="$2"
  local expected_mode="$3"
  local actual_size actual_mode
  actual_size="$(/usr/bin/findmnt --bytes --raw --noheadings \
    --mountpoint "${target}" --output SIZE)"
  actual_mode="$(/usr/bin/stat -c '%a' -- "${target}")"
  if [[ "${actual_size}" != "${expected_size}" || "${actual_mode}" != "${expected_mode}" ]]; then
    echo "error: tmpfs geometry mismatch at ${target}: ${actual_size} ${actual_mode}" >&2
    exit 4
  fi
}

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
  echo "error: installer network namespace lacks an exact loopback-only interface set" >&2
  exit 4
fi
if ! /usr/bin/awk '
  NR == 1 { if ($1 != "Iface") exit 2; next }
  { exit 2 }
' /proc/net/route; then
  echo "error: installer network namespace exposes or cannot parse an IPv4 route" >&2
  exit 4
fi
if ! /usr/bin/awk 'NF != 10 || $10 != "lo" { exit 2 }' /proc/net/ipv6_route; then
  echo "error: installer network namespace exposes or cannot parse an IPv6 route" >&2
  exit 4
fi
if [[ ! -x /usr/bin/perl || ! -f /ziv-apt-input/install-seccomp.pl \
  || -L /ziv-apt-input/install-seccomp.pl \
  || "$(/usr/bin/stat -c '%a %u %g %h' -- /ziv-apt-input/install-seccomp.pl)" != "400 0 0 1" ]]; then
  echo "error: locked seccomp helper is unavailable" >&2
  exit 4
fi

exec /bin/bash --noprofile --norc -c '
  set -euo pipefail
  for descriptor_path in /proc/self/fd/*; do
    descriptor="${descriptor_path##*/}"
    if [[ "${descriptor}" =~ ^[0-9]+$ ]] && (( descriptor > 2 )); then
      eval "exec ${descriptor}>&-"
    fi
  done
  exec /usr/bin/setpriv \
    --reuid=0 --regid=0 --clear-groups \
    --bounding-set=-all --inh-caps=-all --ambient-caps=-all \
    --no-new-privs \
    /usr/bin/env -i \
      PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
      LC_ALL=C LANG=C LANGUAGE=C TZ=UTC SOURCE_DATE_EPOCH=946684800 \
      HOME=/root TMPDIR=/tmp HOSTNAME=zivplayer-builder \
      DEBIAN_FRONTEND=noninteractive DEBCONF_NONINTERACTIVE_SEEN=true \
      UCF_FORCE_CONFFOLD=1 DPKG_COLORS=never \
      /usr/bin/perl /ziv-apt-input/install-seccomp.pl \
      /bin/bash --noprofile --norc /ziv-apt-input/install-apt-chroot.bash
'
