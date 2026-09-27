package com.clench.eyetrack.car

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * How the trip screen's 3D car moves, as plain maths (no Android, unit tested): the idle sway, the
 * camera's short push toward the car, and each control's line particles. CarScene applies it to
 * SceneView nodes every frame.
 *
 * Motion rules (some riders have sensory or vestibular sensitivity): everything eases in and out, no
 * line ever appears or vanishes at full size (it grows from nothing and shrinks back), nothing
 * flashes, and the camera move is small.
 *
 * Car space: the model is centered on the origin and scaled so its longest side is 1 unit. `Box`
 * gives its half sizes; x is along the car (front = +x), z across it, y up.
 */
object CarMotion {
    data class Box(val halfLength: Float, val halfWidth: Float, val halfHeight: Float)

    /** One particle line: where its middle is, how long it is (0 = not drawn), and how it leans. */
    data class Line(val x: Float, val y: Float, val z: Float, val length: Float, val tiltDeg: Float = 0f)

    const val SWAY_DEG = 4f // idle sway either side of the resting angle
    const val SWAY_PERIOD_S = 9.0
    const val REST_YAW_DEG = -32f // a three-quarter view
    private const val LINE_LENGTH = 0.11f

    fun smooth(t: Float): Float {
        val x = t.coerceIn(0f, 1f)
        return x * x * (3 - 2 * x)
    }

    /** 0 -> 1 -> 0 over u in 0..1, zero slope at both ends (a line grows and shrinks, never pops). */
    fun bump(u: Float): Float = if (u <= 0f || u >= 1f) 0f else (0.5f - 0.5f * cos(2 * PI.toFloat() * u))

    /** The idle sway around the resting angle, degrees; `amount` 0..1 (0 = still, for Pull over). */
    fun yaw(seconds: Double, amount: Float): Float =
        REST_YAW_DEG + SWAY_DEG * amount * sin(2 * PI * seconds / SWAY_PERIOD_S).toFloat()

    /**
     * How far the camera moves toward the car at `progress` 0..1 of a control's sequence (units; the
     * camera starts about 1 unit away). Routine: in and back out, smoothly. Pull over: a quicker,
     * firmer push that holds (the car is stopping), released by the still period ending.
     */
    fun push(action: String, progress: Float): Float = if (action == "pull_over") {
        0.08f * smooth(progress / 0.35f)
    } else {
        0.14f * sin(PI.toFloat() * progress.coerceIn(0f, 1f))
    }

    /** The lines for `action` at `progress` 0..1 of its sequence. Pull over has none on purpose. */
    fun lines(action: String, progress: Float, box: Box): List<Line> = when (action) {
        "window_up" -> doorLines(progress, box, up = true)
        "window_down" -> doorLines(progress, box, up = false)
        "warmer" -> ventLines(progress, box, warm = true)
        "cooler" -> ventLines(progress, box, warm = false)
        "music" -> musicLines(progress, box)
        else -> emptyList()
    }

    /** Line `i` of `n`, starting a little after the one before: its own 0..1 time, or <0 / >1 outside it. */
    private fun local(progress: Float, i: Int, n: Int, life: Float = 0.6f): Float {
        val start = (1f - life) * i / max(n - 1, 1)
        return (progress - start) / life
    }

    /** A fixed, even spread in -1..1 for line `i` (no randomness: the same every time). */
    private fun spread(i: Int, n: Int): Float = if (n <= 1) 0f else -1f + 2f * i / (n - 1)

    /** Windows: lines trace the side of the car, rising (up) or falling (down) along the door glass. */
    private fun doorLines(progress: Float, box: Box, up: Boolean): List<Line> {
        val n = 6
        val bottom = 0.05f * box.halfHeight
        val top = 0.95f * box.halfHeight
        return (0 until n).map { i ->
            val u = local(progress, i, n)
            val travel = smooth(u)
            val y = if (up) bottom + (top - bottom) * travel else top - (top - bottom) * travel
            Line(
                x = 0.55f * box.halfLength * spread(i, n),
                y = y,
                z = box.halfWidth * 1.06f, // just outside the door facing the camera
                length = LINE_LENGTH * bump(u),
            )
        }
    }

    /** Heat: warm lines rise and spread outward from the vents; cool lines drift down and settle. */
    private fun ventLines(progress: Float, box: Box, warm: Boolean): List<Line> {
        val n = 10
        return (0 until n).map { i ->
            val u = local(progress, i, n, life = 0.55f)
            val t = smooth(u)
            val across = spread(i, n)
            val roof = box.halfHeight * 1.05f
            val y = if (warm) roof + 0.28f * t else roof + 0.32f * (1f - t) + 0.02f
            val outward = if (warm) 1f + 0.35f * t else 1f + 0.1f * (1f - t)
            Line(
                x = 0.15f * box.halfLength + 0.08f * spread((i * 3) % n, n),
                y = y,
                z = 0.8f * box.halfWidth * across * outward,
                length = LINE_LENGTH * 0.9f * bump(u),
                tiltDeg = 18f * across * (if (warm) t else 1f - t),
            )
        }
    }

    /** Music: a ring of short lines above the car pushing outward in three soft beats. */
    private fun musicLines(progress: Float, box: Box): List<Line> {
        val n = 12
        val beats = 3
        val beat = (progress.coerceIn(0f, 1f) * beats).let { it - it.toInt() }
        val pulse = bump(beat) // one smooth swell per beat
        // The whole ring grows in over the first fifth and shrinks away over the last (from and to nothing).
        val envelope = smooth(progress / 0.2f) * smooth((1f - progress) / 0.2f)
        val radius = 0.32f + 0.1f * pulse
        return (0 until n).map { i ->
            val angle = 2 * PI.toFloat() * i / n
            Line(
                x = radius * cos(angle),
                y = box.halfHeight * 1.1f + 0.06f,
                z = radius * sin(angle) * 0.7f,
                length = LINE_LENGTH * (0.35f + 0.65f * pulse) * envelope,
                tiltDeg = 90f - angle * 180f / PI.toFloat(),
            )
        }
    }

    /** Largest change of any line's length between two frames `dt` apart (the no-flicker check). */
    fun largestJump(action: String, box: Box, frames: Int): Float {
        var worst = 0f
        var before = lines(action, 0f, box)
        for (f in 1..frames) {
            val now = lines(action, f.toFloat() / frames, box)
            for (k in now.indices) worst = max(worst, abs(now[k].length - before[k].length))
            before = now
        }
        return worst
    }
}
