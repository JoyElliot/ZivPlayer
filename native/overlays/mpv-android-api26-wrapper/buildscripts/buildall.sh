#!/bin/bash -e

set -o pipefail
cd -P -- "$( dirname "${BASH_SOURCE[0]}" )"
. ./include/depinfo.sh
. ./include/path.sh

for required_variable in \
	ANDROID_NDK_ROOT TMPDIR INSTALL source_tool_bin cores v_ndk_n; do
	if [[ -z "${!required_variable:-}" ]]; then
		echo "required locked build variable is empty: $required_variable" >&2
		exit 1
	fi
done
if [[ "$(pwd -P)" != "/build/source/buildscripts" ]]; then
	echo "wrapper-inclusive build must run from the prepared source workspace" >&2
	exit 1
fi

cleanbuild=0
nodeps=0
target=mpv+zivplayer_mpv

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
	local ndk_properties="$ndk/source.properties"
	local ndk_build="$ndk/ndk-build"
	local ndk_build_impl="$ndk/build/ndk-build"
	local locked_make="$ndk/prebuilt/linux-x86_64/bin/make"

	if [[ "$ndk" != "$expected_ndk" ]]; then
		echo "unexpected Android NDK root: $ndk" >&2
		return 1
	fi
	if [[ -L "$ndk_properties" || ! -f "$ndk_properties" ]]; then
		echo "locked NDK source.properties is missing or unsafe" >&2
		return 1
	fi
	if [[ "$(/usr/bin/stat -c '%a:%u:%g:%h:%s' -- "$ndk_properties")" != "644:0:0:1:107" ]]; then
		echo "locked NDK source.properties metadata differs" >&2
		return 1
	fi
	if [[ "$(/usr/bin/sha256sum "$ndk_properties")" != \
	      "716f3518a923198cfab037abb32dc3f1b1f7e9a9dcdcda6b66be5906215d2658  $ndk_properties" ]]; then
		echo "locked NDK source.properties digest differs" >&2
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
	if [[ ! -f "$ndk_build" || -L "$ndk_build" || ! -x "$ndk_build" ]]; then
		echo "locked NDK ndk-build entry point is missing or unsafe" >&2
		return 1
	fi
	if [[ "$(/usr/bin/stat -c '%a:%u:%g:%h:%s' -- "$ndk_build")" != "755:0:0:1:73" || \
	      "$(/usr/bin/sha256sum "$ndk_build")" != \
	      "4dab4ba20f79dc510ce760110d897d07a89d7389c2af162c416d14133a7102c7  $ndk_build" ]]; then
		echo "locked NDK ndk-build identity differs" >&2
		return 1
	fi
	if [[ ! -f "$ndk_build_impl" || -L "$ndk_build_impl" || ! -x "$ndk_build_impl" ]]; then
		echo "locked NDK ndk-build implementation is missing or unsafe" >&2
		return 1
	fi
	if [[ "$(/usr/bin/stat -c '%a:%u:%g:%h:%s' -- "$ndk_build_impl")" != "755:0:0:1:5295" || \
	      "$(/usr/bin/sha256sum "$ndk_build_impl")" != \
	      "7a163f2a6d06269946e8d061e0ca30a0cb4fe016056f301e937c8e699c75d49e  $ndk_build_impl" ]]; then
		echo "locked NDK ndk-build implementation identity differs" >&2
		return 1
	fi
	if [[ ! -f "$locked_make" || -L "$locked_make" || ! -x "$locked_make" ]]; then
		echo "locked NDK GNU Make is missing or unsafe" >&2
		return 1
	fi
	if [[ "$(/usr/bin/stat -c '%a:%u:%g:%h:%s' -- "$locked_make")" != "755:0:0:1:210728" || \
	      "$(/usr/bin/sha256sum "$locked_make")" != \
	      "ee92765a5dc7556669fd3e7f9fc58c7ba960523d2131052c9b760f5d1a2fc29d  $locked_make" ]]; then
		echo "locked NDK GNU Make identity differs" >&2
		return 1
	fi
	unset GNUMAKE
	export GNUMAKE="$locked_make"
	export PATH="$toolchain/bin:$ndk:$PATH"
}

