package com.clench.eyetrack.muse

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** sensor/main.py's run_connection per tick: what the Core receives on /ws/sensor. */
class SensorLoopTest {
    private val profile = ClenchProfile("taher", emgRest = 5.0, emgThreshold = 20.0, emgPeak = 55.0)
    private val loop = SensorLoop(profile, "taher (tablet)")
    private val quiet = Sample(5.0, listOf(2.0, 2.0, 2.0, 2.0))
    private val clench = Sample(30.0, listOf(9.0, 2.0, 2.0, 9.0))

    private fun json(frames: List<String>) = frames.map { JSONObject(it) }
    private fun settings(enabled: Boolean, longMs: Int = 2500) =
        loop.onMessage("""{"type":"SETTINGS","muse_enabled":$enabled,"long_clench_ms":$longMs,"lang":"en"}""")

    /** Enabled and armed at t = 0.8. */
    private fun ready() {
        settings(true)
        loop.tick(quiet, 0.0)
        loop.tick(quiet, 0.7)
        assertTrue(loop.armed)
    }

    @Test
    fun aClenchSendsASignalThenTheClench() {
        ready()
        loop.tick(clench, 1.0)
        val out = json(loop.tick(quiet, 1.3))
        assertEquals(listOf("SIGNAL", "CLENCH"), out.map { it.getString("type") })
        assertEquals(1.3, out[1].getDouble("t"), 1e-9)
        assertEquals(0.5, out[1].getDouble("strength"), 1e-9)
    }

    @Test
    fun theSignalCarriesWhatTheConsoleShows() {
        settings(true)
        val s = json(loop.tick(quiet, 0.0)).single()
        assertEquals("SIGNAL", s.getString("type"))
        assertTrue(s.getBoolean("connected"))
        assertEquals("taher (tablet)", s.getString("profile"))
        assertEquals(5.0, s.getDouble("emg"), 0.0)
        assertEquals(20.0, s.getDouble("threshold"), 0.0)
        assertEquals(4, s.getJSONArray("ch").length())
        assertEquals("Relax jaw to arm", s.getString("blocked"))
    }

    @Test
    fun theSignalIsThinnedToFourASecond() {
        settings(true)
        assertEquals(1, loop.tick(quiet, 0.0).size)
        assertEquals(0, loop.tick(quiet, 0.05).size)
        assertEquals(0, loop.tick(quiet, 0.2).size)
        assertEquals(1, loop.tick(quiet, 0.25).size)
    }

    @Test
    fun beforeSettingsNothingIsEnabledAndAPausedLoopSaysSo() {
        loop.tick(quiet, 0.0)
        loop.tick(quiet, 0.7)
        loop.tick(clench, 1.0)
        val out = json(loop.tick(quiet, 1.3))
        // The detector runs while paused (the Core refuses and logs it), and says it is paused.
        assertEquals("Paused in web UI", out.first().getString("blocked"))
    }

    @Test
    fun aLongClenchFollowsTheCoresSetting() {
        settings(true, longMs = 1000)
        loop.tick(quiet, 0.0)
        loop.tick(quiet, 0.7)
        loop.tick(clench, 1.0)
        val out = json(loop.tick(clench, 2.05))
        assertEquals("LONG_CLENCH", out.last().getString("type"))
        assertTrue(out.last().getDouble("duration") >= 1.0)
    }

    @Test
    fun aSettingsChangeMidClenchNeverFires() {
        settings(false)
        loop.tick(quiet, 0.0)
        loop.tick(quiet, 0.7)
        loop.tick(clench, 1.0)
        settings(true)
        loop.tick(clench, 1.1)
        assertFalse(json(loop.tick(quiet, 1.3)).any { it.getString("type") == "CLENCH" })
    }

    @Test
    fun aBlockedSampleReportsWhy() {
        ready()
        val s = json(loop.tick(Sample(30.0, emptyList(), "Head moving — hold still to clench"), 1.0)).single()
        assertEquals("Head moving — hold still to clench", s.getString("blocked"))
    }

    @Test
    fun connectingAndLostAreDisconnectedSignals() {
        val c = json(loop.connecting(1.0)).single()
        assertFalse(c.getBoolean("connected"))
        assertEquals("Connecting to Muse", c.getString("blocked"))
        assertTrue(c.isNull("emg"))
        val l = json(loop.lost(2.0)).single()
        assertEquals("Headband disconnected — reconnecting", l.getString("blocked"))
    }

    @Test
    fun framesThatAreNotSettingsAreIgnored() {
        loop.onMessage("not json")
        loop.onMessage("""{"type":"SCREEN"}""")
        loop.tick(quiet, 0.0)
        assertEquals("Paused in web UI", JSONObject(loop.tick(quiet, 0.3).single()).getString("blocked"))
    }

    @Test
    fun aNewCoreConnectionStartsPaused() {
        ready()
        loop.reset()
        assertEquals("Paused in web UI", JSONObject(loop.tick(quiet, 1.0).single()).getString("blocked"))
    }
}
