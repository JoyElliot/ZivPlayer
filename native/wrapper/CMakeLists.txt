# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.22.1)
project(zivplayer_mpv LANGUAGES CXX)

if(NOT ANDROID)
    message(FATAL_ERROR "The ZivPlayer mpv wrapper is Android-only.")
endif()
if(NOT DEFINED CMAKE_ANDROID_NDK OR CMAKE_ANDROID_NDK STREQUAL "")
    message(FATAL_ERROR "CMAKE_ANDROID_NDK must identify Android NDK 29.0.14206865.")
endif()
set(zivplayer_ndk_properties "${CMAKE_ANDROID_NDK}/source.properties")
if(
    NOT EXISTS "${zivplayer_ndk_properties}"
    OR IS_DIRECTORY "${zivplayer_ndk_properties}"
    OR IS_SYMLINK "${zivplayer_ndk_properties}"
)
    message(FATAL_ERROR "The selected Android NDK has no regular source.properties file.")
endif()
file(
    STRINGS "${zivplayer_ndk_properties}" zivplayer_ndk_revision_lines
    REGEX "^[ \t]*Pkg\\.Revision[ \t]*="
)
list(LENGTH zivplayer_ndk_revision_lines zivplayer_ndk_revision_count)
if(NOT zivplayer_ndk_revision_count EQUAL 1)
    message(FATAL_ERROR "The selected Android NDK has an ambiguous Pkg.Revision.")
endif()
list(GET zivplayer_ndk_revision_lines 0 zivplayer_ndk_revision_line)
if(
    NOT zivplayer_ndk_revision_line
        MATCHES "^[ \t]*Pkg\\.Revision[ \t]*=[ \t]*29\\.0\\.14206865[ \t]*$"
)
    message(FATAL_ERROR "The ZivPlayer mpv wrapper requires Android NDK 29.0.14206865.")
endif()
if(NOT CMAKE_SYSTEM_VERSION STREQUAL "26")
    message(FATAL_ERROR "The ZivPlayer mpv wrapper requires native API 26.")
endif()
if(NOT CMAKE_ANDROID_ARCH_ABI MATCHES "^(arm64-v8a|x86_64)$")
    message(FATAL_ERROR "The ZivPlayer mpv wrapper supports arm64-v8a and x86_64 only.")
endif()
if(NOT CMAKE_ANDROID_STL_TYPE STREQUAL "c++_shared")
    message(FATAL_ERROR "Configure the ZivPlayer mpv wrapper with ANDROID_STL=c++_shared.")
endif()
if(NOT CMAKE_BUILD_TYPE STREQUAL "Release")
    message(FATAL_ERROR "The ZivPlayer mpv wrapper requires a Release build.")
endif()
if(NOT DEFINED ZIVPLAYER_NATIVE_PREFIX OR ZIVPLAYER_NATIVE_PREFIX STREQUAL "")
    message(FATAL_ERROR "ZIVPLAYER_NATIVE_PREFIX must point at the audited native prefix.")
endif()
if(NOT IS_ABSOLUTE "${ZIVPLAYER_NATIVE_PREFIX}")
    message(FATAL_ERROR "ZIVPLAYER_NATIVE_PREFIX must be an absolute path.")
endif()
foreach(required_path IN ITEMS
    "${ZIVPLAYER_NATIVE_PREFIX}/include/mpv/client.h"
    "${ZIVPLAYER_NATIVE_PREFIX}/include/libavcodec/jni.h"
    "${ZIVPLAYER_NATIVE_PREFIX}/lib/libmpv.so"
    "${ZIVPLAYER_NATIVE_PREFIX}/lib/libavcodec.so"
)
    if(
        NOT EXISTS "${required_path}"
        OR IS_DIRECTORY "${required_path}"
        OR IS_SYMLINK "${required_path}"
    )
        message(FATAL_ERROR "The native prefix path is missing, a directory, or a symlink: ${required_path}.")
    endif()
endforeach()

add_library(zivplayer_imported_mpv SHARED IMPORTED)
set_target_properties(
    zivplayer_imported_mpv
    PROPERTIES
        IMPORTED_LOCATION "${ZIVPLAYER_NATIVE_PREFIX}/lib/libmpv.so"
        IMPORTED_SONAME "libmpv.so"
)

add_library(zivplayer_imported_avcodec SHARED IMPORTED)
set_target_properties(
    zivplayer_imported_avcodec
    PROPERTIES
        IMPORTED_LOCATION "${ZIVPLAYER_NATIVE_PREFIX}/lib/libavcodec.so"
        IMPORTED_SONAME "libavcodec.so"
)

add_library(zivplayer_mpv SHARED zivplayer_mpv.cpp)
target_compile_features(zivplayer_mpv PRIVATE cxx_std_11)
target_compile_options(
    zivplayer_mpv
    PRIVATE
        -fvisibility=hidden
        -fvisibility-inlines-hidden
        -fexceptions
        -Wall
        -Wextra
        -Werror
)
target_include_directories(
    zivplayer_mpv
    PRIVATE "${ZIVPLAYER_NATIVE_PREFIX}/include"
)
target_link_libraries(
    zivplayer_mpv
    PRIVATE zivplayer_imported_mpv zivplayer_imported_avcodec
)
target_link_options(
    zivplayer_mpv
    PRIVATE
        -Wl,--no-undefined
        -Wl,-soname,libzivplayer_mpv.so
        -Wl,-z,max-page-size=16384
        -Wl,-z,common-page-size=16384
)
set_target_properties(
    zivplayer_mpv
    PROPERTIES
        CXX_EXTENSIONS OFF
        OUTPUT_NAME zivplayer_mpv
        PREFIX lib
        SUFFIX .so
        RELEASE_POSTFIX ""
)
