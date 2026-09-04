#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 077

die() {
  echo "error: $*" >&2
  exit 4
}

if [[ "$#" -ne 29 || "${EUID}" -ne 0 || "$$" -ne 1 ]]; then
  die "invalid native build namespace invocation"
fi

apt_root="$1"
sdk_root="$2"
workspace_root="$3"
apt_fd="$4"
sdk_fd="$5"
workspace_fd="$6"
source_fd="$7"
output_fd="$8"
home_fd="$9"
tmp_fd="${10}"
namespace_helper_fd="${11}"
probe_helper_fd="${12}"
seccomp_helper_fd="${13}"
apt_identity="${14}"
sdk_identity="${15}"
workspace_identity="${16}"
source_identity="${17}"
output_identity="${18}"
home_identity="${19}"
tmp_identity="${20}"
namespace_helper_sha256="${21}"
probe_helper_sha256="${22}"
seccomp_helper_sha256="${23}"
shift 23

declare -A seen_descriptors=()
for value in \
  "${apt_fd}" "${sdk_fd}" "${workspace_fd}" "${source_fd}" \
  "${output_fd}" "${home_fd}" "${tmp_fd}" "${namespace_helper_fd}" \
  "${probe_helper_fd}" "${seccomp_helper_fd}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || die "input descriptor is not canonical"
  (( ${#value} <= 9 && 10#${value} >= 3 && 10#${value} != 255 )) \
    || die "input descriptor is outside the allowed range"
  [[ -z "${seen_descriptors["${value}"]+present}" ]] \
    || die "input descriptors must be distinct"
  seen_descriptors["${value}"]=1
done
for value in \
  "${apt_identity}" "${sdk_identity}" "${workspace_identity}" \
  "${source_identity}" "${output_identity}" "${home_identity}" \
  "${tmp_identity}"; do
  [[ "${value}" =~ ^[0-9]+:[0-9]+$ ]] || die "input identity is invalid"
done
[[ "${probe_helper_sha256}" =~ ^[0-9a-f]{64}$ ]] \
  || die "probe helper digest is invalid"
[[ "${namespace_helper_sha256}" =~ ^[0-9a-f]{64}$ ]] \
  || die "namespace helper digest is invalid"
[[ "${seccomp_helper_sha256}" =~ ^[0-9a-f]{64}$ ]] \
  || die "seccomp helper digest is invalid"
for value in "${apt_root}" "${sdk_root}" "${workspace_root}"; do
  [[ "${value}" =~ ^/[A-Za-z0-9._/-]+$ \
    && "${value}" != *","* \
    && "${value}" != *":"* \
    && "${value}" != *$'\n'* \
    && "${value}" != *$'\r'* \
    && "${value}" != *$'\t'* ]] \
    || die "native build input path is invalid"
done

for namespace_name in mnt net pid uts ipc; do
  expected="$1"
  shift
  current="$(/usr/bin/readlink -- "/proc/self/ns/${namespace_name}")"
  namespace_pattern="^${namespace_name}:\\[[0-9]+\\]$"
  if [[ ! "${expected}" =~ ${namespace_pattern} \
    || ! "${current}" =~ ${namespace_pattern} \
    || "${current}" == "${expected}" ]]; then
    die "native build probe did not enter a private ${namespace_name} namespace"
  fi
done
[[ "$1" == "ziv-native-build-namespace-probe-v1" ]] \
  || die "unknown native build namespace profile"

apt_source="/proc/self/fd/${apt_fd}"
sdk_source="/proc/self/fd/${sdk_fd}"
workspace_source="/proc/self/fd/${workspace_fd}"
source_source="/proc/self/fd/${source_fd}"
output_source="/proc/self/fd/${output_fd}"
home_source="/proc/self/fd/${home_fd}"
tmp_source="/proc/self/fd/${tmp_fd}"
namespace_helper_source="/proc/self/fd/${namespace_helper_fd}"
probe_helper_source="/proc/self/fd/${probe_helper_fd}"
seccomp_helper_source="/proc/self/fd/${seccomp_helper_fd}"

check_identity() {
  local path="$1"
  local expected="$2"
  local label="$3"
  [[ "$(/usr/bin/stat -Lc '%d:%i' -- "${path}")" == "${expected}" ]] \
    || die "${label} identity changed"
}

check_identity "${apt_source}" "${apt_identity}" "APT root descriptor"
check_identity "${sdk_source}" "${sdk_identity}" "SDK descriptor"
check_identity "${workspace_source}" "${workspace_identity}" "workspace descriptor"
check_identity "${source_source}" "${source_identity}" "source descriptor"
check_identity "${output_source}" "${output_identity}" "output descriptor"
check_identity "${home_source}" "${home_identity}" "HOME descriptor"
check_identity "${tmp_source}" "${tmp_identity}" "temporary descriptor"
check_identity "${apt_root}" "${apt_identity}" "APT root path"
check_identity "${sdk_root}" "${sdk_identity}" "SDK path"
check_identity "${workspace_root}" "${workspace_identity}" "workspace path"
check_identity "${workspace_root}/source" "${source_identity}" "source path"
check_identity "${workspace_root}/output" "${output_identity}" "output path"
check_identity "${workspace_root}/home" "${home_identity}" "HOME path"
check_identity "${workspace_root}/tmp" "${tmp_identity}" "temporary path"
for path in \
  "${apt_source}" "${sdk_source}" "${workspace_source}" \
  "${source_source}" "${output_source}" "${home_source}" "${tmp_source}"; do
  [[ "$(/usr/bin/stat -f -c '%t' -- "${path}")" == ef53 ]] \
    || die "native build input is not on an ext filesystem: ${path}"
done
[[ "$(/usr/bin/sha256sum -- "${namespace_helper_source}")" \
    == "${namespace_helper_sha256}  ${namespace_helper_source}" ]] \
  || die "namespace helper differs from its pinned descriptor"
[[ "$(/usr/bin/sha256sum -- "${probe_helper_source}")" \
    == "${probe_helper_sha256}  ${probe_helper_source}" ]] \
  || die "probe helper differs from its pinned descriptor"
[[ "$(/usr/bin/sha256sum -- "${seccomp_helper_source}")" \
    == "${seccomp_helper_sha256}  ${seccomp_helper_source}" ]] \
  || die "seccomp helper differs from its pinned descriptor"

require_real_directory() {
  local path="$1"
  local expected_mode="$2"
  [[ -d "${path}" && ! -L "${path}" ]] \
    || die "native build mountpoint is not a real directory: ${path}"
  [[ "$(/usr/bin/readlink -e -- "${path}")" == "${path}" ]] \
    || die "native build mountpoint traverses a symbolic link: ${path}"
  [[ "$(/usr/bin/stat -c '%a %u %g' -- "${path}")" == "${expected_mode} 0 0" ]] \
    || die "native build mountpoint metadata differs: ${path}"
}

require_real_directory "${apt_root}" 700
require_real_directory "${sdk_root}" 700
require_real_directory "${workspace_root}" 700
require_real_directory "${workspace_root}/source" 755
require_real_directory "${workspace_root}/output" 700
require_real_directory "${workspace_root}/home" 700
require_real_directory "${workspace_root}/tmp" 700
for relative in opt mnt proc dev run tmp var/tmp var/log; do
  mode=755
  if [[ "${relative}" == tmp || "${relative}" == var/tmp ]]; then
    mode=1777
  fi
  require_real_directory "${apt_root}/${relative}" "${mode}"
  [[ "$(/usr/bin/stat -c '%d' -- "${apt_root}/${relative}")" \
      == "$(/usr/bin/stat -c '%d' -- "${apt_root}")" ]] \
    || die "APT mountpoint crosses a filesystem boundary: ${relative}"
done
[[ -z "$(/usr/bin/find "${apt_root}/opt" -mindepth 1 -maxdepth 1 -print -quit)" ]] \
  || die "APT /opt must be empty before native build composition"
[[ -z "$(/usr/bin/find "${apt_root}/mnt" -mindepth 1 -maxdepth 1 -print -quit)" ]] \
  || die "APT /mnt must be empty before native build composition"
[[ ! -e "${apt_root}/build" && ! -L "${apt_root}/build" ]] \
  || die "APT lower must not contain /build"
for path in "${apt_root}" "${sdk_root}" "${workspace_root}"; do
  if /usr/bin/findmnt --noheadings --mountpoint "${path}" >/dev/null 2>&1; then
    die "native build input unexpectedly starts as a mountpoint: ${path}"
  fi
done

/usr/bin/mount --make-rprivate /
carrier="${apt_root}/mnt"
/usr/bin/mount -t tmpfs tmpfs "${carrier}" \
  -o rw,nosuid,nodev,noexec,mode=0700,size=64m
carrier_record="$(/usr/bin/findmnt --raw --noheadings --mountpoint "${carrier}" \
  --output TARGET,FSTYPE,OPTIONS,PROPAGATION)" \
  || die "cannot inspect native build carrier tmpfs"
[[ "${carrier_record}" != *$'\n'* ]] \
  || die "native build carrier tmpfs has duplicate mount records"
carrier_target="${carrier_record%% *}"
carrier_tail="${carrier_record#* }"
[[ "${carrier_tail}" != "${carrier_record}" ]] \
  || die "native build carrier record is incomplete"
carrier_filesystem="${carrier_tail%% *}"
carrier_tail_next="${carrier_tail#* }"
[[ "${carrier_tail_next}" != "${carrier_tail}" ]] \
  || die "native build carrier record is incomplete"
carrier_options="${carrier_tail_next%% *}"
carrier_propagation="${carrier_tail_next#* }"
[[ "${carrier_propagation}" != "${carrier_tail_next}" \
  && "${carrier_propagation}" != *" "* ]] \
  || die "native build carrier record is incomplete"
if [[ "${carrier_target}" != "${carrier}" \
  || "${carrier_filesystem}" != tmpfs \
  || "${carrier_propagation}" != private \
  || ",${carrier_options}," != *,rw,* \
  || ",${carrier_options}," != *,nosuid,* \
  || ",${carrier_options}," != *,nodev,* \
  || ",${carrier_options}," != *,noexec,* \
  || "$(/usr/bin/stat -f -c '%t' -- "${carrier}")" != 1021994 \
  || "$(/usr/bin/stat -c '%a' -- "${carrier}")" != 700 \
  || "$(/usr/bin/findmnt --bytes --raw --noheadings \
      --mountpoint "${carrier}" --output SIZE)" != 67108864 \
  || "$(/usr/bin/stat -Lc '%d' -- "${carrier}")" \
      == "$(/usr/bin/stat -Lc '%d' -- "${apt_root}")" ]]; then
  die "native build carrier tmpfs policy is not exact"
fi
/usr/bin/install -d -m 0700 \
  "${carrier}/upper" "${carrier}/work" "${carrier}/root"
new_root="${carrier}/root"
carrier_device="$(/usr/bin/stat -Lc '%d' -- "${carrier}")"
for path in "${carrier}/upper" "${carrier}/work" "${new_root}"; do
  [[ "$(/usr/bin/stat -Lc '%a %u %g %d' -- "${path}")" \
      == "700 0 0 ${carrier_device}" \
    && "$(/usr/bin/stat -f -c '%t' -- "${path}")" == 1021994 ]] \
    || die "native build carrier path escaped tmpfs: ${path}"
done
check_identity "${apt_source}" "${apt_identity}" "APT lower descriptor"
check_identity "${apt_root}" "${apt_identity}" "APT lower path"
/usr/bin/mount -t overlay overlay "${new_root}" \
  -o "lowerdir=${apt_root},upperdir=${carrier}/upper,workdir=${carrier}/work,nosuid,nodev"

/usr/bin/install -d -m 0755 \
  "${new_root}/build" \
  "${new_root}/opt/zivplayer" \
  "${new_root}/opt/zivplayer/toolchain"
/usr/bin/mount -t tmpfs tmpfs "${new_root}/build" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=16m
/usr/bin/install -d \
  -m 0755 "${new_root}/build/source"
/usr/bin/install -d \
  -m 0700 \
  "${new_root}/build/output" \
  "${new_root}/build/home" \
  "${new_root}/build/tmp"

check_identity "${sdk_source}" "${sdk_identity}" "SDK bind descriptor"
check_identity "${sdk_root}" "${sdk_identity}" "SDK bind path"
/usr/bin/mount --bind "${sdk_source}" "${new_root}/opt/zivplayer/toolchain"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,exec \
  "${new_root}/opt/zivplayer/toolchain"
check_identity "${new_root}/opt/zivplayer/toolchain" "${sdk_identity}" \
  "SDK bind target"
check_identity "${source_source}" "${source_identity}" "source bind descriptor"
check_identity "${workspace_root}/source" "${source_identity}" "source bind path"
/usr/bin/mount --bind "${source_source}" "${new_root}/build/source"
/usr/bin/mount -o remount,bind,rw,nosuid,nodev,exec \
  "${new_root}/build/source"
check_identity "${new_root}/build/source" "${source_identity}" "source bind target"
check_identity "${output_source}" "${output_identity}" "output bind descriptor"
check_identity "${workspace_root}/output" "${output_identity}" "output bind path"
/usr/bin/mount --bind "${output_source}" "${new_root}/build/output"
/usr/bin/mount -o remount,bind,rw,nosuid,nodev,noexec \
  "${new_root}/build/output"
check_identity "${new_root}/build/output" "${output_identity}" "output bind target"
check_identity "${home_source}" "${home_identity}" "HOME bind descriptor"
check_identity "${workspace_root}/home" "${home_identity}" "HOME bind path"
/usr/bin/mount --bind "${home_source}" "${new_root}/build/home"
/usr/bin/mount -o remount,bind,rw,nosuid,nodev,noexec \
  "${new_root}/build/home"
check_identity "${new_root}/build/home" "${home_identity}" "HOME bind target"
check_identity "${tmp_source}" "${tmp_identity}" "temporary bind descriptor"
check_identity "${workspace_root}/tmp" "${tmp_identity}" "temporary bind path"
/usr/bin/mount --bind "${tmp_source}" "${new_root}/build/tmp"
/usr/bin/mount -o remount,bind,rw,nosuid,nodev,exec \
  "${new_root}/build/tmp"
check_identity "${new_root}/build/tmp" "${tmp_identity}" "temporary bind target"
/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "${new_root}/build"

/usr/bin/mount -t proc proc "${new_root}/proc" \
  -o ro,nosuid,nodev,noexec,hidepid=2
/usr/bin/mount -t tmpfs tmpfs "${new_root}/dev" \
  -o rw,nosuid,noexec,mode=0755,size=16m
/usr/bin/install -d -m 1777 "${new_root}/dev/shm"
for name in null zero random urandom; do
  major=1
  case "${name}" in
    null) minor=3 ;;
    zero) minor=5 ;;
    random) minor=8 ;;
    urandom) minor=9 ;;
    *) die "unknown native build device: ${name}" ;;
  esac
  mode=666
  /usr/bin/mknod -m "${mode}" "${new_root}/dev/${name}" c "${major}" "${minor}"
