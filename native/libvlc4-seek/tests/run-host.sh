#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Run from repository root; argument is an isolated output directory containing the NDK jni.h.
set -eu
out=${1:?output directory with jni.h required}
cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -fPIC -shared \
  native/libvlc4-seek/tests/mock_vlc.c -o "$out/libvlc.so"
cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -I"$out" \
  native/libvlc4-seek/tests/bridge_test.c -L"$out" -lvlc -ldl -pthread -o "$out/bridge-test"
LD_LIBRARY_PATH="$out" "$out/bridge-test"
