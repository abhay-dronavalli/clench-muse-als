package com.clench.eyetrack.car

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sign

/** Horizontal camera orbit only: looking at the centre stops, looking aside turns at <= 30 deg/s. */
class LookOrbit {
    var degrees = CarMotion.ORBIT_START_DEG
        private set
    private var velocity = 0.0

    fun update(x: Float, found: Boolean, seconds: Float): Double {
        val dt = seconds.coerceIn(0f, 0.1f).toDouble()
        val offset = if (found && x.isFinite()) (x.coerceIn(0f, 1f) - 0.5f) * 2f else 0f
        val target = sign(offset) * ((abs(offset) - 0.2f) / 0.8f).coerceIn(0f, 1f) * 30.0
        velocity += (target - velocity) * (1 - exp(-dt / 0.35))
        degrees = (degrees + velocity * dt + 360.0) % 360.0
        return degrees
    }
}