done
/usr/bin/mknod -m 0666 "${new_root}/dev/tty" c 5 0
/usr/bin/ln -s /proc/self/fd "${new_root}/dev/fd"
/usr/bin/ln -s /proc/self/fd/0 "${new_root}/dev/stdin"
/usr/bin/ln -s /proc/self/fd/1 "${new_root}/dev/stdout"
/usr/bin/ln -s /proc/self/fd/2 "${new_root}/dev/stderr"
/usr/bin/mount -t tmpfs tmpfs "${new_root}/dev/shm" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=64m
/usr/bin/mount -o remount,ro,nosuid,noexec "${new_root}/dev"
/usr/bin/mount -t tmpfs tmpfs "${new_root}/run" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=64m
/usr/bin/install -d -m 0755 "${new_root}/run/lock"
/usr/bin/install -m 0400 "${probe_helper_source}" \
  "${new_root}/run/ziv-native-build-probe.bash"
[[ "$(/usr/bin/sha256sum -- "${new_root}/run/ziv-native-build-probe.bash")" \
    == "${probe_helper_sha256}  ${new_root}/run/ziv-native-build-probe.bash" ]] \
  || die "copied native build probe helper digest changed"
/usr/bin/install -m 0444 /dev/null "${new_root}/run/ziv-empty-proc-keys"
/usr/bin/mount --bind "${new_root}/run/ziv-empty-proc-keys" \
  "${new_root}/proc/keys"
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec \
  "${new_root}/proc/keys"
