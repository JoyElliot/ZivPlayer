// SPDX-License-Identifier: GPL-3.0-or-later
@file:androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
package io.github.joyelliot.zivplayer

import androidx.media3.common.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.rules.TestWatcher
import org.junit.runner.Description
import java.io.File

/** 生成样本测试的引擎异常单独保存，避免短小的系统日志环覆盖解码失败原因。 */
class ExoTestLogRule : TestWatcher() {
    private var oldLevel = Log.LOG_LEVEL_ALL
    override fun starting(description: Description) {
        oldLevel = Log.getLogLevel()
        val output = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir,
            "exo-${description.methodName}.log").apply { writeText("") }
        Log.setLogLevel(Log.LOG_LEVEL_ALL)
        Log.setLogger(object : Log.Logger {
            override fun d(tag: String, message: String, throwable: Throwable?) = Log.Logger.DEFAULT.d(tag, message, throwable)
            override fun i(tag: String, message: String, throwable: Throwable?) = Log.Logger.DEFAULT.i(tag, message, throwable)
            override fun w(tag: String, message: String, throwable: Throwable?) { record(tag, message, throwable); Log.Logger.DEFAULT.w(tag, message, throwable) }
            override fun e(tag: String, message: String, throwable: Throwable?) { record(tag, message, throwable); Log.Logger.DEFAULT.e(tag, message, throwable) }
            private fun record(tag: String, message: String, throwable: Throwable?) {
                synchronized(output) { output.appendText("$tag: $message\n${throwable?.stackTraceToString().orEmpty()}\n") }
            }
        })
    }
    override fun finished(description: Description) { Log.setLogger(Log.Logger.DEFAULT); Log.setLogLevel(oldLevel) }
}
