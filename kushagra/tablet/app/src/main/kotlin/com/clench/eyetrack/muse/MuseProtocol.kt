package com.clench.eyetrack.muse

import java.util.UUID

/**
 * The Muse 2 Bluetooth LE protocol (the one muse-js and muse-lsl use; BrainFlow speaks the same).
 *
 * One service, a control characteristic that takes short text commands, and one notifying
 * characteristic per EEG channel and for the gyroscope. Each EEG notification is 20 bytes: a 16-bit
 * packet counter, then 12 samples of 12 bits (256 Hz, so a packet every ~47 ms per channel). Each
 * gyroscope notification is a 16-bit counter, then 3 samples of x, y, z as signed 16-bit numbers.
 */
object MuseProtocol {
    val SERVICE: UUID = uuid16(0xfe8d)
    val CONTROL: UUID = UUID.fromString("273e0001-4c4d-454d-96be-f03bac821358")
    val GYRO: UUID = UUID.fromString("273e0009-4c4d-454d-96be-f03bac821358")
    /** TP9, AF7, AF8, TP10: the order BrainFlow's EEG rows use, and the SIGNAL `ch` order. */
    val EEG: List<UUID> = listOf("0003", "0004", "0005", "0006").map { UUID.fromString("273e$it-4c4d-454d-96be-f03bac821358") }
    val CCCD: UUID = uuid16(0x2902)

    const val SAMPLES_PER_PACKET = 12
    const val GYRO_PER_PACKET = 3
    /** uV per EEG count, centered on 0x800. */
    const val EEG_SCALE = 0.48828125
    /** degrees per second per gyroscope count */
    const val GYRO_SCALE = 0.0074768

    /** Pause, choose preset 21 (4 EEG channels, no aux), start, resume: muse-js's start(). */
    val START_COMMANDS = listOf("h", "p21", "s", "d")
    const val STOP_COMMAND = "h"

    private fun uuid16(short: Int): UUID = UUID.fromString(String.format("%08x-0000-1000-8000-00805f9b34fb", short))

    /** A control command: length byte (text + newline), the text, a newline. */
    fun command(text: String): ByteArray {
        val body = (text + "\n").toByteArray(Charsets.US_ASCII)
        return byteArrayOf(body.size.toByte()) + body
    }

    class EegPacket(val counter: Int, val samples: DoubleArray)
    class GyroPacket(val counter: Int, val xyz: List<DoubleArray>)

    fun eeg(bytes: ByteArray): EegPacket? {
        if (bytes.size < 2 + SAMPLES_PER_PACKET * 3 / 2) return null
        val out = DoubleArray(SAMPLES_PER_PACKET)
        var o = 0
        var i = 2
        while (o < SAMPLES_PER_PACKET) {
            val a = u8(bytes[i]); val b = u8(bytes[i + 1]); val c = u8(bytes[i + 2])
            out[o++] = EEG_SCALE * (((a shl 4) or (b shr 4)) - 0x800)
            out[o++] = EEG_SCALE * ((((b and 0xf) shl 8) or c) - 0x800)
            i += 3
        }
        return EegPacket(u16(bytes, 0), out)
    }

    fun gyro(bytes: ByteArray): GyroPacket? {
        if (bytes.size < 2 + GYRO_PER_PACKET * 6) return null
        val xyz = (0 until GYRO_PER_PACKET).map { s ->
            DoubleArray(3) { axis -> GYRO_SCALE * s16(bytes, 2 + s * 6 + axis * 2) }
        }
        return GyroPacket(u16(bytes, 0), xyz)
    }

    private fun u8(b: Byte) = b.toInt() and 0xff
    private fun u16(b: ByteArray, at: Int) = (u8(b[at]) shl 8) or u8(b[at + 1])
    private fun s16(b: ByteArray, at: Int) = u16(b, at).toShort().toInt()
}