/usr/bin/mount -t tmpfs tmpfs "${new_root}/tmp" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=64m
/usr/bin/mount -t tmpfs tmpfs "${new_root}/var/tmp" \
  -o rw,nosuid,nodev,noexec,mode=1777,size=64m
/usr/bin/mount -t tmpfs tmpfs "${new_root}/var/log" \
  -o rw,nosuid,nodev,noexec,mode=0755,size=64m
/usr/bin/hostname zivplayer-builder
/usr/bin/mount -o remount,ro,nosuid,nodev "${new_root}"

for descriptor in \
  "${apt_fd}" "${sdk_fd}" "${workspace_fd}" "${source_fd}" \
  "${output_fd}" "${home_fd}" "${tmp_fd}" "${probe_helper_fd}"; do
  eval "exec ${descriptor}>&-"
done
cd -- "${new_root}"
/usr/sbin/pivot_root . mnt
cd /
/usr/bin/umount -R -l /mnt
if /usr/bin/grep -Eq '[[:space:]]/mnt(/|[[:space:]])' /proc/self/mountinfo \
  || /usr/bin/findmnt --noheadings --mountpoint /mnt >/dev/null 2>&1; then
  die "old host root remains visible after native build pivot_root"
fi

require_mount_policy() {
  local target="$1"
  local expected_filesystem="$2"
  local required="$3"
  local forbidden="$4"
  local filesystem options propagation option mount_record mount_tail remaining
  mount_record="$(/usr/bin/findmnt --raw --noheadings --mountpoint "${target}" \
    --output FSTYPE,OPTIONS,PROPAGATION)" \
    || die "cannot inspect native build mount: ${target}"
  [[ "${mount_record}" != *$'\n'* ]] \
    || die "native build mount has duplicate records: ${target}"
  filesystem="${mount_record%% *}"
  mount_tail="${mount_record#* }"
  [[ "${mount_tail}" != "${mount_record}" ]] \
    || die "native build mount record is incomplete: ${target}"
  options="${mount_tail%% *}"
  propagation="${mount_tail#* }"
  [[ "${propagation}" != "${mount_tail}" && "${propagation}" != *" "* ]] \
    || die "native build mount record is incomplete: ${target}"
  [[ "${filesystem}" == "${expected_filesystem}" && "${propagation}" == private ]] \
    || die "mount identity mismatch at ${target}: ${filesystem} ${propagation}"
  remaining="${required}"
  while [[ -n "${remaining}" ]]; do
    option="${remaining%%,*}"
    [[ ",${options}," == *",${option},"* ]] \
      || die "mount policy at ${target} lacks ${option}: ${options}"
    if [[ "${remaining}" != *,* ]]; then
      break
    fi
    remaining="${remaining#*,}"
  done
  remaining="${forbidden}"
  while [[ -n "${remaining}" ]]; do
    option="${remaining%%,*}"
    [[ ",${options}," != *",${option},"* ]] \
      || die "mount policy at ${target} exposes ${option}: ${options}"
    if [[ "${remaining}" != *,* ]]; then
      break
    fi
    remaining="${remaining#*,}"
  done
}

