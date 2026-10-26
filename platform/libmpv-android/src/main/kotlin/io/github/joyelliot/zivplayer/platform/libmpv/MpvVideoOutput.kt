// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

/** Explicit renderer selection; benchmarks must also verify mpv's current-vo. */
enum class MpvVideoOutput(val optionValue: String) {
    GPU_NEXT("gpu-next"),
    GPU("gpu"),
}
