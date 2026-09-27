package com.clench.eyetrack.muse

import kotlin.math.sqrt

/**
 * sensor/detect/clench.py's envelope() without BrainFlow: how much jaw-muscle energy (20-110 Hz)
 * one channel carries right now, in uV RMS. The calibrated thresholds in test/calibration.<name>.json
 * are in these units, so this must give the Python numbers, not just similar ones.
 *
 * BrainFlow's chain, reproduced exactly (EmgFilterTest checks it against tools/muse_golden.py):
 *   1. remove_environmental_noise(FIFTY_AND_SIXTY): a zero-phase 48-52 Hz band-stop, then a
 *      single-pass 58-62 Hz band-stop (BrainFlow's own choice for the second one).
 *   2. perform_bandpass(20, 110, order 4, BUTTERWORTH_ZERO_PHASE).
 *   BrainFlow's zero-phase pass runs the reversed signal through the same filter without resetting
 *   its state, unlike scipy's filtfilt, and so does [zeroPhase].
 *   3. RMS over the last 0.2 s, minus the last 20 ms (the filter's wobbly edge).
 *
 * The sections below are scipy.signal.butter(..., output='sos') at 256 Hz, printed by
 * tools/muse_golden.py. Each row is b0, b1, b2, a1, a2 (a0 = 1).
 */
object EmgFilter {
    const val FS = 256
    private const val EDGE = 5 // int(256 * 0.02)
    private const val LENGTH = 51 // int(256 * 0.20)

    private val NOTCH50 = arrayOf(
        doubleArrayOf(0.8795613790064436, -0.5933453184674293, 0.8795613790064436, -0.6110117503992638, 0.9126362671438812),
        doubleArrayOf(1.0, -0.6745922827326444, 1.0, -0.6791783724905169, 0.9137559992715935),
        doubleArrayOf(1.0, -0.6745922827326444, 1.0, -0.5768119255441487, 0.9625810949082861),
        doubleArrayOf(1.0, -0.6745922827326444, 1.0, -0.7448529029446496, 0.9637558131969012),
    )
    private val NOTCH60 = arrayOf(
        doubleArrayOf(0.8795613790064437, -0.17263212518425156, 0.8795613790064435, -0.15184793143344846, 0.9130419006533645),
        doubleArrayOf(1.0, -0.19627069730967217, 0.9999999999999998, -0.22352919866928042, 0.9133500485123455),
        doubleArrayOf(1.0, -0.19627069730967217, 0.9999999999999998, -0.10365323324735282, 0.9630066158314314),
        doubleArrayOf(1.0, -0.19627069730967217, 0.9999999999999998, -0.2808813725242526, 0.9633299612281019),
    )
    private val BAND = arrayOf(
        doubleArrayOf(0.2794971290576483, -0.5589942581152966, 0.2794971290576483, -1.1700860165484241, 0.3650688067398495),
        doubleArrayOf(1.0, 2.0, 1.0, 1.241923007693957, 0.4055714091000679),
        doubleArrayOf(1.0, -2.0, 1.0, -1.5085448182219403, 0.7165220429449712),
        doubleArrayOf(1.0, 2.0, 1.0, 1.567970036959096, 0.7393825294630025),
    )

    /** The window after the hum filters and the band-pass (a new array; [window] is not changed). */
    fun filtered(window: DoubleArray): DoubleArray {
        val x = window.copyOf()
        zeroPhase(NOTCH50, x)
        run(NOTCH60, x, Array(NOTCH60.size) { DoubleArray(2) })
        zeroPhase(BAND, x)
        return x
    }

    /** uV RMS of the filtered window's recent tail. Expects 256 Hz samples (the Muse 2). */
    fun envelope(window: DoubleArray): Double {
        val x = filtered(window)
        val end = x.size - EDGE
        val start = maxOf(0, end - LENGTH)
        if (end <= start) return 0.0
        var sum = 0.0
        for (i in start until end) sum += x[i] * x[i]
        return sqrt(sum / (end - start))
    }

    private fun zeroPhase(sos: Array<DoubleArray>, x: DoubleArray) {
        val state = Array(sos.size) { DoubleArray(2) }
        run(sos, x, state)
        x.reverse()
        run(sos, x, state) // same state: BrainFlow does not reset between the passes
        x.reverse()
    }

    /** Cascaded biquads, transposed direct form II (scipy's sosfilt), in place. */
    private fun run(sos: Array<DoubleArray>, x: DoubleArray, state: Array<DoubleArray>) {
        for (s in sos.indices) {
            val (b0, b1, b2, a1, a2) = sos[s]
            val z = state[s]
            for (i in x.indices) {
                val v = x[i]
                val y = b0 * v + z[0]
                z[0] = b1 * v - a1 * y + z[1]
                z[1] = b2 * v - a2 * y
                x[i] = y
            }
        }
    }
}