declare -A seen_mounts=()
mount_inventory="$(/usr/bin/findmnt --raw --noheadings --output TARGET,FSTYPE)" \
  || die "cannot inventory native build mounts"
remaining_mounts="${mount_inventory}"
while [[ -n "${remaining_mounts}" ]]; do
  if [[ "${remaining_mounts}" == *$'\n'* ]]; then
    mount_record="${remaining_mounts%%$'\n'*}"
    remaining_mounts="${remaining_mounts#*$'\n'}"
  else
    mount_record="${remaining_mounts}"
    remaining_mounts=""
  fi
  target="${mount_record%% *}"
  filesystem="${mount_record#* }"
  [[ "${filesystem}" != "${mount_record}" && "${filesystem}" != *" "* ]] \
    || die "native build mount inventory record is invalid"
  case "${target}:${filesystem}" in
    /:overlay|/build:tmpfs|/build/source:ext4|/build/output:ext4|/build/home:ext4|/build/tmp:ext4|/opt/zivplayer/toolchain:ext4|/proc:proc|/proc/keys:tmpfs|/dev:tmpfs|/dev/shm:tmpfs|/run:tmpfs|/tmp:tmpfs|/var/tmp:tmpfs|/var/log:tmpfs)
      [[ -z "${seen_mounts["${target}"]+present}" ]] \
        || die "duplicate native build mount remains: ${target}"
      seen_mounts["${target}"]="${filesystem}"
      ;;
    *)
      die "unexpected native build mount remains: ${target} (${filesystem})"
      ;;
  esac
