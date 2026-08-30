#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec /usr/bin/env -i \
  PATH=/usr/bin:/bin \
  LC_ALL=C \
  TZ=UTC \
  /bin/bash --noprofile --norc "${script_dir}/prepare-apt-cache.bash" "$@"
