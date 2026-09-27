package com.clench.eyetrack.muse

import android.content.Context
import android.os.SystemClock
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * The Sensor Service on the tablet: the Muse over Bluetooth (MuseBle), the same clench detection as
 * sensor/ (SensorLoop), and the same events to the Core's /ws/sensor. The Core cannot tell it from
 * `python -m sensor.main`; it accepts one sensor at a time, so run one or the other.
 *
 * Clenches only (no DOUBLE_BLINK yet: the Python sensor uses MNE for blinks, which Android lacks).
 *
 * The Core refuses a gesture stamped more than a second from its own clock, and the tablet's clock
 * is not the laptop's, so every timestamp is moved onto the Core's clock (GET /api/time).
 */
class MuseSensor(
    context: Context,
    private val profile: ClenchProfile,
    /** the board's origin, e.g. http://localhost:5173 (the Vite proxy passes /ws and /api to the Core) */
    origin: String,
    museName: String?,
    motionLimit: Double,
    private val onStatus: (String) -> Unit = {},
) {
    private val base = origin.trimEnd('/')
    private val wsUrl = base.replaceFirst(Regex("^http"), "ws") + "/ws/sensor"
    private val http = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(3, TimeUnit.SECONDS)
        .pingInterval(10, TimeUnit.SECONDS)
        .build()
    private val loopThread = Executors.newSingleThreadScheduledExecutor { Thread(it, "muse-sensor") }
    /** blocking HTTP (the clock) stays off the loop, so a slow Core never delays detection */
    private val io = Executors.newSingleThreadScheduledExecutor { Thread(it, "muse-core-clock") }
    private val window = MuseWindow(motionLimit)
    private val loop = SensorLoop(profile, "${profile.name} (tablet)")
    private val ble = MuseBle(context, museName, window, BleEvents())

    // Owned by loopThread.
    private var ws: WebSocket? = null
    private var wsGeneration = 0
    private var streaming = false
    private var headbandStatus: () -> List<String> = { loop.connecting(coreNow()) }
    private var stopped = false

    /** seconds to add to the tablet's clock to get the Core's */
    @Volatile private var clockOffset = 0.0

    fun start() {
        Log.i(TAG, "Muse on the tablet: profile ${profile.name} (threshold ${profile.emgThreshold} uV), Core $wsUrl")
        io.execute { openCore() }
        loopThread.scheduleWithFixedDelay({ runCatching { tick() }.onFailure { Log.e(TAG, "tick failed", it) } }, 0, TICK_MS, TimeUnit.MILLISECONDS)
        io.scheduleWithFixedDelay({ syncClock() }, CLOCK_EVERY_S, CLOCK_EVERY_S, TimeUnit.SECONDS)
        ble.start()
    }

    fun stop() {
        ble.stop()
        loopThread.execute {
            stopped = true
            ws?.close(1000, "tablet closed")
            ws = null
        }
        loopThread.shutdown()
        io.shutdownNow()
    }

    private fun coreNow() = System.currentTimeMillis() / 1000.0 + clockOffset

    private fun send(frames: List<String>) {
        val socket = ws ?: return
        frames.forEach { socket.send(it) }
    }

    // --- the loop (every 50 ms, as sensor/main.py) --------------------------------------------

    private fun tick() {
        if (!streaming) return
        val sample = try {
            window.read(SystemClock.elapsedRealtime())
        } catch (e: StreamStopped) {
            streaming = false
            Log.w(TAG, "Headband lost: ${e.message}")
            headbandStatus = { loop.lost(coreNow()) }
            send(headbandStatus())
            ble.restart(e.message ?: "EEG stream stopped")
            return
        }
        val out = loop.tick(sample, coreNow())
        out.filter { !it.startsWith("{\"type\":\"SIGNAL\"") }.forEach { Log.i(TAG, "sent $it") }
        send(out)
    }

    private inner class BleEvents : MuseBle.Listener {
        override fun onConnecting(detail: String) = onLoop {
            streaming = false
            headbandStatus = { loop.connecting(coreNow()) }
            send(headbandStatus())
            status(detail)
        }

        override fun onStreaming(device: String) = onLoop {
            streaming = true
            status("Headband connected ($device); enable Muse clenches in the web UI when ready")
        }

        override fun onLost(detail: String) = onLoop {
            streaming = false
            headbandStatus = { loop.lost(coreNow()) }
            send(headbandStatus())
            status(detail)
        }
    }

    private fun onLoop(block: () -> Unit) {
        if (!loopThread.isShutdown) runCatching { loopThread.execute(block) }
    }

    private fun status(text: String) {
        Log.i(TAG, text)
        onStatus(text)
    }

    // --- the Core ---------------------------------------------------------------------------

    /** On [io]: set the clock, then connect. */
    private fun openCore() {
        syncClock()
        onLoop { if (!stopped) connectCore() }
    }

    private fun connectCore() {
        val gen = ++wsGeneration
        http.newWebSocket(Request.Builder().url(wsUrl).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = onLoop {
                if (gen != wsGeneration || stopped) {
                    webSocket.close(1000, null)
                    return@onLoop
                }
                ws = webSocket
                loop.reset() // SETTINGS follow at once
                status("Connected to the Core")
                if (!streaming) send(headbandStatus())
            }

            override fun onMessage(webSocket: WebSocket, text: String) = onLoop {
                if (gen == wsGeneration) loop.onMessage(text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) =
                closed(gen, "Core closed the sensor connection ($code${if (reason.isNotEmpty()) ": $reason" else ""})")

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                closed(gen, "Core unreachable at $wsUrl (${t.message})")
        })
    }

    private fun closed(gen: Int, why: String) = onLoop {
        if (gen != wsGeneration || stopped) return@onLoop
        wsGeneration++
        ws = null
        loop.reset()
        status("$why; retrying in ${CORE_RETRY_S} s")
        runCatching { io.schedule({ openCore() }, CORE_RETRY_S, TimeUnit.SECONDS) }
    }

    /** NTP-style, on [io]: the Core's time against the middle of the fastest of a few round trips. */
    private fun syncClock() {
        var best: Pair<Double, Double>? = null // round trip, offset
        repeat(5) {
            runCatching {
                val t0 = System.currentTimeMillis() / 1000.0
                val body = http.newCall(Request.Builder().url("$base/api/time").build()).execute().use {
                    if (!it.isSuccessful) error("HTTP ${it.code}")
                    it.body?.string() ?: error("empty")
                }
                val t1 = System.currentTimeMillis() / 1000.0
                val core = JSONObject(body).getDouble("t")
                if (best == null || t1 - t0 < best!!.first) best = (t1 - t0) to (core - (t0 + t1) / 2)
            }
        }
        val b = best
        if (b == null) {
            Log.w(TAG, "clock: $base/api/time unreachable; using the tablet's clock (gestures are refused if it is off by over 1 s)")
            return
        }
        clockOffset = b.second
        Log.i(TAG, "clock: Core is %+.3f s from the tablet (round trip %.0f ms)".format(b.second, b.first * 1000))
    }

    companion object {
        private const val TAG = "MuseSensor"
        private const val TICK_MS = 50L
        private const val CORE_RETRY_S = 5L
        private const val CLOCK_EVERY_S = 60L
    }
}