done
[[ "${#seen_mounts[@]}" -eq 15 ]] || die "native build mount set is incomplete"

require_mount_policy / overlay 'ro,nosuid,nodev' 'rw,noexec'
require_mount_policy /build tmpfs 'ro,nosuid,nodev,noexec' 'rw,exec'
require_mount_policy /build/source ext4 'rw,nosuid,nodev' 'ro,noexec'
require_mount_policy /build/output ext4 'rw,nosuid,nodev,noexec' 'ro,exec'
require_mount_policy /build/home ext4 'rw,nosuid,nodev,noexec' 'ro,exec'
require_mount_policy /build/tmp ext4 'rw,nosuid,nodev' 'ro,noexec'
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

require_tmpfs_geometry /build 16777216 755
require_tmpfs_geometry /dev 16777216 755
require_tmpfs_geometry /dev/shm 67108864 1777
require_tmpfs_geometry /run 67108864 755
require_tmpfs_geometry /tmp 67108864 1777
require_tmpfs_geometry /var/tmp 67108864 1777
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
  die "native build network namespace is not loopback-only"
fi
if ! /usr/bin/awk '
  NR == 1 { if ($1 != "Iface") exit 2; next }
  { exit 2 }
' /proc/net/route; then
  die "native build namespace exposes an IPv4 route"
