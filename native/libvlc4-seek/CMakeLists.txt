# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.22.1)
project(zivplayer_vlc4 C)
add_library(zivplayer_vlc4 SHARED vlc4_seek.c)
target_compile_features(zivplayer_vlc4 PRIVATE c_std_11)
target_compile_options(zivplayer_vlc4 PRIVATE -Wall -Wextra -Werror -fvisibility=hidden)
target_link_options(zivplayer_vlc4 PRIVATE -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384)
target_link_libraries(zivplayer_vlc4 PRIVATE dl)
