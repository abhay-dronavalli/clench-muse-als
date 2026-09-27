package com.clench.eyetrack.board

import java.util.Locale
import kotlin.math.abs

/**
 * Pure helpers for the board shell (unit tested): screen pixels to board fractions, the JavaScript
 * the shell runs in the page, the feed throttle, and the one-target check of a saved calibration.
 * The page side is web/src/facetrack/native.ts and gaze.ts (docs/eye-tracking.md, "Native shell").
 */
object GazeMath {

    /** Where a view sits on the screen, in screen pixels (getLocationOnScreen + width/height). */
    data class ViewRect(val left: Float, val top: Float, val width: Float, val height: Float)

    data class Frac(val x: Float, val y: Float)

    /**
     * An Eyedid gaze point (screen pixels, origin at the screen's top-left) as fractions of the
     * WebView, which is what gaze.feed() takes. Not clamped: the page clamps.
     */
    fun toFraction(x: Float, y: Float, view: ViewRect): Frac =
        Frac((x - view.left) / view.width, (y - view.top) / view.height)

    /** A number for JavaScript: always a dot, never a locale comma; non-finite becomes 0.5. */
    fun num(v: Float): String = if (v.isFinite()) String.format(Locale.US, "%.4f", v) else "0.5"

    /** A JSON string literal (also valid JavaScript). */
    fun str(s: String): String {
        val b = StringBuilder("\"")
        for (c in s) {
            when {
                c == '"' -> b.append("\\\"")
                c == '\\' -> b.append("\\\\")
                c == '\n' -> b.append("\\n")
                c == '\r' -> b.append("\\r")
                c == ' ' || c == ' ' || c < ' ' -> b.append(String.format(Locale.US, "\\u%04x", c.code))
                else -> b.append(c)
            }
        }
        return b.append('"').toString()
    }

    /** One gaze sample for the page's gaze slot. */
    fun feedJs(f: Frac, found: Boolean, state: String): String =
        "window.clenchGaze&&window.clenchGaze.feed({x:${num(f.x)},y:${num(f.y)}," +
            "found:$found,confidence:${if (found) 1 else 0},state:${str(state)}})"

    /** A NativeEvent for window.clenchNativeEvent; `fields` values are already JavaScript. */
    fun eventJs(type: String, fields: Map<String, String>): String {
        val body = (listOf("type:${str(type)}") + fields.map { (k, v) -> "$k:$v" }).joinToString(",")
        return "window.clenchNativeEvent&&window.clenchNativeEvent({$body})"
    }

    /** Lets through at most one sample per `intervalMs` (the page gets about 30 a second). */
    class Throttle(private val intervalMs: Long) {
        private var last = Long.MIN_VALUE
        fun allow(nowMs: Long): Boolean {
            if (last != Long.MIN_VALUE && nowMs - last < intervalMs) return false
            last = nowMs
            return true
        }
    }

    /**
     * The check after loading a saved calibration: while one target is shown, does the gaze land on
     * the tile around it? `halfW`, `halfH` = half a tile, in the same units as the points. Uses the
     * median of the tracked samples so a few stray ones do not decide it.
     */
    fun validationPasses(samples: List<Frac>, target: Frac, halfW: Float, halfH: Float, minSamples: Int = 10): Boolean {
        if (samples.size < minSamples) return false
        val mx = median(samples.map { it.x })
        val my = median(samples.map { it.y })
        return abs(mx - target.x) <= halfW && abs(my - target.y) <= halfH
    }

    private fun median(v: List<Float>): Float {
        val s = v.sorted()
        return if (s.size % 2 == 1) s[s.size / 2] else (s[s.size / 2 - 1] + s[s.size / 2]) / 2f
    }

    /** Saved calibration data (Eyedid's double[]) to and from a SharedPreferences string. */
    fun encode(data: DoubleArray): String = data.joinToString(",") { String.format(Locale.US, "%s", it) }

    fun decode(s: String?): DoubleArray? {
        if (s.isNullOrBlank()) return null
        return try {
            s.split(",").map { it.trim().toDouble() }.toDoubleArray()
        } catch (e: NumberFormatException) {
            null
        }
    }
}
