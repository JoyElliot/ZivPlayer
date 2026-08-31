#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec /usr/bin/env -i \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LC_ALL=C \
  TZ=UTC \
  SOURCE_DATE_EPOCH=946684800 \
  /bin/bash --noprofile --norc "${script_dir}/install-apt-rootfs.bash" "$@"
