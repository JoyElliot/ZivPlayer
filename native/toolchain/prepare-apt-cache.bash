#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
if [[ "${PATH:-}" != "/usr/bin:/bin" || "${LC_ALL:-}" != "C" || "${TZ:-}" != "UTC" ]] \
  || [[ -n "${BASH_ENV+x}${ENV+x}${PYTHONPATH+x}${PYTHONHOME+x}${TAR_OPTIONS+x}" ]] \
  || [[ -n "${DPKG_ROOT+x}${DPKG_ADMINDIR+x}${APT_CONFIG+x}${GNUPGHOME+x}${GPG_AGENT_INFO+x}" ]] \
  || [[ -n "${LD_PRELOAD+x}${LD_LIBRARY_PATH+x}${LD_AUDIT+x}${PYTHONWARNINGS+x}${PERL5OPT+x}" ]] \
  || [[ -n "${http_proxy+x}${https_proxy+x}${HTTP_PROXY+x}${HTTPS_PROXY+x}${ALL_PROXY+x}${NO_PROXY+x}" ]]; then
  echo "error: refusing an unsanitized APT preparation implementation invocation" >&2
  exit 2
fi

set -euo pipefail
umask 077

if [[ "$#" -ne 0 ]]; then
  echo "error: prepare-apt-cache.sh does not accept arguments" >&2
  exit 2
fi
if [[ "$(/usr/bin/uname -s)" != "Linux" || "$(/usr/bin/dpkg --print-architecture)" != "amd64" ]]; then
  echo "error: APT cache preparation requires Linux/amd64" >&2
  exit 2
fi
if [[ "${EUID}" -ne 0 ]]; then
  echo "error: APT cache preparation must run as root" >&2
  exit 2
fi

readonly expected_apt_version="2.8.3"
readonly expected_dpkg_version="1.22.6ubuntu6.6"
readonly expected_gpgv_version="2.4.4-2ubuntu17.4"
readonly expected_status_size="87191"
readonly expected_status_sha256="e198b5ed0a64a090abce7b39481dd402ed0dc89d6c1ba96c448f4ba6ea75b6ed"
readonly expected_keyring_size="3607"
readonly expected_keyring_sha256="80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31"
readonly expected_signer_fingerprint="f6ecb3762474eda9d21b7022871920d1991bc93c"
readonly expected_layer_size="29752807"
readonly expected_layer_sha256="0926a8eb0e608a5c6888d1cd5594184bdf3ed3aa311dba5b42a547caefdc6f2e"
readonly expected_sources_size="195"
readonly expected_sources_sha256="58d823a08ee0649fa9a9f75d6f4f761cd0ecf5fa39b59bd0a15fb2c17326a6d8"
readonly expected_roots_size="196"
readonly expected_roots_sha256="d1b09501bcc6e988af7dae2e81e16ed56a138e1eb625a6dd823cc017bc7f35fa"

assert_package_version() {
  local package="$1"
  local expected="$2"
  local actual
  actual="$(/usr/bin/dpkg-query -W -f='${Version}' -- "${package}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "error: ${package} version must be ${expected}, got ${actual}" >&2
    exit 2
  fi
}

assert_file_digest() {
  local path="$1"
  local expected_size="$2"
  local expected_sha256="$3"
  local label="$4"
  local actual_size actual_sha256
  if [[ ! -f "${path}" || -L "${path}" ]]; then
    echo "error: ${label} must be a regular non-symlink file: ${path}" >&2
    exit 4
  fi
  actual_size="$(/usr/bin/stat -c '%s' -- "${path}")"
  actual_sha256="$(/usr/bin/sha256sum -- "${path}")"
  actual_sha256="${actual_sha256%% *}"
  if [[ "${actual_size}" != "${expected_size}" || "${actual_sha256}" != "${expected_sha256}" ]]; then
    echo "error: ${label} does not match its locked size/SHA-256" >&2
    exit 4
  fi
}

