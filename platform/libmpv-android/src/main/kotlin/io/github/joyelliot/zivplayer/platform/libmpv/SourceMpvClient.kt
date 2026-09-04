// SPDX-License-Identifier: GPL-3.0-or-later

package io.github.joyelliot.zivplayer.platform.libmpv

import android.content.Context
import android.os.Looper
import android.util.Log
import android.view.Surface
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Source-owned implementation backed by libzivplayer_mpv. */
internal class SourceMpvClient private constructor(
    private val token: Long,
    private val nativeApi: MpvNativeApi,
    private val eventThreadFactory: MpvEventThreadFactory,
    private val isMainThread: () -> Boolean,
    private val eventThreadJoinTimeoutMillis: Long,
    private val failureLogger: (String, Throwable) -> Unit,
) : MpvClient {
    private val observers = CopyOnWriteArraySet<MpvClient.Observer>()
    private val destroyMonitor = Any()
    private val closedRequested = AtomicBoolean(false)
    private val eventPumpFailure = AtomicReference<Throwable?>(null)

    private val state = AtomicReference(State.CREATED)

    @Volatile
    private var eventThread: Thread? = null

    @Volatile
    private var surfaceAttached = false

    @Volatile
    private var terminalDestroyFailure: Throwable? = null

    init {
        require(token > 0L) { "A source libmpv client token must be positive." }
        require(eventThreadJoinTimeoutMillis > 0L) {
            "The event-thread join timeout must be positive."
        }
    }

    override fun addObserver(observer: MpvClient.Observer) {
        checkObserverRegistrationAllowed()
        observers += observer
        if (!isObserverRegistrationAllowed()) {
            observers -= observer
            checkObserverRegistrationAllowed()
        }
    }

    override fun removeObserver(observer: MpvClient.Observer) {
        observers -= observer
    }

    override fun setOptionString(name: String, value: String) {
        synchronized(destroyMonitor) {
            checkState(State.CREATED)
            requireSuccess(
                operation = "set option '$name'",
                status = nativeApi.setOptionString(token, name, value),
            )
        }
    }

    override fun initialize() {
        synchronized(destroyMonitor) {
            checkState(State.CREATED)
            val status = try {
                nativeApi.initialize(token)
            } catch (failure: Throwable) {
                state.set(State.BROKEN)
                throw failure
            }
            if (status < 0) {
                state.set(State.BROKEN)
                throw MpvOperationException("initialize", status)
            }
            val thread = try {
                eventThreadFactory.create(token, ::runEventPump)
            } catch (failure: Throwable) {
                state.set(State.BROKEN)
                eventPumpFailure.compareAndSet(null, failure)
                throw failure
            }
            eventThread = thread
            state.set(State.RUNNING)
            try {
                thread.start()
            } catch (failure: Throwable) {
                eventThread = null
                state.set(State.BROKEN)
                eventPumpFailure.compareAndSet(null, failure)
                throw failure
            }
        }
    }

    override fun command(arguments: Array<String>) {
        checkRunning()
        require(arguments.isNotEmpty()) { "A libmpv command must not be empty." }
        requireSuccess(
            operation = "command '${arguments.first()}'",
            status = nativeApi.command(token, arguments),
        )
    }

    override fun getPropertyDouble(name: String): Double? {
        checkRunning()
        val output = DoubleArray(1)
        return when (val status = nativeApi.getPropertyDouble(token, name, output)) {
            MPV_ERROR_PROPERTY_UNAVAILABLE -> null
            else -> {
                requireSuccess("get property '$name'", status)
                output.single()
            }
        }
    }

    override fun getPropertyBoolean(name: String): Boolean? {
        checkRunning()
        val output = IntArray(1)
        return when (val status = nativeApi.getPropertyBoolean(token, name, output)) {
            MPV_ERROR_PROPERTY_UNAVAILABLE -> null
            else -> {
                requireSuccess("get property '$name'", status)
                output.single() != 0
            }
        }
    }

    override fun setPropertyDouble(name: String, value: Double) {
        checkRunning()
        requireSuccess(
            operation = "set property '$name'",
            status = nativeApi.setPropertyDouble(token, name, value),
        )
    }

    override fun setPropertyBoolean(name: String, value: Boolean) {
        checkRunning()
        requireSuccess(
            operation = "set property '$name'",
            status = nativeApi.setPropertyBoolean(token, name, value),
        )
    }

    override fun setPropertyString(name: String, value: String) {
        checkRunning()
        requireSuccess(
            operation = "set property '$name'",
            status = nativeApi.setPropertyString(token, name, value),
        )
    }

    override fun observeProperty(
        name: String,
        format: MpvPropertyFormat,
        replyUserdata: Long,
    ) {
        checkRunning()
        require(replyUserdata > 0L) { "Property observer IDs must be positive." }
        require(format.isSourceObservationFormat) {
            "The source libmpv client supports stable primitive observation formats only."
        }
        requireSuccess(
            operation = "observe property '$name'",
            status = nativeApi.observeProperty(token, name, format.rawValue, replyUserdata),
        )
    }

    override fun unobserveProperty(replyUserdata: Long): Int {
        checkRunning()
        require(replyUserdata > 0L) { "Property observer IDs must be positive." }
        return requireSuccess(
            operation = "unobserve property",
            status = nativeApi.unobserveProperty(token, replyUserdata),
        )
    }

    override fun attachSurface(surface: Surface) {
        checkRunning()
        requireSuccess("attach Surface", nativeApi.attachSurface(token, surface))
        surfaceAttached = true
    }

    override fun detachSurface() {
        checkSurfaceDetachAllowed()
        requireSuccess("detach Surface", nativeApi.detachSurface(token))
        surfaceAttached = false
    }

    override fun destroy() {
        terminalDestroyFailure?.let { throw it }
        if (state.get() == State.DESTROYED) {
            return
        }

        val pump = eventThread
        check(Thread.currentThread() !== pump) {
            "The source libmpv client cannot destroy itself from its event thread."
        }
        check(!isMainThread()) {
            "The source libmpv client must be destroyed off Android's main thread."
        }

        synchronized(destroyMonitor) {
            terminalDestroyFailure?.let { throw it }
            if (state.get() == State.DESTROYED) {
                return
            }

            state.set(State.CLOSING)
            closedRequested.set(true)
            observers.clear()

            val activePump = eventThread
            if (activePump?.isAlive == true) {
                requireSuccess("wake event thread", nativeApi.wakeup(token))
                try {
                    activePump.join(eventThreadJoinTimeoutMillis)
                } catch (failure: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw IllegalStateException(
                        "Interrupted while joining the source libmpv event thread.",
                        failure,
                    )
                }
                check(!activePump.isAlive) {
                    "The source libmpv event thread did not stop before the join timeout."
                }
            }
            eventThread = null

            if (surfaceAttached) {
                requireSuccess("detach Surface", nativeApi.detachSurface(token))
                surfaceAttached = false
            }

            val destroyStatus = try {
                nativeApi.terminateDestroy(token)
            } catch (failure: Throwable) {
                terminalDestroyFailure = failure
                throw failure
            }
            requireSuccess("terminate native client", destroyStatus)
            state.set(State.DESTROYED)
        }
    }

    private fun runEventPump() {
        try {
            while (!closedRequested.get()) {
                val event = nativeApi.waitEvent(token, EVENT_WAIT_TIMEOUT_SECONDS)
                if (closedRequested.get()) {
                    return
                }
                when (MpvEventType.fromRaw(event.rawEventId)) {
                    MpvEventType.NONE -> Unit
                    MpvEventType.PROPERTY_CHANGE -> dispatchProperty(event)
                    else -> dispatchEvent(event)
                }
                if (event.rawEventId == MpvEventType.SHUTDOWN.rawValue) {
                    throw IllegalStateException("The source libmpv event pump received shutdown.")
                }
            }
        } catch (failure: Throwable) {
            if (
                !closedRequested.get() &&
                state.compareAndSet(State.RUNNING, State.BROKEN)
            ) {
                eventPumpFailure.compareAndSet(null, failure)
                reportFailure("The source libmpv event pump stopped unexpectedly.", failure)
                notifyObservers { it.onFailure(MpvClientFailure.EVENT_PUMP_STOPPED) }
            }
        }
    }

    private fun dispatchProperty(event: MpvNativeEvent) {
        val property = requireNotNull(event.property) {
            "A property-change event omitted its copied payload."
        }
        val change = MpvPropertyChange(
            name = property.name,
            rawFormat = property.rawFormat,
            value = property.toClientValue(),
            replyUserdata = event.replyUserdata,
            errorCode = event.errorCode,
        )
        notifyObservers { it.onPropertyChanged(change) }
    }

    private fun dispatchEvent(event: MpvNativeEvent) {
        when (MpvEventType.fromRaw(event.rawEventId)) {
            MpvEventType.START_FILE -> requireNotNull(event.playlistEntryId) {
                "A start-file event omitted its playlist-entry ID."
            }

            MpvEventType.END_FILE -> {
                requireNotNull(event.playlistEntryId) {
                    "An end-file event omitted its playlist-entry ID."
                }
                requireNotNull(event.rawEndReason) {
                    "An end-file event omitted its reason."
                }
            }

            else -> Unit
        }
        val copied = MpvClientEvent(
            rawEventId = event.rawEventId,
            errorCode = event.errorCode,
            replyUserdata = event.replyUserdata,
            playlistEntryId = event.playlistEntryId,
            rawEndReason = event.rawEndReason,
            endErrorCode = event.endErrorCode,
            playlistInsertId = event.playlistInsertId,
            playlistInsertCount = event.playlistInsertCount,
        )
        notifyObservers { it.onEvent(copied) }
    }

    private inline fun notifyObservers(callback: (MpvClient.Observer) -> Unit) {
        if (closedRequested.get()) {
            return
        }
        for (observer in observers) {
            if (closedRequested.get()) {
                return
            }
            try {
                callback(observer)
            } catch (failure: Throwable) {
                reportFailure("A source libmpv observer callback failed.", failure)
            }
        }
    }

    private fun MpvNativeProperty.toClientValue(): MpvPropertyValue {
        val format = MpvPropertyFormat.fromRaw(rawFormat)
        if (
            format == MpvPropertyFormat.OSD_STRING ||
            format == MpvPropertyFormat.NODE ||
            format == MpvPropertyFormat.NODE_ARRAY ||
            format == MpvPropertyFormat.NODE_MAP ||
            format == MpvPropertyFormat.BYTE_ARRAY ||
            format == MpvPropertyFormat.UNKNOWN
        ) {
            return MpvPropertyValue.Unsupported(rawFormat)
        }
        if (!hasValue || format == MpvPropertyFormat.NONE) {
            return MpvPropertyValue.Unavailable
        }
        return when (format) {
            MpvPropertyFormat.STRING -> stringValue
                ?.let(MpvPropertyValue::StringValue)
                ?: MpvPropertyValue.Unavailable

            MpvPropertyFormat.FLAG -> MpvPropertyValue.Flag(flagValue)
            MpvPropertyFormat.INT64 -> MpvPropertyValue.Int64(int64Value)
            MpvPropertyFormat.DOUBLE -> MpvPropertyValue.DoubleValue(doubleValue)
            MpvPropertyFormat.NONE -> MpvPropertyValue.Unavailable
            MpvPropertyFormat.OSD_STRING,
            MpvPropertyFormat.NODE,
            MpvPropertyFormat.NODE_ARRAY,
            MpvPropertyFormat.NODE_MAP,
            MpvPropertyFormat.BYTE_ARRAY,
            MpvPropertyFormat.UNKNOWN,
            -> error("Unsupported property formats must be handled before value decoding.")
        }
    }

    private fun checkCallable() {
        check(!closedRequested.get()) { "The source libmpv client is closing or destroyed." }
        terminalDestroyFailure?.let { throw it }
    }

    private fun checkState(expected: State) {
        checkCallable()
        val actual = state.get()
        check(actual == expected) {
            "The source libmpv client is ${actual.name.lowercase()}; expected ${expected.name.lowercase()}."
        }
    }

    private fun checkRunning() {
        checkCallable()
        eventPumpFailure.get()?.let { failure ->
            throw IllegalStateException("The source libmpv event pump is unavailable.", failure)
        }
        check(state.get() == State.RUNNING) {
            "The source libmpv client is not initialized."
        }
    }

    private fun checkSurfaceDetachAllowed() {
        checkCallable()
        val actual = state.get()
        check(actual == State.RUNNING || actual == State.BROKEN) {
            "The source libmpv client cannot detach its Surface while ${actual.name.lowercase()}."
        }
    }

    private fun checkObserverRegistrationAllowed() {
        checkCallable()
        val actual = state.get()
        check(actual == State.CREATED || actual == State.RUNNING) {
            "The source libmpv client cannot add observers while ${actual.name.lowercase()}."
        }
    }

    private fun isObserverRegistrationAllowed(): Boolean {
        val actual = state.get()
        return !closedRequested.get() && (actual == State.CREATED || actual == State.RUNNING)
    }

    private fun requireSuccess(operation: String, status: Int): Int =
        requireMpvSuccess(operation, status)

    private fun reportFailure(message: String, failure: Throwable) {
        runCatching { failureLogger(message, failure) }
    }

    private enum class State {
        CREATED,
        RUNNING,
        BROKEN,
        CLOSING,
        DESTROYED,
    }

    companion object Factory : MpvClientFactory {
        private const val LOG_TAG = "ZivMpvSource"
        private const val EVENT_WAIT_TIMEOUT_SECONDS = 0.25
        private const val EVENT_THREAD_JOIN_TIMEOUT_MILLIS = 5_000L

        override fun create(applicationContext: Context): MpvClient? =
            create(applicationContext, MpvNativeBindings)

        internal fun create(
            applicationContext: Context,
            nativeApi: MpvNativeApi,
            eventThreadFactory: MpvEventThreadFactory = MpvEventThreadFactory.Default,
            isMainThread: () -> Boolean = ::isAndroidMainThread,
            eventThreadJoinTimeoutMillis: Long = EVENT_THREAD_JOIN_TIMEOUT_MILLIS,
            failureLogger: (String, Throwable) -> Unit = ::logFailure,
        ): SourceMpvClient? {
            val processContext = applicationContext.applicationContext ?: applicationContext
            val token = nativeApi.create(processContext)
            if (token == 0L) {
                return null
            }
            check(token > 0L) { "The native source bridge returned an invalid client token." }
            return SourceMpvClient(
                token = token,
                nativeApi = nativeApi,
                eventThreadFactory = eventThreadFactory,
                isMainThread = isMainThread,
                eventThreadJoinTimeoutMillis = eventThreadJoinTimeoutMillis,
                failureLogger = failureLogger,
            )
        }

        private fun isAndroidMainThread(): Boolean = runCatching {
            Looper.myLooper() === Looper.getMainLooper()
        }.getOrDefault(false)

        private fun logFailure(message: String, failure: Throwable) {
            runCatching { Log.e(LOG_TAG, message, failure) }
        }
    }
}

internal fun interface MpvEventThreadFactory {
    fun create(token: Long, eventPump: () -> Unit): Thread

    data object Default : MpvEventThreadFactory {
        override fun create(token: Long, eventPump: () -> Unit): Thread =
            Thread(eventPump, "ZivPlayer-mpv-event-$token").apply {
                isDaemon = true
            }
    }
}
