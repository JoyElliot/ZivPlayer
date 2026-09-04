# SPDX-License-Identifier: GPL-3.0-or-later

ifeq ($(ZIVPLAYER_APP_ABI),arm64-v8a)
else ifeq ($(ZIVPLAYER_APP_ABI),x86_64)
else
$(error ZIVPLAYER_APP_ABI must be arm64-v8a or x86_64)
endif

APP_ABI := $(ZIVPLAYER_APP_ABI)
APP_MODULES := zivplayer_mpv
APP_PLATFORM := android-26
APP_STL := c++_shared
APP_OPTIM := release
APP_SUPPORT_FLEXIBLE_PAGE_SIZES := true