assert_package_version apt "${expected_apt_version}"
assert_package_version dpkg "${expected_dpkg_version}"
assert_package_version gpgv "${expected_gpgv_version}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_dir}/../.." && pwd -P)"
cache_parent="${repository_root}/native/cache/toolchain"
layer="${cache_parent}/ubuntu-noble-20260810-amd64-rootfs.tar.gz"
sources_template="${script_dir}/ubuntu.sources.in"
roots_file="${script_dir}/apt-roots.txt"
final_cache="${cache_parent}/apt"

/usr/bin/python3 "${repository_root}/native/tools/toolchain_tool.py" verify-roots
if [[ -e "${final_cache}" || -L "${final_cache}" ]]; then
  echo "error: refusing to replace existing APT cache: ${final_cache}" >&2
  exit 4
fi

state="$(/usr/bin/mktemp -d /tmp/ziv-toolchain-apt.XXXXXXXX)"
staging="$(/usr/bin/mktemp -d "${cache_parent}/.apt.XXXXXXXX.part")"
cleanup() {
  local candidate resolved
  for candidate in "${state:-}" "${staging:-}"; do
    [[ -n "${candidate}" && ( -e "${candidate}" || -L "${candidate}" ) ]] || continue
    resolved="$(/usr/bin/readlink -f -- "${candidate}")"
    if [[ "${candidate}" == "${state:-}" && "${resolved}" == /tmp/ziv-toolchain-apt.* ]]; then
      /usr/bin/rm -rf -- "${resolved}"
    elif [[ "${candidate}" == "${staging:-}" && "${resolved}" == "${cache_parent}"/.apt.*.part ]]; then
      /usr/bin/rm -rf -- "${resolved}"
    else
      echo "error: refusing to remove unexpected temporary path: ${resolved}" >&2
      return 1
    fi
  done
}
trap cleanup EXIT

/usr/bin/install -d -m 0755 -- \
  "${state}/home" "${state}/tmp" "${state}/lists/partial" "${state}/log" "${state}/cache" \
  "${staging}/debs/partial" "${staging}/indices"

/usr/bin/cp --reflink=auto -- "${layer}" "${state}/locked-layer.tar.gz"
/usr/bin/cp --reflink=auto -- "${sources_template}" "${state}/ubuntu.sources.in"
/usr/bin/cp --reflink=auto -- "${roots_file}" "${state}/apt-roots.txt"
assert_file_digest "${state}/locked-layer.tar.gz" "${expected_layer_size}" "${expected_layer_sha256}" "OCI rootfs layer snapshot"
assert_file_digest "${state}/ubuntu.sources.in" "${expected_sources_size}" "${expected_sources_sha256}" "Ubuntu sources template snapshot"
assert_file_digest "${state}/apt-roots.txt" "${expected_roots_size}" "${expected_roots_sha256}" "APT roots snapshot"
layer="${state}/locked-layer.tar.gz"
sources_template="${state}/ubuntu.sources.in"
roots_file="${state}/apt-roots.txt"

status_member_count="$(/usr/bin/tar -tzf "${layer}" | /usr/bin/awk '$0 == "var/lib/dpkg/status" { count++ } END { print count + 0 }')"
keyring_member_count="$(/usr/bin/tar -tzf "${layer}" | /usr/bin/awk '$0 == "usr/share/keyrings/ubuntu-archive-keyring.gpg" { count++ } END { print count + 0 }')"
if [[ "${status_member_count}" != "1" || "${keyring_member_count}" != "1" ]]; then
  echo "error: locked OCI layer must contain exactly one dpkg status and Ubuntu keyring member" >&2
  exit 4
fi
/usr/bin/tar -xOzf "${layer}" var/lib/dpkg/status > "${state}/status"
/usr/bin/tar -xOzf "${layer}" usr/share/keyrings/ubuntu-archive-keyring.gpg \
  > "${state}/ubuntu-archive-keyring.gpg"
assert_file_digest "${state}/status" "${expected_status_size}" "${expected_status_sha256}" "base dpkg status"
assert_file_digest "${state}/ubuntu-archive-keyring.gpg" "${expected_keyring_size}" "${expected_keyring_sha256}" "Ubuntu archive keyring"

