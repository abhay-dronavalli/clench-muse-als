package com.clench.eyetrack.board

import com.clench.eyetrack.board.GazeMath.Frac
import com.clench.eyetrack.board.GazeMath.ViewRect
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Locale

class GazeMathTest {

    @Test
    fun screenPixelsBecomeFractionsOfTheWebView() {
        // WebView below a 100 px bar on a 2960 x 1848 screen.
        val view = ViewRect(0f, 100f, 2960f, 1748f)
        val f = GazeMath.toFraction(1480f, 100f + 874f, view)
        assertEquals(0.5f, f.x, 1e-6f)
        assertEquals(0.5f, f.y, 1e-6f)
        assertEquals(0f, GazeMath.toFraction(0f, 100f, view).y, 1e-6f)
    }

    @Test
    fun feedJsUsesDotsEvenInACommaLocale() {
        val before = Locale.getDefault()
        Locale.setDefault(Locale.GERMANY)
        try {
            val js = GazeMath.feedJs(Frac(0.25f, 0.75f), true, "SUCCESS")
            assertEquals(
                "window.clenchGaze&&window.clenchGaze.feed({x:0.2500,y:0.7500,found:true,confidence:1,state:\"SUCCESS\"})",
                js,
            )
        } finally {
            Locale.setDefault(before)
        }
    }

    @Test
    fun lostEyesSendFoundFalseAndNoConfidence() {
        val js = GazeMath.feedJs(Frac(Float.NaN, 0.5f), false, "FACE_MISSING")
        assertTrue(js.contains("x:0.5,"))
        assertTrue(js.contains("found:false,confidence:0"))
    }

    @Test
    fun stringsAreEscapedForJavaScript() {
        assertEquals("\"a\\\"b\\\\c\\nd\\u2028\"", GazeMath.str("a\"b\\c\nd "))
        val js = GazeMath.eventJs("calibration", mapOf("state" to GazeMath.str("finished"), "person" to GazeMath.str("O'Neil")))
        assertEquals(
            "window.clenchNativeEvent&&window.clenchNativeEvent({type:\"calibration\",state:\"finished\",person:\"O'Neil\"})",
            js,
        )
    }

    @Test
    fun throttleLetsOneSampleThroughPerInterval() {
        val t = GazeMath.Throttle(33)
        assertTrue(t.allow(1000))
        assertFalse(t.allow(1020))
        assertTrue(t.allow(1033))
        assertFalse(t.allow(1034))
    }

    @Test
    fun validationUsesTheMedianAndHalfATile() {
        val target = Frac(0.5f, 0.25f)
        val near = List(12) { Frac(0.52f, 0.27f) }
        assertTrue(GazeMath.validationPasses(near, target, 1f / 6, 0.25f))
        // A few wild samples do not decide it.
        assertTrue(GazeMath.validationPasses(near + List(3) { Frac(0f, 1f) }, target, 1f / 6, 0.25f))
        // Mostly on the next tile over: fail.
        assertFalse(GazeMath.validationPasses(List(12) { Frac(0.8f, 0.25f) }, target, 1f / 6, 0.25f))
        // Too few tracked samples (eyes not seen): fail, so the person recalibrates.
        assertFalse(GazeMath.validationPasses(near.take(5), target, 1f / 6, 0.25f))
    }

    @Test
    fun calibrationDataRoundTrips() {
        val data = doubleArrayOf(1.5, -2.25e-3, 0.0)
        assertArrayEquals(data, GazeMath.decode(GazeMath.encode(data)), 0.0)
        assertNull(GazeMath.decode(""))
        assertNull(GazeMath.decode("1,x"))
    }
}
