package com.clench.eyetrack.car

import android.graphics.Color
import android.util.Log
import android.view.View
import androidx.activity.ComponentActivity
import com.google.android.filament.MaterialInstance
import com.google.android.filament.Skybox
import dev.romainguy.kotlin.math.Float3
import io.github.sceneview.SceneView
import io.github.sceneview.node.CubeNode
import io.github.sceneview.node.CylinderNode
import io.github.sceneview.node.ModelNode
import io.github.sceneview.node.Node
import io.github.sceneview.node.SphereNode
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread
import kotlin.math.exp
import kotlin.math.max

/**
 * The trip screen's 3D car (SceneView / Filament), drawn full screen behind the transparent board
 * WebView. A daytime drive: sky, grass, a road with lane dashes, trees. The car stays put facing down
 * the road while the dashes and trees move past it and wrap around (it looks like driving), and the
 * camera makes one slow circle around the car every 45 s. The page (web/src/board/trip.tsx) drives it
 * through ClenchNative.carScene(on) and carEffect(action, ms) (CAR_ACTION); the maths is CarMotion.
 *
 * Pull over: the scenery and the camera ease to a stop (the car is stopping) and stay still a moment.
 *
 * Main thread only (Filament's rule). The GLB (app/src/main/assets/jaguar_i-pace.glb, not in git) is
 * read off the main thread the first time the car is shown; without it the scene shows the road alone.
 */
class CarScene(activity: ComponentActivity, private val asset: String = ASSET) {
    val view: SceneView = SceneView(
        context = activity,
        isOpaque = true,
        cameraManipulator = null, // the page above takes every touch; the camera is ours
        sharedLifecycle = activity.lifecycle,
    )

    private var car: ModelNode? = null
    private var box = CarMotion.Box(0.5f, 0.22f, 0.17f)
    private var loading = false
    private var shown = false
    private var built = false

    /** A piece of scenery that moves past: its node, its place at travel 0, and its fixed y / z. */
    private class Mover(val node: Node, val baseX: Float, val y: Float, val z: Float, val span: Float)
    private val movers = mutableListOf<Mover>()

    private class Effect(val action: String, val startNanos: Long, val durationNanos: Long, val lines: List<CubeNode>)
    private var effect: Effect? = null
    private var drive = 1f // 1 = cruising, 0 = stopped (Pull over); eases between the two
    private var stillUntilNanos = 0L
    private var lastNanos = 0L
    private var travel = 0f // how far the world has moved past the car
    private var orbitDeg = CarMotion.ORBIT_START_DEG
    private val materials = HashMap<String, MaterialInstance>()

    init {
        view.visibility = View.GONE
        view.scene.skybox = Skybox.Builder().color(0.42f, 0.64f, 0.92f, 1f).build(view.engine) // daytime sky
        view.onFrame = ::frame
        placeCamera(0f)
    }

    /** Show or hide the scene (the trip screen is up, or not). */
    fun show(on: Boolean) {
        shown = on
        view.visibility = if (on) View.VISIBLE else View.GONE
        if (on && !built) buildWorld()
        if (on && car == null && !loading) load()
    }

    /** Play a control's effect for `ms` (CAR_ACTION). A new one replaces one still playing. */
    fun play(action: String, ms: Int) {
        clearEffect()
        val now = System.nanoTime()
        val lines = CarMotion.lines(action, 0f, box).map { newLine(action) }
        lines.forEach { view.addChildNode(it) }
        effect = Effect(action, now, ms * 1_000_000L, lines)
        if (action == "pull_over") stillUntilNanos = now + (ms + STILL_AFTER_MS) * 1_000_000L
        Log.i(TAG, "effect $action for $ms ms (${lines.size} lines)")
    }

    fun destroy() {
        clearEffect()
        view.destroy()
    }

    // --- the world --------------------------------------------------------------------------

    private val ground get() = -box.halfHeight // the model is centered: its wheels touch y = -halfHeight