token_count="$(/usr/bin/grep -o '@UBUNTU_ARCHIVE_KEYRING@' "${sources_template}" | /usr/bin/wc -l)"
if [[ "${token_count}" != "1" ]]; then
  echo "error: ubuntu.sources.in must contain exactly one keyring token" >&2
  exit 2
fi
/usr/bin/sed "s|@UBUNTU_ARCHIVE_KEYRING@|${state}/ubuntu-archive-keyring.gpg|" \
  "${sources_template}" > "${state}/ubuntu.sources"

mapfile -t roots < <(/usr/bin/sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "${roots_file}")
if [[ "${#roots[@]}" -eq 0 ]]; then
  echo "error: no APT roots were declared" >&2
  exit 2
fi
previous_root=""
for root in "${roots[@]}"; do
  if [[ ! "${root}" =~ ^[a-z0-9][a-z0-9+.-]*$ ]]; then
    echo "error: invalid APT root name: ${root}" >&2
    exit 2
  fi
  if [[ -n "${previous_root}" && ! "${previous_root}" < "${root}" ]]; then
    echo "error: APT roots must be strictly sorted and unique" >&2
    exit 2
  fi
  previous_root="${root}"
done

{
  echo 'Dir::Etc::main "/dev/null";'
  echo 'Dir::Etc::parts "-";'
  echo 'APT::Update::Pre-Invoke "";'
  echo 'APT::Update::Post-Invoke "";'
  echo 'APT::Update::Post-Invoke-Success "";'
  echo 'DPkg::Pre-Invoke "";'
  echo 'DPkg::Post-Invoke "";'
  echo '#clear DPkg::Pre-Install-Pkgs;'
  echo '#clear APT::Architectures;'
  echo 'APT::Architectures:: "amd64";'
} > "${state}/apt.conf"

apt_options=(
  -o "Dir::State::status=${state}/status"
  -o "Dir::State::extended_states=${state}/extended_states"
  -o "Dir::State::lists=${state}/lists"
  -o "Dir::Cache=${state}/cache"
  -o "Dir::Cache::archives=${staging}/debs"
  -o "Dir::Cache::pkgcache="
  -o "Dir::Cache::srcpkgcache="
  -o "Dir::Log=${state}/log"
  -o "Dir::Etc::sourcelist=${state}/ubuntu.sources"
  -o "Dir::Etc::sourceparts=-"
  -o "Dir::Etc::trusted=/dev/null"
  -o "Dir::Etc::trustedparts=-"
  -o "Dir::Etc::preferences=/dev/null"
  -o "Dir::Etc::preferencesparts=-"
  -o "APT::Architecture=amd64"
  -o "APT::Get::List-Cleanup=0"
  -o "Acquire::Languages=none"
  -o "Acquire::Retries=0"
  -o "Acquire::http::Proxy=DIRECT"
  -o "Acquire::https::Proxy=DIRECT"
  -o "Acquire::https::Verify-Peer=true"
  -o "Acquire::https::Verify-Host=true"
  -o "Acquire::Check-Date=true"
  -o "Acquire::Check-Valid-Until=true"
  -o "Acquire::By-Hash=true"
  -o "Acquire::AllowWeakRepositories=false"
  -o "Acquire::AllowInsecureRepositories=false"
  -o "Acquire::AllowDowngradeToInsecureRepositories=false"
  -o "APT::Get::AllowUnauthenticated=false"
  -o "Debug::NoLocking=1"
)

run_apt() {
  /usr/bin/env -i \
    PATH=/usr/bin:/bin \
    LC_ALL=C \
    TZ=UTC \
    HOME="${state}/home" \
    TMPDIR="${state}/tmp" \
    APT_CONFIG="${state}/apt.conf" \
    /usr/bin/apt-get "${apt_options[@]}" "$@"
}

run_apt update
run_apt --simulate --no-install-recommends install "${roots[@]}" \
  > "${staging}/simulation.txt"
run_apt --download-only --no-install-recommends --yes install "${roots[@]}"

