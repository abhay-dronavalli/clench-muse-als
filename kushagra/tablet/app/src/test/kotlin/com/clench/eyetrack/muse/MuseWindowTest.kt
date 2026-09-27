package com.clench.eyetrack.muse

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.sin

/** sensor/sources/muse.py's read() checks, on packets as the headband sends them. */
class MuseWindowTest {
    private var clock = 0L
    private val window = MuseWindow(motionLimit = 30.0).also { it.clear(0) }
    private var sampleNo = 0

    /** Feed [packets] packets on all four channels (12 samples each, 47 ms apart) and gyro alongside. */
    private fun feed(packets: Int, firstCounter: Int = 0, signal: (Int) -> Double = { 10 * sin(it * 0.9) }, gyroDps: Double = 0.0) {
        for (p in 0 until packets) {
            val counter = (firstCounter + p) and 0xffff
            val samples = DoubleArray(12) { signal(sampleNo + it) }
            sampleNo += 12
            clock += 47
            for (ch in 0 until 4) window.onEeg(ch, MuseProtocol.EegPacket(counter, samples), clock)
            window.onGyro(MuseProtocol.GyroPacket(counter, List(3) { doubleArrayOf(gyroDps, 0.0, 0.0) }), clock)
        }
    }

    @Test
    fun waitsForASecondOfSamples() {
        feed(10)
        assertEquals("Waiting for EEG samples", window.read(clock).blocked)
    }

    @Test
    fun aFullQuietWindowIsClearWithALevelAndChannels() {
        feed(22)
        val s = window.read(clock)
        assertNull(s.blocked)
        assertEquals(4, s.channels.size)
        assertTrue(s.level.isFinite() && s.level >= 0)
    }

    @Test
    fun aClenchRaisesTheLevel() {
        feed(22)
        val rest = window.read(clock).level
        feed(22, firstCounter = 22, signal = { 10 * sin(it * 0.9) + 80 * sin(2 * Math.PI * 75 * it / 256.0) })
        assertTrue(window.read(clock).level > 5 * rest)
    }

    @Test(expected = StreamStopped::class)
    fun aStreamThatStopsForASecondIsLost() {
        feed(22)
        window.read(clock + 1_500)
    }

    @Test(expected = StreamStopped::class)
    fun aHeadbandThatNeverStreamsIsLost() {
        window.read(3_500)
    }

    @Test
    fun oneDroppedPacketIsToleratedButAGapIsNot() {
        feed(12)
        feed(12, firstCounter = 13) // counter 12 lost: 12 samples missing, under 0.1 s
        assertNull(window.read(clock).blocked)
        feed(3, firstCounter = 30) // five more lost in the window
        assertEquals("EEG samples interrupted; waiting for continuous data", window.read(clock).blocked)
    }

    @Test
    fun theCounterMayWrapAround() {
        feed(22, firstCounter = 0xfff5)
        assertNull(window.read(clock).blocked)
    }

    @Test
    fun aFlatChannelIsPoorContact() {
        feed(22, signal = { 0.1 })
        assertEquals("TP9 poor contact — adjust band", window.read(clock).blocked)
    }

    @Test
    fun headMotionBlocks() {
        feed(22, gyroDps = 45.0)
        val s = window.read(clock)
        assertEquals("Head moving — hold still to clench", s.blocked)
        assertTrue(s.level >= 0) // the level still goes to the console
    }

    @Test
    fun noGyroscopeMeansWaiting() {
        for (p in 0 until 22) {
            clock += 47
            for (ch in 0 until 4) window.onEeg(ch, MuseProtocol.EegPacket(p, DoubleArray(12) { 10 * sin((p * 12 + it) * 0.9) }), clock)
        }
        assertEquals("Waiting for motion sensor", window.read(clock).blocked)
    }

    @Test
    fun clearForgetsTheOldConnection() {
        feed(22)
        window.clear(clock)
        assertEquals("Waiting for EEG samples", window.read(clock).blocked)
    }
}
