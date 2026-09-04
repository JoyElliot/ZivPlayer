// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.util.Log
import android.view.Surface
import dev.jdtech.mpv.MPVLib
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Temporary adapter around the locked development-only Maven AAR.
 *
 * The AAR cannot report most command/property/Surface failures or event
 * payloads. This class deliberately contains that loss so the rest of the
 * Android adapter can move to the source-built client contract first.
 */
internal class BootstrapMpvClient private constructor(
    private val delegate: MPVLib,
) : MpvClient {
    private val observers = CopyOnWriteArraySet<MpvClient.Observer>()
    private val destroyed = AtomicBoolean(false)
    private val destroyMonitor = Any()

    private var destroyAttempted = false
    private var terminalDestroyFailure: Throwable? = null

    private val delegateObserver = object : MPVLib.EventObserver {
        override fun eventProperty(property: String) {
            emitProperty(property, MpvPropertyFormat.NONE, MpvPropertyValue.Unavailable)
        }

        override fun eventProperty(property: String, value: Long) {
            emitProperty(property, MpvPropertyFormat.INT64, MpvPropertyValue.Int64(value))
        }

        override fun eventProperty(property: String, value: Double) {
            emitProperty(
                property,
                MpvPropertyFormat.DOUBLE,
                MpvPropertyValue.DoubleValue(value),
            )
        }

        override fun eventProperty(property: String, value: Boolean) {
            emitProperty(property, MpvPropertyFormat.FLAG, MpvPropertyValue.Flag(value))
        }

        override fun eventProperty(property: String, value: String) {
            emitProperty(
                property,
                MpvPropertyFormat.STRING,
                MpvPropertyValue.StringValue(value),
            )
        }

        override fun event(eventId: Int) {
            if (destroyed.get()) {
                return
            }
            val event = MpvClientEvent(
                rawEventId = eventId,
            )
            notifyObservers { it.onEvent(event) }
        }
    }

    override fun addObserver(observer: MpvClient.Observer) {
        synchronized(destroyMonitor) {
            checkAlive()
            observers += observer
        }
    }

    override fun removeObserver(observer: MpvClient.Observer) {
        observers -= observer
    }

    override fun setOptionString(name: String, value: String) {
        checkAlive()
        val result = delegate.setOptionString(name, value)
        if (result < 0) {
            throw MpvOperationException("set option '$name'", result)
        }
    }

    override fun initialize() {
        checkAlive()
        delegate.init()
    }

    override fun command(arguments: Array<String>) {
        checkAlive()
        delegate.command(arguments)
    }

    override fun getPropertyDouble(name: String): Double? {
        checkAlive()
        return delegate.getPropertyDouble(name)
    }

    override fun getPropertyBoolean(name: String): Boolean? {
        checkAlive()
        return delegate.getPropertyBoolean(name)
    }

    override fun setPropertyDouble(name: String, value: Double) {
        checkAlive()
        delegate.setPropertyDouble(name, value)
    }

    override fun setPropertyBoolean(name: String, value: Boolean) {
        checkAlive()
        delegate.setPropertyBoolean(name, value)
    }

    override fun setPropertyString(name: String, value: String) {
        checkAlive()
        delegate.setPropertyString(name, value)
    }

    override fun observeProperty(
        name: String,
        format: MpvPropertyFormat,
        replyUserdata: Long,
    ) {
        checkAlive()
        require(replyUserdata > 0L) { "Property observer IDs must be positive." }
        require(format.isPrimitiveObservationFormat) {
            "The bootstrap libmpv client supports primitive observation formats only."
        }
        delegate.observeProperty(name, format.toBootstrapFormat())
    }

    override fun unobserveProperty(replyUserdata: Long): Int {
        // The bootstrap AAR exposes no unobserve operation. Its native
        // observer ends with the instance during destroy().
        checkAlive()
        require(replyUserdata > 0L) { "Property observer IDs must be positive." }
        return 0
    }

    override fun attachSurface(surface: Surface) {
        checkAlive()
        delegate.attachSurface(surface)
    }

    override fun detachSurface() {
        checkAlive()
        delegate.detachSurface()
    }

    override fun destroy() {
        synchronized(destroyMonitor) {
            if (destroyAttempted) {
                terminalDestroyFailure?.let { throw it }
                return
            }

            // Disable calls and callbacks before touching the delegate. A
            // concurrent destroy waits on this monitor. The AAR clears its
            // handle only after nativeDestroy returns, so an exception leaves
            // its commit point unknowable: never invoke that native destroy a
            // second time.
            destroyAttempted = true
            destroyed.set(true)
            observers.clear()

            val observerFailure = runCatching {
                delegate.removeObserver(delegateObserver)
            }.exceptionOrNull()

            val nativeFailure = runCatching { delegate.destroy() }.exceptionOrNull()
            if (nativeFailure == null) {
                observerFailure?.let(::reportCleanupFailure)
                return
            }
            observerFailure?.let { nativeFailure.addSuppressed(it) }
            terminalDestroyFailure = nativeFailure
            throw nativeFailure
        }
    }

    private fun reportCleanupFailure(failure: Throwable) {
        runCatching {
            Log.e(LOG_TAG, "Bootstrap observer cleanup failed before native destroy.", failure)
        }
    }

    private fun emitProperty(
        name: String,
        format: MpvPropertyFormat,
        value: MpvPropertyValue,
    ) {
        if (destroyed.get()) {
            return
        }
        val change = MpvPropertyChange(
            name = name,
            rawFormat = format.rawValue,
            value = value,
        )
        notifyObservers { it.onPropertyChanged(change) }
    }

    private inline fun notifyObservers(callback: (MpvClient.Observer) -> Unit) {
        observers.forEach { observer ->
            try {
                callback(observer)
            } catch (failure: Throwable) {
                // A client observer must not be able to terminate the AAR's
                // native event thread. Do not log event or property payloads.
                runCatching {
                    Log.e(LOG_TAG, "A libmpv observer callback failed.", failure)
                }
            }
        }
    }

    private fun checkAlive() {
        check(!destroyed.get()) { "The bootstrap libmpv client is destroyed." }
    }

    private fun MpvPropertyFormat.toBootstrapFormat(): Int = when (this) {
        MpvPropertyFormat.NONE -> MPVLib.MpvFormat.MPV_FORMAT_NONE
        MpvPropertyFormat.STRING -> MPVLib.MpvFormat.MPV_FORMAT_STRING
        MpvPropertyFormat.OSD_STRING -> MPVLib.MpvFormat.MPV_FORMAT_OSD_STRING
        MpvPropertyFormat.FLAG -> MPVLib.MpvFormat.MPV_FORMAT_FLAG
        MpvPropertyFormat.INT64 -> MPVLib.MpvFormat.MPV_FORMAT_INT64
        MpvPropertyFormat.DOUBLE -> MPVLib.MpvFormat.MPV_FORMAT_DOUBLE
        MpvPropertyFormat.NODE,
        MpvPropertyFormat.NODE_ARRAY,
        MpvPropertyFormat.NODE_MAP,
        MpvPropertyFormat.BYTE_ARRAY,
        MpvPropertyFormat.UNKNOWN,
        -> error("A non-primitive property format cannot be observed by the bootstrap client.")
    }

    companion object Factory : MpvClientFactory {
        private const val LOG_TAG = "ZivMpvBootstrap"

        override fun create(applicationContext: Context): MpvClient? {
            val delegate = MPVLib.create(applicationContext) ?: return null
            val client = BootstrapMpvClient(delegate)
            try {
                delegate.addObserver(client.delegateObserver)
            } catch (failure: Throwable) {
                runCatching { delegate.destroy() }
                    .exceptionOrNull()
                    ?.let(failure::addSuppressed)
                throw failure
            }
            return client
        }
    }
}
