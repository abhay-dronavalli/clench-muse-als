package com.clench.eyetrack.car

import android.graphics.Color
import com.google.android.filament.MaterialInstance
import dev.romainguy.kotlin.math.Float3
import io.github.sceneview.SceneView
import io.github.sceneview.node.CubeNode
import io.github.sceneview.node.CylinderNode
import io.github.sceneview.node.Node
import io.github.sceneview.node.SphereNode
import kotlin.random.Random

/**
 * The trip scene's world, built from WorldPlan: grass, the road with its lines, and a random mix of
 * round trees, pines, cypresses, bushes, houses, flowers, rocks, clouds and far hills, plus a sun. Each
 * frame `update` moves everything past the parked car (far things slower, clouds drifting on their own)
 * and re-rolls whatever has just wrapped around behind it. Main thread only.
 */
class CarWorld(private val view: SceneView, private val ground: Float, seed: Long) {
    private val engine = view.engine
    private val rng = Random(seed)
    private val materials = HashMap<String, MaterialInstance>()

    private class Placed(val piece: WorldPlan.Piece, val node: Node, var lap: Int)
    private val placed = mutableListOf<Placed>()
    private val dashes = mutableListOf<Node>()
    private val all = mutableListOf<Node>() // every top-level node, to show or hide the whole world

    init {
        val g = ground
        add(CubeNode(engine, Float3(90f, 0.02f, 90f), Float3(0f, g - 0.011f, 0f), color("grass", 104, 170, 84)))
        add(CubeNode(engine, Float3(90f, 0.004f, WorldPlan.ROAD_WIDTH), Float3(0f, g + 0.001f, WorldPlan.ROAD_Z), color("road", 72, 74, 80)))
        for (edge in listOf(WorldPlan.ROAD_LEFT + 0.03f, WorldPlan.ROAD_RIGHT - 0.03f)) {
            add(CubeNode(engine, Float3(90f, 0.005f, 0.018f), Float3(0f, g + 0.002f, edge), paint()))
        }
        repeat(DASHES) {
            val dash = CubeNode(engine, Float3(0.3f, 0.005f, 0.022f), Float3(0f, 0f, 0f), paint())
            add(dash)
            dashes += dash
        }
        // The sun: far away, high, off to one side.
        add(SphereNode(engine, 2.4f, Float3(38f, 16f, -42f), 16, 20, color("sun", 255, 236, 150)))
        for (piece in WorldPlan.plan(seed)) {
            val node = build(piece)
            add(node)
            placed += Placed(piece, node, WorldPlan.lap(piece, 0f))
            place(piece, node, 0f, 0f)
        }
    }

    /** Move the world for `travel` (how far the car has gone) and `seconds` (clouds drift in time). */
    fun update(travel: Float, seconds: Float) {
        val dashSpan = DASHES * DASH_GAP
        dashes.forEachIndexed { i, d ->
            d.position = Float3(CarMotion.wrap(-dashSpan / 2 + i * DASH_GAP, travel, dashSpan), ground + 0.002f, WorldPlan.ROAD_Z)
        }
        for (p in placed) {
            val drift = if (p.piece.kind == WorldPlan.Kind.CLOUD) seconds * CLOUD_DRIFT else 0f
            val lap = WorldPlan.lap(p.piece, travel + drift / p.piece.parallax)
            if (lap != p.lap) { // just wrapped around behind the car: a fresh look for its next pass
                p.lap = lap
                WorldPlan.reroll(p.piece, rng)
            }
            place(p.piece, p.node, travel, drift)
        }
    }

    private fun place(p: WorldPlan.Piece, node: Node, travel: Float, drift: Float) {
        val x = CarMotion.wrap(p.baseX, travel * p.parallax + drift, p.span)
        val y = when (p.kind) {
            WorldPlan.Kind.CLOUD -> 2.4f + 0.5f * p.scale
            WorldPlan.Kind.HILL -> ground - 0.72f * p.scale
            else -> ground
        }
        node.position = Float3(x, y, p.z)
        node.scale = Float3(p.scale, p.scale, p.scale)
        val turn = if (p.kind == WorldPlan.Kind.HOUSE) (if (p.z > 0) 180f else 0f) + (p.turnDeg % 24f - 12f) else p.turnDeg
        node.rotation = Float3(0f, turn, 0f)
    }

    // --- the pieces -------------------------------------------------------------------------

