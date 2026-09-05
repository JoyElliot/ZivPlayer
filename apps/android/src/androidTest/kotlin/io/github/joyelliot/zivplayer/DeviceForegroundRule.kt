// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer

import androidx.test.platform.app.InstrumentationRegistry
import android.os.ParcelFileDescriptor
import org.junit.rules.ExternalResource

/** Give ActivityScenario a visible app on devices which restrict instrumented background launches. */
class DeviceForegroundRule : ExternalResource() {
    override fun before() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        // Start the separate fixture process after fresh installation, including OEM cold-start gates.
        val fixture = shell("content query --uri content://io.github.joyelliot.zivplayer.test.fixtures/tracks-subtitles.mkv")
        check("_display_name=tracks-subtitles.mkv" in fixture) { "Fixture process did not start: $fixture" }
        startActivity("${instrumentation.targetContext.packageName}/.MainActivity")
    }

    companion object {
        fun startActivity(component: String) {
            val output = shell("am start -W -f 0x00020000 -n $component")
            check("Status: ok" in output) { "The device did not foreground the test activity: $output" }
        }

        private fun shell(command: String): String {
            val descriptor = InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand(command)
            return ParcelFileDescriptor.AutoCloseInputStream(descriptor).bufferedReader().use { it.readText() }
        }
    }
}
