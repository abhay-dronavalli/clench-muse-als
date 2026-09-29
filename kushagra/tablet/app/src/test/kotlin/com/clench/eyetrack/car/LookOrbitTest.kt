package com.clench.eyetrack.car

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.hypot

class LookOrbitTest {
    @Test fun staysStillInTheCentreOrWithoutEyes() {
        for (x in listOf(0.45f, 0.5f, 0.55f, Float.NaN)) {
            val orbit = LookOrbit()
            repeat(120) { orbit.update(x, true, 1f / 60) }
            assertEquals(CarMotion.ORBIT_START_DEG, orbit.degrees, 0.001)
        }
        val orbit = LookOrbit()
        repeat(120) { orbit.update(1f, false, 1f / 60) }
        assertEquals(CarMotion.ORBIT_START_DEG, orbit.degrees, 0.001)
    }

    @Test fun movesEitherDirectionAndEasesToAStopWhenEyesDisappear() {
        val right = LookOrbit()
        val left = LookOrbit()
        repeat(60) { right.update(1f, true, 1f / 60); left.update(0f, true, 1f / 60) }
        assertTrue(right.degrees > CarMotion.ORBIT_START_DEG)
        assertTrue(left.degrees < CarMotion.ORBIT_START_DEG)
        repeat(300) { right.update(1f, false, 1f / 60) }
        val stopped = right.degrees
        right.update(1f, false, 1f / 60)
        assertEquals(stopped, right.degrees, 0.001)
    }

    @Test fun fullOrbitKeepsFixedHeightAndDistance() {
        val orbit = LookOrbit()
        var wrapped = false
        repeat(1500) {
            val previous = orbit.degrees
            val angle = orbit.update(1f, true, 1f / 60)
            if (angle < previous) wrapped = true
            val (x, y, z) = CarMotion.orbit(angle, 1.9f, 0.45f)
            assertEquals(0.45f, y, 0f)
            assertEquals(1.9f, hypot(x, z), 0.0001f)
            assertTrue(angle >= 0 && angle < 360)
        }
        assertTrue(wrapped)
    }
}