for suite in noble noble-updates noble-security; do
  inrelease_source="${state}/lists/snapshot.ubuntu.com_ubuntu_20260811T000000Z_dists_${suite}_InRelease"
  inrelease_target="${staging}/indices/${suite}.InRelease"
  if [[ ! -f "${inrelease_source}" || -L "${inrelease_source}" ]]; then
    echo "error: missing regular InRelease for ${suite}" >&2
    exit 4
  fi
  gpgv_status="$(/usr/bin/gpgv --homedir "${state}/home" --status-fd=1 \
    --keyring "${state}/ubuntu-archive-keyring.gpg" \
    "${inrelease_source}" 2> "${state}/${suite}.gpgv.log")"
  mapfile -t signer_fingerprints < <(
    printf '%s\n' "${gpgv_status}" \
      | /usr/bin/awk '$2 == "VALIDSIG" { print tolower($3) }'
  )
  if [[ "${#signer_fingerprints[@]}" -ne 1 || "${signer_fingerprints[0]}" != "${expected_signer_fingerprint}" ]]; then
    echo "error: ${suite} InRelease signer does not match the locked fingerprint" >&2
    exit 4
  fi
  /usr/bin/install -m 0644 -- "${inrelease_source}" "${inrelease_target}"

  for component in main universe; do
    packages_source="${state}/lists/snapshot.ubuntu.com_ubuntu_20260811T000000Z_dists_${suite}_${component}_binary-amd64_Packages"
    packages_target="${staging}/indices/${suite}-${component}-amd64.Packages"
    if [[ ! -f "${packages_source}" || -L "${packages_source}" ]]; then
      echo "error: missing regular Packages index for ${suite}/${component}" >&2
      exit 4
    fi
    read -r published_sha256 published_size < <(
      /usr/bin/awk -v wanted="${component}/binary-amd64/Packages" '
        $0 == "SHA256:" { in_sha256 = 1; next }
        in_sha256 && $1 == "-----BEGIN" { exit }
        in_sha256 && $3 == wanted { print $1, $2; exit }
      ' "${inrelease_source}"
    )
    if [[ -z "${published_sha256:-}" || -z "${published_size:-}" ]]; then
      echo "error: ${suite} InRelease does not publish ${component}/binary-amd64/Packages" >&2
      exit 4
    fi
    assert_file_digest "${packages_source}" "${published_size}" "${published_sha256}" \
      "${suite}/${component} Packages"
    /usr/bin/install -m 0644 -- "${packages_source}" "${packages_target}"
  done
done

/usr/bin/rm -f -- "${staging}/debs/lock"
if /usr/bin/find "${staging}/debs/partial" -mindepth 1 -print -quit | /usr/bin/grep -q .; then
  echo "error: APT left unexpected partial downloads" >&2
  exit 4
fi
/usr/bin/rmdir -- "${staging}/debs/partial"
deb_count="$(/usr/bin/find "${staging}/debs" -maxdepth 1 -type f -name '*.deb' -printf '.' | /usr/bin/wc -c)"
if [[ "${deb_count}" != "102" ]]; then
  echo "error: expected 102 downloaded .deb files, got ${deb_count}" >&2
  exit 4
fi

(
  cd -- "${staging}"
  /usr/bin/find debs indices -type f -printf '%p\0' \
    | /usr/bin/sort -z \
    | /usr/bin/xargs -0 /usr/bin/sha256sum
) > "${staging}/SHA256SUMS"

/usr/bin/python3 "${repository_root}/native/tools/toolchain_tool.py" \
  verify-apt-cache --apt-cache "${staging}"
/usr/bin/python3 "${repository_root}/native/tools/toolchain_tool.py" verify-roots
if ! /usr/bin/mv -Tn -- "${staging}" "${final_cache}"; then
  echo "error: cannot atomically publish the APT cache" >&2
  exit 4
fi
if [[ -e "${staging}" || -L "${staging}" ]]; then
  echo "error: refusing to replace an APT cache published by another process" >&2
  exit 4
fi
staging=""
echo "APT preparation cache ready: ${final_cache}"
