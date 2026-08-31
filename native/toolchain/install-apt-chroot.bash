#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
umask 022

if [[ "${EUID}" -ne 0 || ! -f /ziv-apt-input/install-order.tsv ]]; then
  echo "error: invalid pivoted APT installer state" >&2
  exit 2
fi
if [[ ! -r /proc/keys || -s /proc/keys ]]; then
  echo "error: package scripts can observe host keyring metadata" >&2
  exit 4
fi
if ! security_state="$(/usr/bin/awk '
  $1 ~ /^Cap(Inh|Prm|Eff|Bnd|Amb):$/ {
    if (++seen[$1] != 1 || $2 !~ /^[0-9A-Fa-f]+$/ || $2 !~ /^0+$/) bad=1
    capabilities++
  }
  $1 == "NoNewPrivs:" {
    if (++nnp_count != 1 || $2 !~ /^[01]$/) bad=1
    nnp=$2
  }
  $1 == "Seccomp:" {
    if (++seccomp_count != 1 || $2 !~ /^[0-9]+$/) bad=1
    seccomp=$2
  }
  $1 == "Seccomp_filters:" {
    if (++filter_count != 1 || $2 !~ /^[0-9]+$/) bad=1
    filters=$2
  }
  END {
    if (bad || capabilities != 5 || nnp_count != 1 || seccomp_count != 1 \
      || filter_count != 1) exit 2
    printf "%s %s %s\n", nnp, seccomp, filters
  }
' /proc/self/status)"; then
  echo "error: cannot prove the package-script process security state" >&2
  exit 4
fi
read -r no_new_privs seccomp_mode seccomp_filters <<< "${security_state}"
if [[ "${no_new_privs}" != "1" || "${seccomp_mode}" != "2" \
  || ! "${seccomp_filters}" =~ ^[0-9]+$ || "${seccomp_filters}" -lt 1 ]]; then
  echo "error: package scripts lack the locked capability/seccomp policy" >&2
  exit 4
fi
input_options="$(/usr/bin/findmnt --noheadings --output OPTIONS --target /ziv-apt-input)"
if [[ ",${input_options}," != *,ro,* \
  || ",${input_options}," != *,nosuid,* \
  || ",${input_options}," != *,nodev,* \
  || ",${input_options}," != *,noexec,* ]]; then
  echo "error: locked APT input is not mounted read-only with safe flags" >&2
  exit 4
fi
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
  echo "error: package scripts lack an exact loopback-only interface set" >&2
  exit 4
fi
if ! /usr/bin/awk '
  NR == 1 { if ($1 != "Iface") exit 2; next }
  { exit 2 }
' /proc/net/route; then
  echo "error: package scripts can observe or cannot parse an IPv4 route" >&2
  exit 4
fi
if ! /usr/bin/awk 'NF != 10 || $10 != "lo" { exit 2 }' /proc/net/ipv6_route; then
  echo "error: package scripts can observe or cannot parse an IPv6 route" >&2
  exit 4
fi

policy=/usr/sbin/policy-rc.d
if [[ ! -f "${policy}" || -L "${policy}" \
  || "$(/usr/bin/stat -c '%a %u %g %h' -- "${policy}")" != "755 0 0 1" \
  || "$(/usr/bin/sha256sum -- "${policy}")" \
    != "c2bcd9decf63ff2c0d9f473f38bc3607900530aad80f99139855d56678456230  ${policy}" ]]; then
  echo "error: locked policy-rc.d is missing or changed" >&2
  exit 4
fi

expected_header=$'sequence\tpackage\tarchitecture\tversion\tarchive\tsha256'
IFS= read -r header < /ziv-apt-input/install-order.tsv
if [[ "${header}" != "${expected_header}" ]]; then
  echo "error: APT install order header is not canonical" >&2
  exit 4
fi

sequence=0
while IFS=$'\t' read -r current package architecture version archive sha256 extra; do
  sequence=$((sequence + 1))
  if [[ "${current}" != "${sequence}" || -n "${extra:-}" \
    || ! "${package}" =~ ^[a-z0-9][a-z0-9+.-]*$ \
    || ! "${architecture}" =~ ^(amd64|all)$ \
    || ! "${version}" =~ ^[!-~]+$ \
    || ! "${archive}" =~ ^[A-Za-z0-9][A-Za-z0-9%+._~-]*\.deb$ \
    || ! "${sha256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "error: invalid APT install order row ${sequence}" >&2
    exit 4
  fi
  package_path="/ziv-apt-input/debs/${archive}"
  if [[ ! -f "${package_path}" || -L "${package_path}" ]]; then
    echo "error: locked package is missing: ${archive}" >&2
    exit 3
  fi
  actual_sha256="$(/usr/bin/sha256sum -- "${package_path}")"
  actual_sha256="${actual_sha256%% *}"
  if [[ "${actual_sha256}" != "${sha256}" ]]; then
    echo "error: locked package digest changed: ${archive}" >&2
    exit 4
  fi
  /usr/bin/printf 'install[%03d] %s %s %s\n' \
    "${sequence}" "${package}" "${architecture}" "${version}"
  /usr/bin/dpkg --log=/var/log/ziv-dpkg.log \
    --install --force-confold -- "${package_path}"
done < <(/usr/bin/tail -n +2 /ziv-apt-input/install-order.tsv)

if [[ "${sequence}" -ne 102 ]]; then
  echo "error: expected 102 package-install rows, got ${sequence}" >&2
  exit 4
fi
deb_count="$(/usr/bin/find /ziv-apt-input/debs -mindepth 1 -maxdepth 1 -type f -name '*.deb' -printf '.' | /usr/bin/wc -c)"
if [[ "${deb_count}" != "102" ]]; then
  echo "error: prepared APT input contains an unexpected package set" >&2
  exit 4
fi

/usr/bin/dpkg --log=/var/log/ziv-dpkg.log --configure --pending
/usr/bin/dpkg --log=/var/log/ziv-dpkg.log --triggers-only --pending
/usr/bin/dpkg --log=/var/log/ziv-dpkg.log --configure --pending
/usr/bin/dpkg --log=/var/log/ziv-dpkg.log --triggers-only --pending
if ! /usr/bin/dpkg --audit > /tmp/ziv-dpkg-audit.txt; then
  echo "error: dpkg audit command failed" >&2
  exit 4
fi
if [[ -s /tmp/ziv-dpkg-audit.txt ]]; then
  echo "error: dpkg audit reports an incomplete installation" >&2
  exit 4
fi
/usr/bin/rm -f /tmp/ziv-dpkg-audit.txt
if [[ -s /var/lib/dpkg/triggers/Unincorp ]]; then
  echo "error: dpkg left unincorporated triggers" >&2
  exit 4
fi
if /usr/bin/grep '^Status:' /var/lib/dpkg/status \
  | /usr/bin/grep -Fvxq 'Status: install ok installed'; then
  echo "error: dpkg status contains a non-installed package" >&2
  exit 4
fi
if /usr/bin/grep -Eq '^Triggers-(Pending|Awaited):' /var/lib/dpkg/status; then
  echo "error: dpkg status contains an undrained trigger" >&2
  exit 4
fi
if [[ ! -d /var/lib/dpkg/updates ]]; then
  echo "error: dpkg updates directory is missing" >&2
  exit 4
fi
if /usr/bin/find /var/lib/dpkg/updates -mindepth 1 -print -quit | /usr/bin/grep -q .; then
  echo "error: dpkg left pending database updates" >&2
  exit 4
fi

/usr/bin/printf 'offline APT installation complete: %d packages\n' "${sequence}"
