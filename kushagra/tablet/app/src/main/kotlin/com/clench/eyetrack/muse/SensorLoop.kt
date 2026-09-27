package com.clench.eyetrack.muse

import org.json.JSONArray
import org.json.JSONObject

/**
 * sensor/main.py's run_connection, one tick at a time and without I/O: SETTINGS in, SIGNAL and
 * gestures out, as JSON for /ws/sensor. Not thread-safe; MuseSensor calls it from one thread.
 * Times are epoch seconds on the Core's clock (MuseSensor corrects the tablet's clock).
 */
class SensorLoop(private val profile: ClenchProfile, private val profileLabel: String = profile.name) {
    private val detector = ClenchInput(profile)
    private var enabled = false
    private var settingsReceived = false
    private var nextStatus = 0.0

    val armed get() = detector.armed

    /** A new Core connection: nothing counts until its SETTINGS arrive. */
    fun reset() {
        detector.reset()
        enabled = false
        settingsReceived = false
        nextStatus = 0.0
    }

    /** One frame from the Core. Only SETTINGS matter to the sensor. */
    fun onMessage(text: String) {
        val msg = runCatching { JSONObject(text) }.getOrNull() ?: return
        if (msg.optString("type") != "SETTINGS") return
        val newEnabled = msg.optBoolean("muse_enabled", false)
        val newLong = msg.optDouble("long_clench_ms", Double.NaN).takeIf { it.isFinite() && it != 0.0 } ?: 2500.0
        if (newEnabled != enabled || newLong != detector.longMs) detector.reset()
        enabled = newEnabled
        detector.longMs = newLong
        settingsReceived = true
    }

    /** The headband is being looked for or connected. */
    fun connecting(now: Double): List<String> {
        detector.reset()
        return listOf(status(now, blocked = "Connecting to Muse"))
    }

    /** The headband dropped; the caller reconnects. */
    fun lost(now: Double): List<String> {
        detector.reset()
        return listOf(status(now, blocked = "Headband disconnected — reconnecting"))
    }

    /** One reading (every 50 ms): a SIGNAL every 0.25 s or with a gesture, then the gestures. */
    fun tick(sample: Sample, now: Double): List<String> {
        val gestures = detector.update(sample.level, now, enabled && settingsReceived, sample.blocked)
        val reason = sample.blocked
            ?: (if (!enabled) "Paused in web UI" else null)
            ?: (if (!detector.armed) "Relax jaw to arm" else null)
        val out = mutableListOf<String>()
        if (now >= nextStatus || gestures.isNotEmpty()) {
            out += status(now, sample, connected = true, blocked = reason)
            nextStatus = now + 0.25
        }
        gestures.forEach { out += gesture(it) }
        return out
    }

    private fun status(now: Double, sample: Sample? = null, connected: Boolean = false, blocked: String? = null): String =
        JSONObject()
            .put("type", "SIGNAL")
            .put("t", now)
            .put("ch", JSONArray(sample?.channels ?: emptyList<Double>()))
            .put("connected", connected)
            .put("profile", profileLabel)
            .put("emg", sample?.level?.takeIf { it.isFinite() && it >= 0 } ?: JSONObject.NULL)
            .put("threshold", profile.emgThreshold)
            .put("blocked", blocked ?: sample?.blocked ?: JSONObject.NULL)
            .toString()

    companion object {
        fun gesture(g: Gesture): String = when (g) {
            is Gesture.Clench -> JSONObject().put("type", "CLENCH").put("t", g.t).put("strength", g.strength)
            is Gesture.LongClench -> JSONObject().put("type", "LONG_CLENCH").put("t", g.t).put("duration", g.duration)
        }.toString()
    }
}
