// SPDX-License-Identifier: GPL-3.0-or-later
package io.github.joyelliot.zivplayer

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.MediaCodecList
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/** Opt-in, read-only discovery; charging samples are never labelled as playback power. */
class PowerTelemetryDeviceTest {
    @Test fun inspectAvailableCountersAndDecoders() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue(InstrumentationRegistry.getArguments().getString("powerProbe") == "true")
        val context = instrumentation.targetContext
        val report = JSONObject().put("model", Build.MODEL).put("sdk", Build.VERSION.SDK_INT)
        val samples = JSONArray()
        repeat(8) {
            samples.put(batterySample(context))
            SystemClock.sleep(1000)
        }
        report.put("samples", samples)
        val decoders = JSONArray()
        val requested = setOf("video/avc", "video/hevc", "video/x-vnd.on2.vp9", "video/av01", "audio/mp4a-latm", "audio/flac")
        MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos.filterNot { it.isEncoder }.forEach { codec ->
            codec.supportedTypes.filter { it in requested }.forEach { mime ->
                val row = JSONObject().put("name", codec.name).put("mime", mime)
                if (Build.VERSION.SDK_INT >= 29) {
                    row.put("hardwareAccelerated", codec.isHardwareAccelerated)
                        .put("softwareOnly", codec.isSoftwareOnly).put("vendor", codec.isVendor)
                }
                runCatching {
                    codec.getCapabilitiesForType(mime).videoCapabilities?.let { video ->
                        row.put("supports1080p24", video.areSizeAndRateSupported(1920, 1080, 24.0))
                        row.put("supports720p24", video.areSizeAndRateSupported(1280, 720, 24.0))
                    }
                }.onFailure { row.put("capabilityError", it.javaClass.simpleName) }
                decoders.put(row)
            }
        }
        report.put("decoders", decoders)
        File(context.cacheDir, "power-telemetry-probe.json").writeText(report.toString(2))
    }

    companion object {
        fun batterySample(context: Context): JSONObject {
            val battery = context.getSystemService(BatteryManager::class.java)
            val state = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val power = context.getSystemService(PowerManager::class.java)
            return JSONObject().put("elapsedMs", SystemClock.elapsedRealtime())
                .put("wallTimeMs", System.currentTimeMillis()).put("processCpuMs", Process.getElapsedCpuTime())
                .put("currentNowUa", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW))
                .put("currentAverageUa", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_AVERAGE))
                .put("chargeCounterRaw", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER))
                .put("energyCounterRaw", battery.getLongProperty(BatteryManager.BATTERY_PROPERTY_ENERGY_COUNTER))
                .put("capacityPercent", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
                .put("charging", battery.isCharging)
                .put("plugged", state?.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1))
                .put("batteryStatus", state?.getIntExtra(BatteryManager.EXTRA_STATUS, -1))
                .put("voltageMv", state?.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1))
                .put("temperatureTenthsC", state?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1))
                .put("thermalStatus", if (Build.VERSION.SDK_INT >= 29) power.currentThermalStatus else -1)
        }
    }
}
