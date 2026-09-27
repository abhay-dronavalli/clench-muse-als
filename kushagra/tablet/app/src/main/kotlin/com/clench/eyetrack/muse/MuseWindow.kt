package com.clench.eyetrack.muse

import kotlin.math.sqrt

/** One reading for the detector: the jaw level, each channel's spread for the console chart, and why
 *  a gesture must not count right now (null = it may). sensor/sources/muse.py's Sample. */
data class Sample(val level: Double, val channels: List<Double>, val blocked: String? = null)

/** The EEG stream stopped: the caller drops the headband and reconnects. */
class StreamStopped(message: String) : Exception(message)

/**
 * The last second of Muse data, as BrainFlow's ring buffer holds it for sensor/sources/muse.py, and
 * read() with the same checks in the same order: enough samples, a live stream, no gap, contact on
 * TP9 and TP10, a live gyroscope, then the jaw level and the head-motion block.
 *
 * Bluetooth callbacks write and the sensor loop reads, on different threads; every method locks.
 * Times are milliseconds on any one monotonic clock.
 */
class MuseWindow(private val motionLimit: Double = 30.0) {
    private class Channel {
        val values = DoubleArray(WINDOW)
        val index = LongArray(WINDOW) // absolute sample number, from the packet counter
        var count = 0
        var head = 0 // next write position
        var lastCounter = -1
        var lastIndex = 0L
        var lastAt = Long.MIN_VALUE

        fun clear() {
            count = 0; head = 0; lastCounter = -1; lastAt = Long.MIN_VALUE
        }

        fun add(p: MuseProtocol.EegPacket, now: Long) {
            val first: Long
            if (lastCounter < 0) {
                first = 0L
            } else {
                val delta = (p.counter - lastCounter) and 0xffff
                if (delta == 0) return // a repeat
                if (delta >= 0x8000) { // went backwards: the headband restarted its count
                    clear()
                    add(p, now)
                    return
                }
                first = lastIndex + (delta - 1) * MuseProtocol.SAMPLES_PER_PACKET + 1
            }
            for (i in p.samples.indices) {
                values[head] = p.samples[i]
                index[head] = first + i
                head = (head + 1) % WINDOW
                if (count < WINDOW) count++
            }
            lastCounter = p.counter
            lastIndex = first + p.samples.size - 1
            lastAt = now
        }

        /** oldest first */
        fun ordered(): Pair<DoubleArray, LongArray> {
            val v = DoubleArray(count); val ix = LongArray(count)
            val start = (head - count + WINDOW) % WINDOW
            for (i in 0 until count) {
                v[i] = values[(start + i) % WINDOW]; ix[i] = index[(start + i) % WINDOW]
            }
            return v to ix
        }
    }

    private val eeg = Array(4) { Channel() }
    private val gyroNorm = DoubleArray(GYRO_WINDOW)
    private var gyroCount = 0
    private var gyroHead = 0
    private var gyroAt = Long.MIN_VALUE
    private var openedAt = 0L

    /** Forget everything (a new connection). [now] starts the grace for the first samples. */
    @Synchronized fun clear(now: Long) {
        eeg.forEach { it.clear() }
        gyroCount = 0; gyroHead = 0; gyroAt = Long.MIN_VALUE
        openedAt = now
    }

    @Synchronized fun onEeg(channel: Int, packet: MuseProtocol.EegPacket, now: Long) = eeg[channel].add(packet, now)

    @Synchronized fun onGyro(packet: MuseProtocol.GyroPacket, now: Long) {
        for (xyz in packet.xyz) {
            gyroNorm[gyroHead] = sqrt(xyz[0] * xyz[0] + xyz[1] * xyz[1] + xyz[2] * xyz[2])
            gyroHead = (gyroHead + 1) % GYRO_WINDOW
            if (gyroCount < GYRO_WINDOW) gyroCount++
        }
        gyroAt = now
    }

    @Synchronized fun read(now: Long): Sample {
        if (eeg.any { it.count < WINDOW }) {
            // BrainFlow would have failed to open a headband that never streams; say so here instead.
            val last = eeg.maxOf { it.lastAt }
            if (now - maxOf(last, openedAt) > FIRST_DATA_MS) throw StreamStopped("no EEG samples")
            return Sample(0.0, emptyList(), "Waiting for EEG samples")
        }
        if (eeg.any { now - it.lastAt > STALE_MS }) throw StreamStopped("EEG stream stopped")
        val rows = eeg.map { it.ordered() }
        // A dropped packet or two is fine (BrainFlow's 0.1 s timestamp tolerance); more is a gap.
        if (rows.any { (_, ix) -> ix.last() - ix.first() - (WINDOW - 1) > GAP_SAMPLES }) {
            return Sample(0.0, emptyList(), "EEG samples interrupted; waiting for continuous data")
        }
        for ((row, label) in listOf(0 to "TP9", 3 to "TP10")) {
            val values = rows[row].first
            val min = values.min(); val max = values.max()
            val clipped = maxOf(values.count { it == min }, values.count { it == max }).toDouble() / values.size
            if (values.any { !it.isFinite() } || std(values) < 1 || clipped > 0.2) {
                return Sample(0.0, emptyList(), "$label poor contact — adjust band")
            }
        }
        // Suppress the whole gesture during head motion (the Core also refuses while blocked).
        if (gyroCount < MIN_GYRO) return Sample(0.0, emptyList(), "Waiting for motion sensor")
        if (now - gyroAt > STALE_MS) return Sample(0.0, emptyList(), "Motion sensor unavailable")
        val level = maxOf(EmgFilter.envelope(rows[0].first), EmgFilter.envelope(rows[3].first))
        val channels = rows.map { std(it.first) }.takeIf { c -> c.all { it.isFinite() } } ?: emptyList()
        val moving = (0 until gyroCount).any { gyroNorm[it] > motionLimit }
        return Sample(level, channels, if (moving) "Head moving — hold still to clench" else null)
    }

    companion object {
        const val WINDOW = 256 // 1 s at 256 Hz
        private const val GYRO_WINDOW = 16
        private const val MIN_GYRO = 8
        private const val STALE_MS = 1_000L
        private const val FIRST_DATA_MS = 3_000L
        private const val GAP_SAMPLES = 25 // 0.1 s at 256 Hz

        /** numpy's std (population) */
        fun std(v: DoubleArray): Double {
            val mean = v.average()
            return sqrt(v.sumOf { (it - mean) * (it - mean) } / v.size)
        }
    }
}