fi
if ! /usr/bin/awk 'NF != 10 || $10 != "lo" { exit 2 }' /proc/net/ipv6_route; then
  die "native build namespace exposes an IPv6 route"
fi

apt_seccomp_helper='/usr/share/zivplayer/toolchain-evidence/apt-stage/helpers/native/toolchain/install-seccomp.pl'
[[ -f "${apt_seccomp_helper}" && ! -L "${apt_seccomp_helper}" \
  && "$(/usr/bin/stat -c '%a %u %g %h' -- "${apt_seccomp_helper}")" == '644 0 0 1' ]] \
  || die "locked seccomp helper is unavailable"
[[ "$(/usr/bin/sha256sum -- "${apt_seccomp_helper}")" \
    == "${seccomp_helper_sha256}  ${apt_seccomp_helper}" ]] \
  || die "APT seccomp helper differs from the execution policy"
[[ "$(/usr/bin/sha256sum -- "${seccomp_helper_source}")" \
    == "${seccomp_helper_sha256}  ${seccomp_helper_source}" ]] \
  || die "pinned seccomp helper changed before execution"
namespace_script_descriptors=()
for descriptor_path in /proc/self/fd/*; do
  descriptor="${descriptor_path##*/}"
  if [[ ! -e "${descriptor_path}" && ! -L "${descriptor_path}" ]]; then
    continue
  fi
  case "${descriptor}" in
    0|1|2|"${namespace_helper_fd}"|"${seccomp_helper_fd}")
      ;;
    *)
      if [[ "${descriptor}" =~ ^[0-9]+$ \
        && "${descriptor_path}" -ef "${namespace_helper_source}" ]]; then
        namespace_script_descriptors+=("${descriptor}")
      else
        die "native namespace helper retained an unexpected descriptor: ${descriptor}"
      fi
      ;;
  esac
done
(( ${#namespace_script_descriptors[@]} <= 1 )) \
  || die "native namespace helper retained duplicate script descriptors"

# A brace group is parsed before its first command, so closing Bash's internal
# script descriptor here cannot prevent the final exec command from being read.
{
  eval "exec ${namespace_helper_fd}>&-"
  eval "exec ${seccomp_helper_fd}>&-"
  for descriptor in "${namespace_script_descriptors[@]}"; do
    eval "exec ${descriptor}>&-"
  done
  exec /usr/bin/setpriv \
    --reuid=0 --regid=0 --clear-groups \
    --bounding-set=-all --inh-caps=-all --ambient-caps=-all \
    --no-new-privs \
    /usr/bin/env -i \
      PATH=/opt/zivplayer/toolchain/bin:/opt/zivplayer/toolchain/android-sdk/build-tools/36.0.0:/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin:/usr/sbin:/usr/bin:/sbin:/bin \
      LC_ALL=C LANG=C LANGUAGE=C TZ=UTC SOURCE_DATE_EPOCH=946684800 \
      PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 \
      HOME=/build/home TMPDIR=/build/tmp \
      XDG_CACHE_HOME=/build/home/.cache \
      XDG_CONFIG_HOME=/build/home/.config \
      XDG_DATA_HOME=/build/home/.local/share \
      HOSTNAME=zivplayer-builder \
      ANDROID_HOME=/opt/zivplayer/toolchain/android-sdk \
      ANDROID_SDK_ROOT=/opt/zivplayer/toolchain/android-sdk \
      ANDROID_NDK_ROOT=/opt/zivplayer/toolchain/android-sdk/ndk/29.0.14206865 \
      JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
      /usr/bin/perl "${apt_seccomp_helper}" \
      /bin/bash --noprofile --norc \
      /run/ziv-native-build-probe.bash --namespace-probe
}