    private fun build(p: WorldPlan.Piece): Node {
        val n = Node(engine)
        when (p.kind) {
            WorldPlan.Kind.ROUND_TREE -> {
                n.addChildNode(CylinderNode(engine, 0.025f, 0.24f, Float3(0f, 0.12f, 0f), 10, bark()))
                n.addChildNode(SphereNode(engine, 0.17f, Float3(0f, 0.37f, 0f), 14, 18, leaf(p.shade)))
            }
            WorldPlan.Kind.PINE -> {
                n.addChildNode(CylinderNode(engine, 0.02f, 0.1f, Float3(0f, 0.05f, 0f), 8, bark()))
                // Tiers narrowing upward: a stylised pine.
                listOf(0.16f to 0.12f, 0.12f to 0.2f, 0.085f to 0.28f, 0.045f to 0.35f).forEach { (r, y) ->
                    n.addChildNode(CylinderNode(engine, r, 0.09f, Float3(0f, y, 0f), 12, pine(p.shade)))
                }
            }
            WorldPlan.Kind.CYPRESS -> {
                n.addChildNode(CylinderNode(engine, 0.018f, 0.08f, Float3(0f, 0.04f, 0f), 8, bark()))
                n.addChildNode(Node(engine).apply {
                    position = Float3(0f, 0.3f, 0f)
                    scale = Float3(1f, 2.6f, 1f)
                    addChildNode(SphereNode(engine, 0.09f, Float3(0f, 0f, 0f), 12, 16, pine(p.shade)))
                })
            }
            WorldPlan.Kind.BUSH -> {
                listOf(Float3(0f, 0.07f, 0f) to 0.1f, Float3(0.09f, 0.05f, 0.03f) to 0.075f, Float3(-0.08f, 0.05f, -0.03f) to 0.08f)
                    .forEach { (c, r) -> n.addChildNode(SphereNode(engine, r, c, 12, 14, leaf(p.shade))) }
            }
            WorldPlan.Kind.HOUSE -> {
                val wall = pick("wall", p.shade, WALLS)
                n.addChildNode(CubeNode(engine, Float3(0.5f, 0.3f, 0.4f), Float3(0f, 0.15f, 0f), wall))
                // A pitched roof: a long box turned 45° about its length.
                n.addChildNode(Node(engine).apply {
                    position = Float3(0f, 0.3f, 0f)
                    rotation = Float3(45f, 0f, 0f)
                    addChildNode(CubeNode(engine, Float3(0.56f, 0.28f, 0.28f), Float3(0f, 0f, 0f), pick("roof", p.shade, ROOFS)))
                })
                n.addChildNode(CubeNode(engine, Float3(0.08f, 0.13f, 0.01f), Float3(0.08f, 0.065f, 0.201f), color("door", 110, 76, 56)))
                n.addChildNode(CubeNode(engine, Float3(0.09f, 0.07f, 0.01f), Float3(-0.12f, 0.17f, 0.201f), color("glass", 190, 220, 240)))
            }
            WorldPlan.Kind.FLOWER -> {
                n.addChildNode(CylinderNode(engine, 0.003f, 0.03f, Float3(0f, 0.015f, 0f), 6, color("stem", 70, 130, 60)))
                n.addChildNode(SphereNode(engine, 0.012f, Float3(0f, 0.033f, 0f), 8, 10, pick("flower", p.shade, FLOWERS)))
            }
            WorldPlan.Kind.ROCK -> n.addChildNode(Node(engine).apply {
                scale = Float3(1.2f, 0.6f, 1f)
                addChildNode(SphereNode(engine, 0.035f, Float3(0f, 0.01f, 0f), 8, 10, pick("rock", p.shade, ROCKS)))
            })
            WorldPlan.Kind.CLOUD -> {
                listOf(Float3(0f, 0f, 0f) to 0.5f, Float3(0.55f, -0.1f, 0.1f) to 0.38f, Float3(-0.5f, -0.12f, -0.05f) to 0.4f, Float3(0.2f, 0.2f, 0f) to 0.34f)
                    .forEach { (c, r) -> n.addChildNode(SphereNode(engine, r, c, 12, 16, color("cloud", 255, 255, 255))) }
            }
            WorldPlan.Kind.HILL -> n.addChildNode(SphereNode(engine, 1f, Float3(0f, 0f, 0f), 18, 24, pick("hill", p.shade, HILLS)))
        }
        return n
    }

    private fun add(node: Node) {
        view.addChildNode(node)
        all += node
    }

    /** Show or hide the whole world (the onboarding's preview shows the car alone). */
    fun setVisible(on: Boolean) = all.forEach { it.showAll(on) }

    private fun Node.showAll(on: Boolean) {
        isVisible = on
        childNodes.forEach { it.showAll(on) }
    }

    private fun paint() = color("paint", 242, 242, 236)
    private fun bark() = color("bark", 110, 78, 52)
    private fun leaf(shade: Int) = pick("leaf", shade, LEAVES)
    private fun pine(shade: Int) = pick("pine", shade, PINES)

    private fun pick(key: String, shade: Int, palette: List<Triple<Int, Int, Int>>): MaterialInstance {
        val (r, g, b) = palette[shade % palette.size]
        return color("$key$shade", r, g, b)
    }

    private fun color(key: String, r: Int, g: Int, b: Int): MaterialInstance = materials.getOrPut(key) {
        view.materialLoader.createColorInstance(Color.rgb(r, g, b), 0f, 0.85f, 0.1f)
    }

    companion object {
        private const val DASHES = 24
        private const val DASH_GAP = 0.75f
        private const val CLOUD_DRIFT = 0.04f // units a second the clouds drift on their own
        private val LEAVES = listOf(Triple(52, 112, 58), Triple(66, 130, 60), Triple(92, 142, 62), Triple(44, 100, 64))
        private val PINES = listOf(Triple(30, 86, 56), Triple(38, 96, 62), Triple(28, 76, 50), Triple(46, 104, 66))
        private val WALLS = listOf(
            Triple(238, 226, 204), Triple(214, 229, 240), Triple(245, 214, 196),
            Triple(222, 238, 214), Triple(250, 238, 190), Triple(230, 215, 236),
        )
        private val ROOFS = listOf(Triple(164, 72, 56), Triple(112, 92, 82), Triple(82, 88, 98))
        private val FLOWERS = listOf(Triple(240, 92, 112), Triple(250, 200, 60), Triple(255, 255, 255), Triple(172, 122, 232), Triple(255, 142, 62))
        private val ROCKS = listOf(Triple(140, 140, 136), Triple(120, 118, 112), Triple(160, 156, 148))
        private val HILLS = listOf(Triple(110, 172, 92), Triple(96, 160, 84), Triple(124, 182, 100))
    }
}