loadarch () {
	unset CC CXX CPATH LIBRARY_PATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH
	unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS
	unset PKG_CONFIG_PATH
	unset APP_PROJECT_PATH NDK_PROJECT_PATH NDK_APPLICATION_MK APP_BUILD_SCRIPT
	unset APP_ABI APP_CFLAGS APP_CONLYFLAGS APP_CPPFLAGS APP_CXXFLAGS APP_LDFLAGS
	unset APP_ASFLAGS APP_ASMFLAGS APP_CLANG_TIDY APP_CLANG_TIDY_FLAGS
	unset APP_DEBUG APP_DEBUGGABLE APP_MANIFEST APP_STRIP_MODE APP_WRAP_SH
	unset APP_ALLOW_MISSING_DEPS APP_WEAK_API_DEFS
	unset APP_MODULES APP_OPTIM APP_PLATFORM APP_SHORT_COMMANDS APP_STL APP_THIN_ARCHIVE
	unset APP_SUPPORT_FLEXIBLE_PAGE_SIZES
	unset NDK_ALLOW_MISSING_DEPS NDK_ANALYZE NDK_CCACHE NDK_DEBUG NDK_LOG
	unset NDK_ABI_FILTER NDK_DEBUG_IMPORTS NDK_DEBUG_MODULES NDK_DEBUG_TOPO
	unset NDK_GRADLE_INJECTED_IMPORT_PATH NDK_MODULE_PATH NDK_NO_ERRORS NDK_NO_INFO
	unset NDK_NO_USER_WRAP_SH NDK_NO_WARNINGS NDK_SANITIZERS NDK_TOOLCHAIN
	unset NDK_TOOLCHAIN_VERSION
	unset NDK_USE_CYGPATH NDK_WRAP_SH

	local apilvl=26
	local cc_triple
	if [[ "$1" == "arm64" ]]; then
		export ndk_triple=aarch64-linux-android
		cc_triple=$ndk_triple$apilvl
		prefix_name=arm64
		wrapper_abi=arm64-v8a
	elif [[ "$1" == "x86_64" ]]; then
		export ndk_triple=x86_64-linux-android
		cc_triple=$ndk_triple$apilvl
		prefix_name=x86_64
		wrapper_abi=x86_64
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

install_git_stub () {
	local git_stub="$source_tool_bin/git"
	local expected_sha256=c07d6c0d3d6f1bcd8396ab432e050a0578f73e7e644c5c3fd230386c1294cb75
	local metadata

	if [[ -L "$source_tool_bin" || ! -d "$source_tool_bin" ]]; then
		echo "locked source-tool directory is missing or unsafe: $source_tool_bin" >&2
		return 1
	fi
	if [[ "$(/usr/bin/stat -c '%a:%u:%g' -- "$source_tool_bin")" != "755:0:0" ]]; then
		echo "locked source-tool directory metadata differs: $source_tool_bin" >&2
		return 1
	fi
	if [[ -L "$git_stub" || ( -e "$git_stub" && ! -f "$git_stub" ) ]]; then
		echo "locked git stub path is unsafe: $git_stub" >&2
		return 1
	fi
	if [[ ! -e "$git_stub" ]]; then
		(umask 077; printf '#!/bin/sh\nexit 127\n' >"$git_stub")
		chmod 0500 -- "$git_stub"
	fi
	metadata=$(/usr/bin/stat -c '%a:%u:%g:%h:%s' -- "$git_stub")
	if [[ "$metadata" != "500:0:0:1:19" ]]; then
		echo "locked git stub metadata differs: $git_stub" >&2
		return 1
	fi
	if [[ "$(/usr/bin/sha256sum "$git_stub")" != "$expected_sha256  $git_stub" ]]; then
		echo "locked git stub digest differs: $git_stub" >&2
		return 1
	fi
	if [[ "$(command -v git)" != "$git_stub" ]]; then
		echo "an unexpected git executable precedes the locked stub" >&2
		return 1
	fi
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

verify_wrapper_input () {
	local path=$1
	local size=$2
	local digest=$3
	local metadata

	if [[ -L "$path" || ! -f "$path" ]]; then
		echo "wrapper input is missing or unsafe: $path" >&2
		return 1
	fi
	metadata=$(/usr/bin/stat -c '%a:%u:%g:%h:%s' -- "$path")
	if [[ "$metadata" != "444:0:0:1:$size" ]]; then
		echo "wrapper input metadata differs: $path" >&2
		return 1
	fi
	if [[ "$(/usr/bin/sha256sum "$path")" != "$digest  $path" ]]; then
		echo "wrapper input digest differs: $path" >&2
		return 1
	fi
}

verify_wrapper_inputs () {
	verify_wrapper_input /build/wrapper/Android.mk 1360 0dbe6408ea5fa0e4ad21d2ef2efda5ce718f7bcc82a211b0e8d9721ab750f0f8
	verify_wrapper_input /build/wrapper/Application.mk 361 4c7f0bda74ef74b385c877f28508318fe6af060f4cf8a4bbeb561adc8e1800d2
	verify_wrapper_input /build/wrapper/zivplayer_mpv.cpp 40906 50d1d90247ec659c12bac74178d3971c2941697d4b6f398fd29cc4ca57a04179
}

build_wrapper () {
	local project_root=/build/source/app/src/main
	local wrapper_input_root=/build/wrapper
	local android_mk="$wrapper_input_root/Android.mk"
	local application_mk="$wrapper_input_root/Application.mk"
	local wrapper_build_root="$TMPDIR/zivplayer-wrapper-$prefix_name"
	local wrapper_output="$wrapper_build_root/libs/$wrapper_abi/libzivplayer_mpv.so"
	local wrapper_destination="$prefix_dir/lib/libzivplayer_mpv.so"
	local wrapper_destination_parent="$prefix_dir/lib"
	local source_digest
	local destination_digest

	if [[ "$(pwd -P)" != "/build/source/buildscripts" ]]; then
		echo "wrapper build must run from the prepared source workspace" >&2
		return 1
	fi
	verify_wrapper_inputs
	if [[ -L "$wrapper_destination_parent" || ! -d "$wrapper_destination_parent" || \
	      "$(realpath -e -- "$wrapper_destination_parent")" != "$wrapper_destination_parent" ]]; then
		echo "wrapper destination parent is missing or unsafe: $wrapper_destination_parent" >&2
		return 1
	fi
	if [[ -e "$wrapper_build_root" || -L "$wrapper_build_root" ]]; then
		echo "refusing to reuse wrapper build directory: $wrapper_build_root" >&2
		return 1
	fi
	if [[ -e "$wrapper_destination" || -L "$wrapper_destination" ]]; then
		echo "refusing to replace wrapper output: $wrapper_destination" >&2
		return 1
	fi
	mkdir -m 0700 -- "$wrapper_build_root"

	ZIVPLAYER_NATIVE_PREFIX="$prefix_dir" \
	ZIVPLAYER_APP_ABI="$wrapper_abi" \
	"$ANDROID_NDK_ROOT/ndk-build" \
		-C "$project_root" \
		-j"$cores" \
		APP_ABI="$wrapper_abi" \
		APP_BUILD_SCRIPT="$android_mk" \
		APP_MODULES=zivplayer_mpv \
		APP_OPTIM=release \
		APP_PLATFORM=android-26 \
		APP_PROJECT_PATH=null \
		APP_STL=c++_shared \
		APP_SUPPORT_FLEXIBLE_PAGE_SIZES=true \
		NDK_APPLICATION_MK="$application_mk" \
		NDK_DEBUG=0 \
		NDK_LIBS_OUT="$wrapper_build_root/libs" \
		NDK_OUT="$wrapper_build_root/obj" \
		NDK_PROJECT_PATH=null

	if [[ -L "$wrapper_output" || ! -f "$wrapper_output" || ! -s "$wrapper_output" ]]; then
		echo "ndk-build did not produce a regular non-empty wrapper: $wrapper_output" >&2
		return 1
	fi
	if [[ "$(/usr/bin/stat -c '%u:%g:%h' -- "$wrapper_output")" != "0:0:1" ]]; then
		echo "ndk-build wrapper metadata is unsafe: $wrapper_output" >&2
		return 1
	fi
	"$INSTALL" -m 0644 -- "$wrapper_output" "$wrapper_destination"
	if [[ -L "$wrapper_destination" || ! -f "$wrapper_destination" ]]; then
		echo "installed wrapper output is missing or unsafe: $wrapper_destination" >&2
		return 1
	fi
	if [[ "$(/usr/bin/stat -c '%a:%u:%g:%h' -- "$wrapper_destination")" != "644:0:0:1" ]]; then
		echo "installed wrapper output metadata differs: $wrapper_destination" >&2
		return 1
	fi
	source_digest=$(/usr/bin/sha256sum "$wrapper_output")
	source_digest=${source_digest%% *}
	destination_digest=$(/usr/bin/sha256sum "$wrapper_destination")
	destination_digest=${destination_digest%% *}
	if [[ "$source_digest" != "$destination_digest" ]]; then
		echo "installed wrapper output digest differs" >&2
		return 1
	fi
}

if [[ $# -ne 3 || "$1" != "--arch" || "$3" != "$target" ]]; then
	echo "Usage: buildall.sh --arch <arm64|x86_64> mpv+zivplayer_mpv" >&2
	exit 1
fi
arch=$2

loadndk
loadarch "$arch"
verify_wrapper_inputs
install_git_stub
setup_prefix
build mpv
build_wrapper

exit 0
