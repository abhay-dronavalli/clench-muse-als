package com.clench.eyetrack.muse

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MuseProtocolTest {
    /** Pack twelve 12-bit counts the way the Muse does, after a 16-bit packet counter. */
    private fun eegPacket(counter: Int, counts: IntArray): ByteArray {
        val out = ByteArray(20)
        out[0] = (counter shr 8).toByte(); out[1] = counter.toByte()
        var i = 2
        for (k in counts.indices step 2) {
            val a = counts[k]; val b = counts[k + 1]
            out[i] = (a shr 4).toByte()
            out[i + 1] = (((a and 0xf) shl 4) or (b shr 8)).toByte()
            out[i + 2] = b.toByte()
            i += 3
        }
        return out
    }

    @Test
    fun eegSamplesAreCenteredAndScaledToMicrovolts() {
        val counts = intArrayOf(0x800, 0x801, 0x7ff, 0, 0xfff, 0x800, 0x900, 0x700, 1, 2, 3, 4)
        val p = MuseProtocol.eeg(eegPacket(0xbeef, counts))!!
        assertEquals(0xbeef, p.counter)
        assertArrayEquals(counts.map { 0.48828125 * (it - 0x800) }.toDoubleArray(), p.samples, 1e-12)
        assertEquals(-1000.0, p.samples[3], 1e-12)
    }

    @Test
    fun gyroIsSignedAndScaledToDegreesPerSecond() {
        val bytes = ByteArray(20)
        bytes[1] = 7
        val raw = shortArrayOf(1000, -1000, 0, 1, -1, 32767, -32768, 5, 6)
        raw.forEachIndexed { i, v -> bytes[2 + i * 2] = (v.toInt() shr 8).toByte(); bytes[3 + i * 2] = v.toByte() }
        val p = MuseProtocol.gyro(bytes)!!
        assertEquals(7, p.counter)
        assertArrayEquals(doubleArrayOf(7.4768, -7.4768, 0.0), p.xyz[0], 1e-9)
        assertEquals(-32768 * 0.0074768, p.xyz[2][0], 1e-9)
    }

    @Test
    fun shortPacketsAreIgnored() {
        assertNull(MuseProtocol.eeg(ByteArray(10)))
        assertNull(MuseProtocol.gyro(ByteArray(10)))
    }

    @Test
    fun commandsCarryTheirLengthAndANewline() {
        assertArrayEquals(byteArrayOf(4, 'p'.code.toByte(), '2'.code.toByte(), '1'.code.toByte(), '\n'.code.toByte()), MuseProtocol.command("p21"))
        assertArrayEquals(byteArrayOf(2, 'h'.code.toByte(), '\n'.code.toByte()), MuseProtocol.command("h"))
    }

    @Test
    fun uuidsAreTheMuseOnes() {
        assertEquals("0000fe8d-0000-1000-8000-00805f9b34fb", MuseProtocol.SERVICE.toString())
        assertEquals("273e0003-4c4d-454d-96be-f03bac821358", MuseProtocol.EEG[0].toString())
        assertEquals("273e0006-4c4d-454d-96be-f03bac821358", MuseProtocol.EEG[3].toString())
    }
}
