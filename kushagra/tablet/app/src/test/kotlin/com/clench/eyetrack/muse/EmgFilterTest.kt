package com.clench.eyetrack.muse

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The tablet's envelope must be the Python sensor's (BrainFlow) number: the thresholds depend on it.
 *  Reference windows: tools/muse_golden.py, run through sensor/detect/clench.py. */
class EmgFilterTest {
    private val golden = JSONObject(javaClass.getResource("/muse/emg_golden.json")!!.readText())
    private val cases = golden.getJSONArray("cases").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }

    private fun doubles(o: JSONObject, key: String) = o.getJSONArray(key).let { a -> DoubleArray(a.length()) { a.getDouble(it) } }

    @Test
    fun envelopeMatchesBrainFlowOnEveryReferenceWindow() {
        assertTrue(cases.size >= 20)
        for ((i, c) in cases.withIndex()) {
            val expected = c.getDouble("envelope")
            assertEquals("window $i", expected, EmgFilter.envelope(doubles(c, "x")), 1e-6 + 1e-9 * expected)
        }
    }

    @Test
    fun filteredTailMatchesBrainFlowSampleBySample() {
        for ((i, c) in cases.withIndex()) {
            val tail = doubles(c, "tail")
            val out = EmgFilter.filtered(doubles(c, "x"))
            for (k in tail.indices) assertEquals("window $i sample $k", tail[k], out[out.size - tail.size + k], 1e-6)
        }
    }

    @Test
    fun aClenchBurstReadsFarAboveRest() {
        val t = DoubleArray(256) { it / 256.0 }
        val rest = DoubleArray(256) { 8 * kotlin.math.sin(2 * Math.PI * 35 * t[it]) }
        val clench = DoubleArray(256) { rest[it] + 75 * kotlin.math.sin(2 * Math.PI * 75 * t[it]) }
        assertTrue(EmgFilter.envelope(clench) > 5 * EmgFilter.envelope(rest))
    }

    @Test
    fun theInputIsNotChanged() {
        val x = DoubleArray(256) { (it % 7).toDouble() }
        val copy = x.copyOf()
        EmgFilter.envelope(x)
        assertTrue(x.contentEquals(copy))
    }
}
