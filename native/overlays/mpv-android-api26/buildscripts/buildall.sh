#!/bin/bash -e

set -o pipefail
cd "$( dirname "${BASH_SOURCE[0]}" )"
. ./include/depinfo.sh
. ./include/path.sh

cleanbuild=0
nodeps=0
target=mpv

getdeps () {
	varname="dep_${1//-/_}[*]"
	echo ${!varname}
}

wasbuilt () {
	# Do not use associative arrays: the upstream dependency walker intentionally
	# stays compatible with the shell subset used by the reviewed baseline.
	varname="built_${1//-/_}"
	return "${!varname:-1}"
}

markbuilt () {
	varname="built_${1//-/_}"
	declare -g "$varname=0"
}

loadndk () {
	local ndk="$ANDROID_NDK_ROOT"
	local expected_ndk="/opt/zivplayer/toolchain/android-sdk/ndk/${v_ndk_n}"
	local toolchain="$ndk/toolchains/llvm/prebuilt/linux-x86_64"

	if [[ "$ndk" != "$expected_ndk" ]]; then
		echo "unexpected Android NDK root: $ndk" >&2
		return 1
	fi
	local locked_tool
	for locked_tool in \
		aarch64-linux-android26-clang \
		aarch64-linux-android26-clang++ \
		x86_64-linux-android26-clang \
		x86_64-linux-android26-clang++ \
		llvm-ar llvm-nm llvm-ranlib llvm-strip; do
		if [[ ! -x "$toolchain/bin/$locked_tool" ]]; then
			echo "locked NDK tool is missing: $toolchain/bin/$locked_tool" >&2
			return 1
		fi
	done
	export PATH="$toolchain/bin:$ndk:$PATH"
}

loadarch () {
	unset CC CXX CPATH LIBRARY_PATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH
	unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS
	unset PKG_CONFIG_PATH

	local apilvl=26
	local cc_triple
	if [[ "$1" == "arm64" ]]; then
		export ndk_triple=aarch64-linux-android
		cc_triple=$ndk_triple$apilvl
		prefix_name=arm64
	elif [[ "$1" == "x86_64" ]]; then
		export ndk_triple=x86_64-linux-android
		cc_triple=$ndk_triple$apilvl
		prefix_name=x86_64
	else
		echo "unsupported ZivPlayer architecture: $1" >&2
		exit 1
	fi
	export ndk_suffix=_$prefix_name
	export prefix_dir="$PWD/prefix/$prefix_name"
	export CC=$cc_triple-clang
	export CXX=$cc_triple-clang++
	export LDFLAGS="-Wl,-O1,--icf=safe -Wl,-z,max-page-size=16384"
	export AR=llvm-ar
	export RANLIB=llvm-ranlib

	if ! command -v pkg-config >/dev/null; then
		echo "pkg-config is missing" >&2
		return 1
	fi
	export PKG_CONFIG_SYSROOT_DIR="$prefix_dir"
	export PKG_CONFIG_LIBDIR="$PKG_CONFIG_SYSROOT_DIR/lib/pkgconfig"
}

setup_prefix () {
	local prefix_root="$PWD/prefix"
	if [[ -L "$prefix_root" || ( -e "$prefix_root" && ! -d "$prefix_root" ) ]]; then
		echo "native prefix root is not a regular directory: $prefix_root" >&2
		return 1
	fi
	if [[ ! -d "$prefix_root" ]]; then
		mkdir -- "$prefix_root"
	fi
	if [[ -e "$prefix_dir" || -L "$prefix_dir" ]]; then
		echo "refusing to reuse native prefix: $prefix_dir" >&2
		return 1
	fi
	mkdir -- "$prefix_dir"
	# Enforce the upstream flat DESTDIR layout (/usr/local -> /).
	ln -s . "$prefix_dir/usr"
	ln -s . "$prefix_dir/local"

	local cpu_family=${ndk_triple%%-*}
	local crossfile="$prefix_dir/crossfile.txt"

	cat >"$crossfile" <<CROSSFILE
[built-in options]
buildtype = 'release'
default_library = 'static'
wrap_mode = 'nodownload'
prefix = '/usr/local'
c_link_args = ['-Wl,-O1,--icf=safe', '-Wl,-z,max-page-size=16384']
cpp_link_args = ['-Wl,-O1,--icf=safe', '-Wl,-z,max-page-size=16384']
[binaries]
c = '$CC'
cpp = '$CXX'
ar = 'llvm-ar'
nm = 'llvm-nm'
strip = 'llvm-strip'
pkgconfig = 'pkg-config'
pkg-config = 'pkg-config'
[host_machine]
system = 'android'
cpu_family = '$cpu_family'
cpu = '${CC%%-*}'
endian = 'little'
CROSSFILE
}

build () {
	if [[ $1 != "mpv-android" && ! -d deps/$1 ]]; then
		printf >&2 '\e[1;31m%s\e[m\n' "Target $1 not found"
		return 1
	fi
	wasbuilt "$1" && return 0
	if [[ $nodeps -eq 0 ]]; then
		printf >&2 '\e[1;34m%s\e[m\n' "Preparing $1..."
		local deps
		deps=$(getdeps "$1")
		echo >&2 "Dependencies: $deps"
		for dep in $deps; do
			build "$dep"
		done
	fi
	printf >&2 '\e[1;34m%s\e[m\n' "Building $1..."
	if [[ "$1" == "mpv-android" ]]; then
		pushd ..
		BUILDSCRIPT=buildscripts/scripts/$1.sh
	else
		pushd deps/$1
		BUILDSCRIPT=../../scripts/$1.sh
	fi
	[[ $cleanbuild -eq 1 ]] && "$BUILDSCRIPT" clean
	"$BUILDSCRIPT" build
	popd
	markbuilt "$1"
}

if [[ $# -ne 3 || "$1" != "--arch" || "$3" != "$target" ]]; then
	echo "Usage: buildall.sh --arch <arm64|x86_64> mpv" >&2
	exit 1
fi
arch=$2

loadndk
loadarch "$arch"
setup_prefix
build "$target"

exit 0
