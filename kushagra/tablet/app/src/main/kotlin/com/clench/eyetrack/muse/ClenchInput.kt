package com.clench.eyetrack.muse

/** A per-person jaw calibration (test/calibration.<name>.json, made by the Muse bench). */
data class ClenchProfile(val name: String, val emgRest: Double, val emgThreshold: Double, val emgPeak: Double?) {
    init {
        require(emgRest.isFinite() && emgThreshold.isFinite() && emgRest >= 0 && emgRest < emgThreshold) {
            "Clench profile needs a finite threshold above its resting baseline"
        }
    }
}

/** A gesture for the Core: CLENCH or LONG_CLENCH (core/contracts.py). */
sealed class Gesture {
    abstract val t: Double
    data class Clench(override val t: Double, val strength: Double) : Gesture()
    data class LongClench(override val t: Double, val duration: Double) : Gesture()
}

/**
 * sensor/detect/clench.py's EdgeDetector: a threshold crossing with hysteresis (rise at the
 * threshold, fall at 60% of the way back to rest), a minimum duration and a refractory gap.
 * Times are seconds.
 */
class EdgeDetector(val threshold: Double, private val minMs: Double, private val refractoryMs: Double, baseline: Double = 0.0) {
    val release = baseline + (threshold - baseline) * RELEASE_FRACTION
    var active = false
        private set
    private var startedAt = 0.0
    private var lastEventAt = -1e9
    private var peak = 0.0

    sealed class Edge {
        data class Rise(val peak: Double) : Edge()
        data class Fall(val durationMs: Double, val peak: Double) : Edge()
    }

    fun update(level: Double, now: Double): Edge? {
        if (!active) {
            if (level > threshold && (now - lastEventAt) * 1000 > refractoryMs) {
                active = true
                startedAt = now
                peak = level
                return Edge.Rise(level)
            }
        } else {
            peak = maxOf(peak, level)
            if (level < release) {
                active = false
                lastEventAt = now
                val durationMs = (now - startedAt) * 1000
                val p = peak
                peak = 0.0
                if (durationMs >= minMs) return Edge.Fall(durationMs, p)
            }
        }
        return null
    }

    fun heldMs(now: Double) = if (active) (now - startedAt) * 1000 else 0.0

    companion object {
        const val RELEASE_FRACTION = 0.6
    }
}

/**
 * sensor/detect/input.py's ClenchInput: the calibrated jaw detector to CLENCH / LONG_CLENCH.
 *
 * Detection keeps running while paused or blocked (the Core refuses and logs what it gets then), and
 * any change of the pause or block state re-arms, so a clench held across Enable, or a crossing
 * during head motion, never fires. After a reset the jaw must stay relaxed 600 ms before a gesture.
 */
class ClenchInput(private val profile: ClenchProfile, var longMs: Double = 2500.0) {
    private lateinit var edge: EdgeDetector
    var armed = false
        private set
    private var quietSince: Double? = null
    private var longFired = false
    private var gate: Pair<Boolean, Boolean>? = null

    init {
        reset()
    }

    fun reset() {
        edge = EdgeDetector(profile.emgThreshold, 80.0, 250.0, profile.emgRest)
        armed = false
        quietSince = null
        longFired = false
    }

    fun update(level: Double, now: Double, enabled: Boolean, blocked: String? = null): List<Gesture> {
        val g = enabled to (blocked == null)
        if (g != gate) {
            gate = g
            reset()
        }
        if (!level.isFinite()) {
            reset()
            return emptyList()
        }
        if (!armed) {
            if (level < edge.release) {
                val since = quietSince ?: now.also { quietSince = it }
                armed = now - since >= 0.6
            } else {
                quietSince = null
            }
            return emptyList()
        }
        val e = edge.update(level, now)
        if (e is EdgeDetector.Edge.Rise) longFired = false
        if (e is EdgeDetector.Edge.Fall && !longFired) {
            val span = maxOf(profile.emgPeak?.takeIf { it != 0.0 } ?: edge.threshold, edge.threshold) - profile.emgRest
            val strength = ((e.peak - profile.emgRest) / span).coerceIn(0.0, 1.0)
            return listOf(Gesture.Clench(now, strength))
        }
        if (edge.active && !longFired && edge.heldMs(now) >= longMs) {
            longFired = true
            return listOf(Gesture.LongClench(now, edge.heldMs(now) / 1000))
        }
        return emptyList()
    }
}
