package com.clench.eyetrack.car

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CarMotionTest {
    private val box = CarMotion.Box(halfLength = 0.5f, halfWidth = 0.2f, halfHeight = 0.15f)
    private val routine = listOf("window_up", "window_down", "warmer", "cooler", "music")

    private fun alive(action: String, p: Float) = CarMotion.lines(action, p, box).filter { it.length > 1e-4f }

    @Test
    fun noLineEverPopsOrFlickers() {
        // At 60 fps over a 900 ms sequence (54 frames), no line may change length by more than a
        // fraction of its size between two frames, and every line starts and ends at nothing.
        for (action in routine) {
            assertTrue("$action jumps", CarMotion.largestJump(action, box, 54) < 0.02f)
            assertTrue("$action starts drawn", alive(action, 0f).isEmpty())
            assertTrue("$action ends drawn", alive(action, 1f).isEmpty())
        }
    }

    @Test
    fun windowUpRisesAndWindowDownFallsAlongTheDoor() {
        fun heights(action: String) = (1..9).map { CarMotion.lines(action, it / 10f, box)[0] }
            .filter { it.length > 0 }.map { it.y }
        val up = heights("window_up")
        val down = heights("window_down")
        assertTrue(up.zipWithNext().all { (a, b) -> b >= a })
        assertTrue(down.zipWithNext().all { (a, b) -> b <= a })
        assertTrue(CarMotion.lines("window_up", 0.3f, box).all { it.z > box.halfWidth }) // outside the door
    }

    @Test
    fun warmDriftsUpAndOutCoolSettlesDown() {
        val warmEarly = CarMotion.lines("warmer", 0.1f, box)[0]
        val warmLate = CarMotion.lines("warmer", 0.5f, box)[0]
        assertTrue(warmLate.y > warmEarly.y)
        val coolEarly = CarMotion.lines("cooler", 0.1f, box)[0]
        val coolLate = CarMotion.lines("cooler", 0.5f, box)[0]
        assertTrue(coolLate.y < coolEarly.y)
        assertTrue(CarMotion.lines("warmer", 0.4f, box).all { it.y > box.halfHeight }) // above the car
    }

    @Test
    fun musicPulsesInBeatsRatherThanFloating() {
        val lengths = (0..60).map { CarMotion.lines("music", it / 60f, box)[0].length }
        // Rises and falls several times: count local maxima.
        val peaks = lengths.windowed(3).count { (a, b, c) -> b > a && b >= c }
        assertEquals(3, peaks)
    }

    @Test
    fun pullOverHasNoParticlesAndAHoldingPush() {
        assertTrue(CarMotion.lines("pull_over", 0.5f, box).isEmpty())
        assertEquals(CarMotion.push("pull_over", 0.5f), CarMotion.push("pull_over", 1f), 1e-6f)
        assertEquals(0f, CarMotion.push("warmer", 0f), 1e-6f)
        assertEquals(0f, CarMotion.push("warmer", 1f), 1e-5f)
    }

    @Test
    fun stillMeansStill() {
        assertEquals(CarMotion.REST_YAW_DEG, CarMotion.yaw(2.3, amount = 0f), 1e-6f)
        val swing = (0..90).map { CarMotion.yaw(it / 10.0, 1f) }
        assertTrue(swing.max() - swing.min() <= 2 * CarMotion.SWAY_DEG + 1e-3f)
    }
}