    private fun buildWorld() {
        built = true
        val g = ground
        val engine = view.engine
        // Grass to the horizon, and the road along x (the car drives in the right-hand lane, at z = 0).
        view.addChildNode(CubeNode(engine, Float3(80f, 0.02f, 80f), Float3(0f, g - 0.011f, 0f), color("grass", 96, 164, 78)))
        view.addChildNode(CubeNode(engine, Float3(80f, 0.004f, ROAD_WIDTH), Float3(0f, g + 0.001f, ROAD_Z), color("road", 70, 72, 78)))
        for (edge in listOf(ROAD_Z - ROAD_WIDTH / 2 + 0.03f, ROAD_Z + ROAD_WIDTH / 2 - 0.03f)) {
            view.addChildNode(CubeNode(engine, Float3(80f, 0.005f, 0.018f), Float3(0f, g + 0.002f, edge), color("paint", 240, 240, 235)))
        }
        // Lane dashes and trees move past; each set wraps over its own span.
        val dashSpan = DASHES * DASH_GAP
        repeat(DASHES) { i ->
            val node = CubeNode(engine, Float3(0.3f, 0.005f, 0.022f), Float3(0f, 0f, 0f), color("paint", 240, 240, 235))
            addMover(node, -dashSpan / 2 + i * DASH_GAP, g + 0.002f, ROAD_Z, dashSpan)
        }
        val treeSpan = TREES * TREE_GAP
        repeat(TREES) { i ->
            // Both sides, set back at slightly different distances so the rows do not look planted.
            val side = if (i % 2 == 0) 1f else -1f
            // Set back beyond the camera's circle, so a tree never comes between the camera and the car.
            val setback = TREE_SETBACK + 0.6f * ((i * 7) % 5) / 4f
            val z = if (side > 0) ROAD_Z + ROAD_WIDTH / 2 + setback else ROAD_Z - ROAD_WIDTH / 2 - setback
            val tree = Node(engine)
            val trunkH = 0.22f + 0.06f * ((i * 3) % 4) / 3f
            tree.addChildNode(CylinderNode(engine, 0.025f, trunkH, Float3(0f, trunkH / 2, 0f), 10, color("trunk", 110, 78, 52)))
            val canopy = 0.15f + 0.05f * ((i * 5) % 3) / 2f
            val leaf = if (i % 3 == 0) color("leaf2", 64, 128, 60) else color("leaf", 52, 112, 58)
            tree.addChildNode(SphereNode(engine, canopy, Float3(0f, trunkH + canopy * 0.8f, 0f), 14, 18, leaf))
            addMover(tree, -treeSpan / 2 + i * TREE_GAP + 0.3f * side, g, z, treeSpan)
        }
    }

    private fun addMover(node: Node, baseX: Float, y: Float, z: Float, span: Float) {
        node.position = Float3(baseX, y, z)
        view.addChildNode(node)
        movers += Mover(node, baseX, y, z, span)
    }

    private fun load() {
        loading = true
        val context = view.context.applicationContext
        thread(name = "car-model") {
            val bytes = try {
                context.assets.open(asset).use { it.readBytes() }
            } catch (e: Exception) {
                Log.e(TAG, "no car model: app/src/main/assets/$asset is missing (${e.message})")
                null
            }
            view.post {
                loading = false
                if (bytes == null || car != null) return@post
                val buffer = ByteBuffer.allocateDirect(bytes.size).order(ByteOrder.nativeOrder()).put(bytes).also { it.rewind() }
                val node = ModelNode(
                    modelInstance = view.modelLoader.createModelInstance(buffer),
                    autoAnimate = false,
                    scaleToUnits = 1f,
                    centerOrigin = Float3(0f, 0f, 0f),
                )
                val e = node.extents
                val longest = max(e.x, max(e.y, e.z)).takeIf { it > 0f } ?: 1f
                box = CarMotion.Box(
                    halfLength = 0.5f * max(e.x, e.z) / longest,
                    halfWidth = 0.5f * minOf(e.x, e.z) / longest,
                    halfHeight = 0.5f * e.y / longest,
                )
                // Face down the road (+x); a model built along z is turned a quarter.
                node.rotation = Float3(0f, if (e.z > e.x) 90f else 0f, 0f)
                view.addChildNode(node)
                car = node
                Log.i(TAG, "car model loaded (${bytes.size / 1024} KB, box $box)")
            }
        }
    }

    // --- every frame ------------------------------------------------------------------------

