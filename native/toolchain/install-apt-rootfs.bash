#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

if [[ "${PATH:-}" != "/usr/sbin:/usr/bin:/sbin:/bin" \
  || "${LC_ALL:-}" != "C" \
  || "${TZ:-}" != "UTC" \
  || "${SOURCE_DATE_EPOCH:-}" != "946684800" ]] \
  || [[ -n "${BASH_ENV+x}${ENV+x}${PYTHONPATH+x}${PYTHONHOME+x}${TAR_OPTIONS+x}" ]] \
  || [[ -n "${DPKG_ROOT+x}${DPKG_ADMINDIR+x}${APT_CONFIG+x}${GNUPGHOME+x}" ]] \
  || [[ -n "${LD_PRELOAD+x}${LD_LIBRARY_PATH+x}${LD_AUDIT+x}${PERL5OPT+x}" ]] \
  || [[ -n "${http_proxy+x}${https_proxy+x}${HTTP_PROXY+x}${HTTPS_PROXY+x}${ALL_PROXY+x}${NO_PROXY+x}" ]]; then
  echo "error: refusing an unsanitized offline installer invocation" >&2
  exit 2
fi

set -euo pipefail
umask 077

if [[ "$#" -ne 2 || "$1" != "--rootfs" ]]; then
  echo "error: expected --rootfs ABSOLUTE_PATH" >&2
  exit 2
fi
if [[ "$(/usr/bin/uname -s)" != "Linux" || "$(/usr/bin/uname -m)" != "x86_64" ]]; then
  echo "error: offline APT installation requires Linux/amd64" >&2
  exit 2
fi
if [[ "${EUID}" -ne 0 ]]; then
  echo "error: offline APT installation requires root" >&2
  exit 2
fi

requested_rootfs="$2"
if [[ "${requested_rootfs}" != /* || "${requested_rootfs}" == *$'\n'* || "${requested_rootfs}" == *$'\r'* ]]; then
  echo "error: rootfs path must be an absolute single-line path" >&2
  exit 2
fi
rootfs="$(/usr/bin/readlink -e -- "${requested_rootfs}")"
if [[ "${rootfs}" != "${requested_rootfs}" || ! -d "${rootfs}" || -L "${rootfs}" ]]; then
  echo "error: rootfs must be a canonical real directory" >&2
  exit 2
fi
read -r root_mode root_uid root_gid < <(/usr/bin/stat -c '%a %u %g' -- "${rootfs}")
if [[ "${root_mode}" != "700" || "${root_uid}" != "0" || "${root_gid}" != "0" ]]; then
  echo "error: rootfs must be 0700 root:root" >&2
  exit 4
fi
if [[ "$(/usr/bin/findmnt --noheadings --output FSTYPE --target "${rootfs}" | /usr/bin/tr -d '[:space:]')" != "ext4" ]]; then
  echo "error: rootfs must be on ext4" >&2
  exit 4
fi
if /usr/bin/findmnt --noheadings --mountpoint "${rootfs}" >/dev/null 2>&1; then
  echo "error: rootfs must not already be a host mountpoint" >&2
  exit 4
fi
if [[ ! -f "${rootfs}/ziv-toolchain-base.json" || -L "${rootfs}/ziv-toolchain-base.json" ]]; then
  echo "error: verified base receipt is missing" >&2
  exit 3
fi
input="${rootfs}/ziv-apt-input"
if [[ ! -d "${input}" || -L "${input}" \
  || "$(/usr/bin/stat -c '%a %u %g' -- "${input}")" != "700 0 0" \
  || ! -f "${input}/install-apt-chroot.bash" \
  || -L "${input}/install-apt-chroot.bash" ]]; then
  echo "error: prepared offline installer input is missing" >&2
  exit 3
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_dir}/../.." && pwd -P)"
namespace_script="${script_dir}/install-apt-namespace.bash"
clean_exec="${repository_root}/native/tools/exec_clean.py"
if [[ ! -f "${namespace_script}" || -L "${namespace_script}" \
  || ! -f "${clean_exec}" || -L "${clean_exec}" ]]; then
  echo "error: trusted namespace launcher input is missing" >&2
  exit 3
fi
for tool in /usr/bin/env /usr/bin/python3.12 /usr/bin/unshare /bin/bash; do
  if [[ ! -f "${tool}" || -L "${tool}" ]]; then
    echo "error: canonical namespace launcher tool is missing: ${tool}" >&2
    exit 3
  fi
done
host_mount_namespace="$(/usr/bin/readlink -- /proc/self/ns/mnt)"
host_network_namespace="$(/usr/bin/readlink -- /proc/self/ns/net)"
host_pid_namespace="$(/usr/bin/readlink -- /proc/self/ns/pid)"
host_uts_namespace="$(/usr/bin/readlink -- /proc/self/ns/uts)"
host_ipc_namespace="$(/usr/bin/readlink -- /proc/self/ns/ipc)"
if [[ ! "${host_mount_namespace}" =~ ^mnt:\[[0-9]+\]$ \
  || ! "${host_network_namespace}" =~ ^net:\[[0-9]+\]$ \
  || ! "${host_pid_namespace}" =~ ^pid:\[[0-9]+\]$ \
  || ! "${host_uts_namespace}" =~ ^uts:\[[0-9]+\]$ \
  || ! "${host_ipc_namespace}" =~ ^ipc:\[[0-9]+\]$ ]]; then
  echo "error: cannot capture the exact host namespace identities" >&2
  exit 4
fi

exec /usr/bin/env -i \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LC_ALL=C \
  TZ=UTC \
  SOURCE_DATE_EPOCH=946684800 \
  /usr/bin/python3.12 -I -S -B "${clean_exec}" -- \
  /usr/bin/unshare \
    --mount --net --pid --fork --kill-child=SIGKILL --uts --ipc \
    --propagation private \
    /bin/bash --noprofile --norc "${namespace_script}" "${rootfs}" \
      "${host_mount_namespace}" "${host_network_namespace}" \
      "${host_pid_namespace}" "${host_uts_namespace}" "${host_ipc_namespace}"
