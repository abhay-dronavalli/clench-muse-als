package com.clench.eyetrack.car

import kotlin.math.abs
import kotlin.random.Random

/**
 * What the trip scene's world is made of and where it goes: plain data and maths, unit tested.
 * CarWorld turns it into SceneView nodes.
 *
 * Random each trip (a new seed), so no two rides look alike, and each piece is re-rolled (its setback,
 * size, turn, colour shade) every time it wraps around behind the car, so the road never visibly
 * repeats. Rules that keep it calm and readable:
 *   - Anything taller than a flower stands beyond the camera's circle (CAMERA_RADIUS): nothing ever
 *     comes between the camera and the car.
 *   - Only low things (flowers, rocks) sit near the road.
 *   - No traffic: a car sweeping past the circling camera would be a jolt.
 *   - Far things move slower than near ones (parallax), clouds drift on their own.
 */
object WorldPlan {
    enum class Kind { ROUND_TREE, PINE, CYPRESS, BUSH, HOUSE, FLOWER, ROCK, CLOUD, HILL }

    /** One piece: its kind, where it starts along the road, its lateral place, size and shade. */
    data class Piece(
        val kind: Kind,
        val baseX: Float,
        var z: Float,
        var scale: Float,
        var turnDeg: Float,
        var shade: Int, // index into the kind's colour palette
        val span: Float, // it wraps around over this length of road
        val parallax: Float, // 1 = moves with the road, less = further away
    )

    const val CAMERA_RADIUS = 2.3f // CarScene's orbit
    const val ROAD_Z = -0.29f
    const val ROAD_WIDTH = 1.15f
    private const val CLEAR = CAMERA_RADIUS + 0.6f // tall things stand at least this far from the car

    val ROAD_LEFT = ROAD_Z - ROAD_WIDTH / 2
    val ROAD_RIGHT = ROAD_Z + ROAD_WIDTH / 2

    fun plan(seed: Long): List<Piece> {
        val rng = Random(seed)
        val out = mutableListOf<Piece>()
        fun add(kind: Kind, count: Int, span: Float, parallax: Float) {
            repeat(count) { i ->
                val p = Piece(kind, -span / 2 + span * (i + rng.nextFloat() * 0.8f) / count, 0f, 1f, 0f, 0, span, parallax)
                reroll(p, rng)
                out += p
            }
        }
        add(Kind.ROUND_TREE, 14, 26f, 1f)
        add(Kind.PINE, 10, 26f, 1f)
        add(Kind.CYPRESS, 6, 26f, 1f)
        add(Kind.BUSH, 10, 22f, 1f)
        add(Kind.HOUSE, 7, 30f, 1f)
        add(Kind.FLOWER, 44, 14f, 1f)
        add(Kind.ROCK, 14, 16f, 1f)
        add(Kind.CLOUD, 7, 60f, 0.12f)
        add(Kind.HILL, 9, 120f, 0.05f)
        return out
    }

    /** New setback, size, turn and shade for a piece (when it is made, and each time it wraps). */
    fun reroll(p: Piece, rng: Random) {
        val side = if (rng.nextBoolean()) 1f else -1f
        fun beyond(min: Float, extra: Float) = side * (min + rng.nextFloat() * extra)
        when (p.kind) {
            Kind.ROUND_TREE, Kind.PINE, Kind.CYPRESS -> {
                p.z = beyond(CLEAR, 3.5f); p.scale = 0.8f + rng.nextFloat() * 0.6f; p.shade = rng.nextInt(4)
            }
            Kind.BUSH -> { p.z = beyond(CLEAR, 2.5f); p.scale = 0.7f + rng.nextFloat() * 0.6f; p.shade = rng.nextInt(4) }
            Kind.HOUSE -> { p.z = beyond(CLEAR + 1.2f, 3f); p.scale = 0.8f + rng.nextFloat() * 0.5f; p.shade = rng.nextInt(6) }
            Kind.FLOWER -> {
                // In the grass beside the road, on either side.
                p.z = if (side > 0) ROAD_RIGHT + 0.12f + rng.nextFloat() * 1.6f else ROAD_LEFT - 0.12f - rng.nextFloat() * 1.6f
                p.scale = 0.7f + rng.nextFloat() * 0.6f; p.shade = rng.nextInt(5)
            }
            Kind.ROCK -> {
                p.z = if (side > 0) ROAD_RIGHT + 0.15f + rng.nextFloat() * 2f else ROAD_LEFT - 0.15f - rng.nextFloat() * 2f
                p.scale = 0.6f + rng.nextFloat() * 0.8f; p.shade = rng.nextInt(3)
            }
            Kind.CLOUD -> { p.z = beyond(6f, 10f); p.scale = 0.8f + rng.nextFloat() * 0.9f; p.shade = 0 }
            Kind.HILL -> { p.z = beyond(18f, 14f); p.scale = 3f + rng.nextFloat() * 4f; p.shade = rng.nextInt(3) }
        }
        p.turnDeg = rng.nextFloat() * 360f
    }

    /** How tall a piece stands above the ground (units), for the keep-clear rule. */
    fun height(kind: Kind): Float = when (kind) {
        Kind.FLOWER -> 0.03f
        Kind.ROCK -> 0.04f
        else -> 0.3f
    }

    /** True when nothing tall of `pieces` stands inside the camera's circle (a test and a guard). */
    fun keepsClear(pieces: List<Piece>): Boolean =
        pieces.filter { height(it.kind) > 0.1f }.all { abs(it.z) > CAMERA_RADIUS + 0.3f }

    /** Which lap a piece is on at `travel` (it changes when the piece wraps around: time to re-roll). */
    fun lap(p: Piece, travel: Float): Int =
        kotlin.math.floor((p.baseX - travel * p.parallax + p.span / 2) / p.span).toInt()
}
