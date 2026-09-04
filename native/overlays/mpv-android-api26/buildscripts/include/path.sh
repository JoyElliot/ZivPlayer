#!/bin/bash -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"

if [[ "$(uname -s)" != "Linux" ]]; then
	echo "ZivPlayer native builds require Linux" >&2
	exit 1
fi

# The release build receives this projection as a read-only bind mount. Do not
# fall back to an ambient SDK, NDK, JDK, package manager, or user PATH.
ZIV_TOOLCHAIN_ROOT=/opt/zivplayer/toolchain
export ANDROID_HOME="$ZIV_TOOLCHAIN_ROOT/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_NDK_ROOT="$ANDROID_HOME/ndk/29.0.14206865"
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export HOME=/build/home
export TMPDIR=/build/tmp
export XDG_CACHE_HOME="$HOME/.cache"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_DATA_HOME="$HOME/.local/share"

toolchain_bin="$ANDROID_NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin"
build_tools_bin="$ANDROID_HOME/build-tools/36.0.0"
source_tool_bin="$DIR/sdk/bin"
for required_directory in \
	"$ZIV_TOOLCHAIN_ROOT/bin" \
	"$ANDROID_HOME/platforms/android-36" \
	"$build_tools_bin" \
	"$toolchain_bin" \
	"$JAVA_HOME" \
	"$HOME" \
	"$TMPDIR"; do
	if [[ -L "$required_directory" || ! -d "$required_directory" ]]; then
		echo "locked toolchain path is missing: $required_directory" >&2
		exit 1
	fi
done

gas_preprocessor="$source_tool_bin/gas-preprocessor.pl"
if [[ ! -f "$gas_preprocessor" || -L "$gas_preprocessor" || ! -x "$gas_preprocessor" ]]; then
	echo "locked gas-preprocessor helper is missing or unsafe" >&2
	exit 1
fi
if [[ "$(/usr/bin/sha256sum "$gas_preprocessor")" != \
      "7124d70cdecba7c5612f9a71fbf3f28514dd9c2ca3022f58ad793f88bb925fcf  $gas_preprocessor" ]]; then
	echo "locked gas-preprocessor helper digest differs" >&2
	exit 1
fi

export PATH="$ZIV_TOOLCHAIN_ROOT/bin:$build_tools_bin:$toolchain_bin:$source_tool_bin:/usr/sbin:/usr/bin:/sbin:/bin"
export INSTALL=/usr/bin/install
export SED=sed
export PYTHONHASHSEED=0
export PYTHONDONTWRITEBYTECODE=1
export LC_ALL=C
export LANG=C
export LANGUAGE=C
export TZ=UTC
export SOURCE_DATE_EPOCH=946684800
export ZERO_AR_DATE=1
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
unset http_proxy https_proxy ftp_proxy all_proxy no_proxy
unset HTTP_PROXY HTTPS_PROXY FTP_PROXY ALL_PROXY NO_PROXY
unset CCACHE_DIR CCACHE_PREFIX SCCACHE_DIR RUSTC_WRAPPER
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset LD_PRELOAD LD_LIBRARY_PATH COMPILER_PATH GCC_EXEC_PREFIX
unset PYTHONPATH PYTHONHOME PERL5OPT PERL5LIB RUBYOPT GEM_HOME GEM_PATH
unset MAKEFLAGS MFLAGS CMAKE_PREFIX_PATH CONFIG_SITE ACLOCAL_PATH
unset GIT_CONFIG_SYSTEM GIT_CONFIG_COUNT

# Parallelism is part of the fixed build profile and transcript.
cores=4
umask 022
