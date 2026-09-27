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
import io.github.sceneview.node.ModelNode
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread
import kotlin.math.exp
import kotlin.math.max

/**
 * The trip screen's 3D car (SceneView / Filament), drawn full screen behind the transparent board
 * WebView. A daytime drive through a random world (CarWorld / WorldPlan). The car stays put facing down
 * the road while the world moves past it and wraps around (it looks like driving), and the
 * camera makes one slow circle around the car every 45 s. The page (web/src/board/trip.tsx) drives it
 * through ClenchNative.carScene(on) and carEffect(action, ms) (CAR_ACTION); the maths is CarMotion.
 *
 * The scenery moves at the telemetry's speed (carSpeed, CAR_STATE): Slow down slows it, and after Pull
 * over (speed 0) the road stands still. Pull over also stills the camera for a moment.
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

    private var world: CarWorld? = null
    private val seed = System.nanoTime() // a different world every time the app starts
    private var seconds = 0f // scene time, for the clouds' own drift
    private var shift = 0f // horizontal lens shift: the car in the right half in the split layout
    private var shiftTarget = CAR_SHIFT

    private class Effect(
        val action: String,
        val window: String,
        val startNanos: Long,
        val durationNanos: Long,
        val lines: List<CubeNode>,
    )
    private var effect: Effect? = null
    private var drive = 1f // 1 = moving, 0 = held still (Pull over's moment); eases between the two
    private var speed = 1f // the scenery's speed as a share of cruising (CRUISE_MPH), eased
    private var speedTarget = 1f
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
        if (on && car == null && !loading) load() // the world follows once the car's size is known
    }

    /**
     * The page's trip layout. The page has a telemetry column on its left, so the car is always centred
     * in what is to the right of it: "car" and "map" (the map covers the car's area then) a little right
     * of the screen's middle, "split" in the right half (the route map has the left half).
     */
    fun setLayout(mode: String) {
        shiftTarget = if (mode == "split") SPLIT_SHIFT else CAR_SHIFT
    }

    /** The car's speed (CAR_STATE): the road and trees move at it; 0 = stopped. */
    fun setSpeed(mph: Int) {
        speedTarget = (mph / CRUISE_MPH).coerceIn(0f, 2f)
    }

    /** Play a control's effect for `ms` (CAR_ACTION); `window` for the window controls. */
    fun play(action: String, ms: Int, window: String = "all") {
        clearEffect()
        val now = System.nanoTime()
        val which = window.ifEmpty { "all" }
        val lines = CarMotion.lines(action, 0f, box, which).map { newLine(action) }
        lines.forEach { view.addChildNode(it) }
        effect = Effect(action, which, now, ms * 1_000_000L, lines)
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
        world = CarWorld(view, ground, seed)
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
                if (bytes == null) {
                    if (!built) buildWorld() // the road alone
                    return@post
                }
                if (car != null) return@post
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
                // Face down the road (+x); a model built along z is turned a quarter. Lifted a hair so the
                // tyres sit on the road's surface instead of sinking into it.
                node.rotation = Float3(0f, if (e.z > e.x) 90f else 0f, 0f)
                node.position = Float3(0f, CAR_LIFT, 0f)
                view.addChildNode(node)
                car = node
                if (!built) buildWorld() // the ground at the car's real wheel height
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
        speed += (speedTarget - speed) * (1f - exp(-dt / SPEED_EASE_S))
        travel += CarMotion.DRIVE_SPEED * speed * drive * dt
        orbitDeg += 360.0 / CarMotion.ORBIT_PERIOD_S * drive * dt
        seconds += dt
        world?.update(travel, seconds)
        shift += (shiftTarget - shift) * (1f - exp(-dt / LAYOUT_EASE_S))
        view.cameraNode.setShift(shift.toDouble(), 0.0)

        val e = effect
        var push = 0f
        if (e != null) {
            val progress = ((nanos - e.startNanos).toFloat() / e.durationNanos).coerceIn(0f, 1f)
            push = CarMotion.push(e.action, progress)
            for ((node, l) in e.lines.zip(CarMotion.lines(e.action, progress, box, e.window))) {
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
            "louder", "softer" -> Triple(150, 110, 255) // violet
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
        private const val DRIVE_EASE_S = 0.6f // how gently the camera and scenery hold still and resume
        private const val SPEED_EASE_S = 1.2f // how gently the scenery follows a new speed
        private const val CRUISE_MPH = 32f // the scenery's DRIVE_SPEED is this speed
        private const val CAR_LIFT = 0.006f // tyres on the road's surface (its top is ~0.0045 above ground)
        // Lens shifts (measured on the tablet: 0.3 moves the car a quarter of the screen's width).
        private const val CAR_SHIFT = 0.06f // past the page's 288 px telemetry column
        private const val SPLIT_SHIFT = 0.33f // the middle of the right half beside the column and the map
        private const val LAYOUT_EASE_S = 0.4f
        // The camera circles a little above the car, looking gently down at LOOK_Y below it: a band of
        // sky at the top, the horizon near 15% down, and the car at about 17-35% down the screen, above
        // the tiles (which start near 40%) and never cut off.
        private const val ORBIT_RADIUS = 2.3f
        private const val ORBIT_HEIGHT = 0.2f
        private const val LOOK_Y = -0.52f
        private const val PUSH_SCALE = 0.9f // CarMotion's push, as a share of the orbit radius
    }
}