    private fun frame(nanos: Long) {
        if (!shown) return
        val dt = if (lastNanos == 0L) 0f else ((nanos - lastNanos) / 1e9f).coerceIn(0f, 0.1f)
        lastNanos = nanos
        // Cruising, or stopped for Pull over; always eased, never a jolt.
        val target = if (nanos < stillUntilNanos) 0f else 1f
        drive += (target - drive) * (1f - exp(-dt / DRIVE_EASE_S))
        travel += CarMotion.DRIVE_SPEED * drive * dt
        orbitDeg += 360.0 / CarMotion.ORBIT_PERIOD_S * drive * dt
        for (m in movers) m.node.position = Float3(CarMotion.wrap(m.baseX, travel, m.span), m.y, m.z)

        val e = effect
        var push = 0f
        if (e != null) {
            val progress = ((nanos - e.startNanos).toFloat() / e.durationNanos).coerceIn(0f, 1f)
            push = CarMotion.push(e.action, progress)
            for ((node, l) in e.lines.zip(CarMotion.lines(e.action, progress, box))) {
                node.position = Float3(l.x, l.y, l.z)
                node.rotation = Float3(0f, 0f, l.tiltDeg)
                node.scale = Float3(1f, max(l.length / LINE_LENGTH, 0.0001f), 1f)
                node.isVisible = l.length > 1e-4f
            }
            if (progress >= 1f && e.action != "pull_over") clearEffect()
        }
        if (e?.action == "pull_over" && nanos >= stillUntilNanos) clearEffect()
        placeCamera(push)
    }

    /** The camera on its circle, pushed `push` toward the car, always looking just below it (which
     *  places the car in the upper-middle of the screen, above the tiles, with room to spare). */
    private fun placeCamera(push: Float) {
        val (x, y, z) = CarMotion.orbit(orbitDeg, ORBIT_RADIUS * (1f - PUSH_SCALE * push), ORBIT_HEIGHT)
        view.cameraNode.position = Float3(x, y, z)
        view.cameraNode.lookAt(Float3(0f, LOOK_Y, 0f))
    }

    // --- effects and materials --------------------------------------------------------------

    private fun newLine(action: String): CubeNode {
        val (r, g, b) = when (action) {
            "window_up", "window_down" -> Triple(255, 255, 255) // white against the sky and the paint
            "warmer" -> Triple(255, 140, 60) // warm amber
            "cooler" -> Triple(60, 150, 255) // cool blue
            "music" -> Triple(150, 110, 255) // violet
            else -> Triple(200, 200, 200)
        }
        return CubeNode(view.engine, Float3(0.01f, LINE_LENGTH, 0.01f), Float3(0f, 0f, 0f), color("line-$action", r, g, b)).apply {
            isVisible = false
        }
    }

    private fun color(key: String, r: Int, g: Int, b: Int): MaterialInstance = materials.getOrPut(key) {
        view.materialLoader.createColorInstance(Color.rgb(r, g, b), 0f, 0.85f, 0.1f)
    }

    private fun clearEffect() {
        val e = effect ?: return
        effect = null
        e.lines.forEach {
            view.removeChildNode(it)
            it.destroy()
        }
    }

    companion object {
        private const val TAG = "CarScene"
        const val ASSET = "jaguar_i-pace.glb"
        private const val LINE_LENGTH = 0.11f // CubeNode height; CarMotion lengths scale it
        private const val STILL_AFTER_MS = 2_500L // the car stays stopped this long after Pull over
        private const val DRIVE_EASE_S = 0.6f // how gently the car stops and starts again
        private const val ROAD_WIDTH = 1.15f
        private const val ROAD_Z = -0.29f // the car (z = 0) is in the right-hand lane
        private const val DASHES = 24
        private const val DASH_GAP = 0.75f
        private const val TREES = 22
        private const val TREE_GAP = 1.1f
        private const val TREE_SETBACK = 2.6f // from the road's edge; the camera circles at 2.3
        // The camera circles a little above the car, looking gently down at LOOK_Y below it: a band of
        // sky at the top, the horizon near 15% down, and the car at about 17-35% down the screen, above
        // the tiles (which start near 40%) and never cut off.
        private const val ORBIT_RADIUS = 2.3f
        private const val ORBIT_HEIGHT = 0.2f
        private const val LOOK_Y = -0.52f
        private const val PUSH_SCALE = 0.9f // CarMotion's push, as a share of the orbit radius
    }
}
