package com.clench.eyetrack.muse

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The same cases as tests/test_sensor_input.py, so the tablet and the laptop detect alike. */
class ClenchInputTest {
    private val profile = ClenchProfile("test", emgRest = 5.0, emgThreshold = 20.0, emgPeak = 55.0)

    private fun armed(longMs: Double = 2500.0) = ClenchInput(profile, longMs).also {
        // Enabling while the jaw is already active must not fire.
        assertEquals(emptyList<Gesture>(), it.update(5.0, 0.0, enabled = true))
        assertEquals(emptyList<Gesture>(), it.update(5.0, 0.7, enabled = true))
    }

    @Test
    fun shortClenchEmitsOnReleaseWithStrength() {
        val d = armed()
        assertEquals(emptyList<Gesture>(), d.update(30.0, 1.0, enabled = true))
        assertEquals(listOf(Gesture.Clench(1.25, 0.5)), d.update(5.0, 1.25, enabled = true))
    }

    @Test
    fun longClenchEmitsHelpOnceAndSuppressesRelease() {
        val d = armed(longMs = 1000.0)
        assertEquals(emptyList<Gesture>(), d.update(30.0, 1.0, enabled = true))
        val events = d.update(30.0, 2.05, enabled = true)
        val long = events.single() as Gesture.LongClench
        assertTrue(long.duration >= 1.0)
        assertEquals(emptyList<Gesture>(), d.update(5.0, 2.2, enabled = true))
    }

    @Test
    fun disabledOrBlockedInputResetsAndRearms() {
        val d = armed()
        d.update(30.0, 1.0, enabled = true)
        assertEquals(emptyList<Gesture>(), d.update(30.0, 1.1, enabled = false))
        assertEquals(emptyList<Gesture>(), d.update(5.0, 1.2, enabled = true))
        assertEquals(emptyList<Gesture>(), d.update(5.0, 1.9, enabled = true))
        d.update(30.0, 2.0, enabled = true)
        assertEquals(emptyList<Gesture>(), d.update(30.0, 2.1, enabled = true, blocked = "Head moving"))
    }

    @Test
    fun aPausedDetectorStillReportsAClenchForTheCoreToRefuse() {
        val d = ClenchInput(profile)
        d.update(5.0, 0.0, enabled = false)
        d.update(5.0, 0.7, enabled = false)
        d.update(30.0, 1.0, enabled = false)
        assertEquals(listOf("Clench"), d.update(5.0, 1.25, enabled = false).map { it::class.simpleName })
    }

    @Test
    fun aClenchHeldAcrossEnableNeverFires() {
        val d = ClenchInput(profile)
        d.update(5.0, 0.0, enabled = false)
        d.update(5.0, 0.7, enabled = false)
        d.update(30.0, 1.0, enabled = false) // clench starts while paused
        assertEquals(emptyList<Gesture>(), d.update(30.0, 1.1, enabled = true)) // enabled mid-clench
        assertEquals(emptyList<Gesture>(), d.update(5.0, 1.3, enabled = true)) // release: not armed
    }

    @Test
    fun aCrossingDuringHeadMotionNeverFiresAfterItStops() {
        val d = armed()
        d.update(30.0, 1.0, enabled = true, blocked = "Head moving")
        assertEquals(emptyList<Gesture>(), d.update(5.0, 1.2, enabled = true))
    }

    @Test
    fun tooShortABumpIsNotAClench() {
        val d = armed()
        d.update(30.0, 1.0, enabled = true)
        assertEquals(emptyList<Gesture>(), d.update(5.0, 1.05, enabled = true)) // 50 ms < 80 ms
    }

    @Test
    fun aNanLevelResets() {
        val d = armed()
        d.update(30.0, 1.0, enabled = true)
        assertEquals(emptyList<Gesture>(), d.update(Double.NaN, 1.1, enabled = true))
        assertEquals(emptyList<Gesture>(), d.update(5.0, 1.3, enabled = true))
        assertTrue(!d.armed)
    }

    @Test(expected = IllegalArgumentException::class)
    fun aThresholdBelowRestIsRefused() {
        ClenchProfile("bad", emgRest = 20.0, emgThreshold = 10.0, emgPeak = null)
    }
}
