# SPDX-License-Identifier: GPL-3.0-or-later

LOCAL_PATH := $(call my-dir)

ifeq ($(TARGET_ARCH_ABI),arm64-v8a)
zivplayer_expected_prefix := /build/source/buildscripts/prefix/arm64
else ifeq ($(TARGET_ARCH_ABI),x86_64)
zivplayer_expected_prefix := /build/source/buildscripts/prefix/x86_64
else
$(error ZivPlayer wrapper supports only arm64-v8a and x86_64)
endif

ifneq ($(strip $(ZIVPLAYER_NATIVE_PREFIX)),$(zivplayer_expected_prefix))
$(error ZIVPLAYER_NATIVE_PREFIX does not match TARGET_ARCH_ABI)
endif

include $(CLEAR_VARS)
LOCAL_MODULE := mpv
LOCAL_SRC_FILES := $(ZIVPLAYER_NATIVE_PREFIX)/lib/libmpv.so
LOCAL_EXPORT_C_INCLUDES := $(ZIVPLAYER_NATIVE_PREFIX)/include
include $(PREBUILT_SHARED_LIBRARY)

include $(CLEAR_VARS)
LOCAL_MODULE := avcodec
LOCAL_SRC_FILES := $(ZIVPLAYER_NATIVE_PREFIX)/lib/libavcodec.so
LOCAL_EXPORT_C_INCLUDES := $(ZIVPLAYER_NATIVE_PREFIX)/include
include $(PREBUILT_SHARED_LIBRARY)

include $(CLEAR_VARS)
LOCAL_MODULE := zivplayer_mpv
LOCAL_SRC_FILES := zivplayer_mpv.cpp
LOCAL_CPP_FEATURES := exceptions
LOCAL_CPPFLAGS := \
	-std=c++11 \
	-fvisibility=hidden \
	-fvisibility-inlines-hidden \
	-Wall \
	-Wextra \
	-Werror
LOCAL_LDFLAGS := \
	-Wl,--no-undefined \
	-Wl,-soname,libzivplayer_mpv.so \
	-Wl,-z,max-page-size=16384 \
	-Wl,-z,common-page-size=16384
LOCAL_SHARED_LIBRARIES := mpv avcodec
include $(BUILD_SHARED_LIBRARY)
