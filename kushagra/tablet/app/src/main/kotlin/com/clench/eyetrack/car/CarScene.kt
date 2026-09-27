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
 * over (speed 0) the road stands still. Pull over also stills the camera for a moment. Parked (speed 0:
 * boarding while the trip is planned, or pulled over) the camera comes closer and circles twice as slowly.
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
    private var preview = false
    private val lookOrbit = LookOrbit()
    private var lookX = 0.5f
    private var lookSeenAt = 0L

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
    private var parked = 0f // 1 = the car is parked (speed 0): the camera closer and its lap slower, eased
    private var previewDrift = 0.0 // the preview's own slow lap, added to where the eyes steer it
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

    /**
     * The onboarding's preview: just the car, on a soft light platform against a plain light backdrop.
     * Out of it (the app itself) the car drives through its world again: road, grass, trees, sky.
     */
    fun setPreview(on: Boolean) {
        preview = on
        lookSeenAt = 0L
        if (on) clearEffect()
        applyLook()
    }

    private val daySky by lazy { Skybox.Builder().color(0.42f, 0.64f, 0.92f, 1f).build(view.engine) }
    private val plainBackdrop by lazy { Skybox.Builder().color(0.80f, 0.86f, 0.88f, 1f).build(view.engine) }
    private val stage = mutableListOf<Node>()

    /** The world for the app, the platform for the preview. */
    private fun applyLook() {
        view.scene.skybox = if (preview) plainBackdrop else daySky
        world?.setVisible(!preview)
        stage.forEach { it.isVisible = preview; it.childNodes.forEach { c -> c.isVisible = preview } }
    }

    /** A soft round platform under the car and a darker oval under its body (preview only). */
    private fun buildStage() {
        val engine = view.engine
        val platform = CylinderNode(engine, 1.1f, 0.01f, Float3(0f, ground - 0.005f, 0f), 64, stageColor("platform", 226, 232, 234))
        val shadow = Node(engine).apply {
            position = Float3(0f, ground + 0.0005f, 0f)
            scale = Float3(1f, 1f, 0.5f)
            addChildNode(CylinderNode(engine, 0.6f, 0.002f, Float3(0f, 0f, 0f), 48, stageColor("shadow", 188, 196, 200)))
        }
        listOf(platform, shadow).forEach {
            view.addChildNode(it)
            stage += it
        }
    }

    private fun stageColor(key: String, r: Int, g: Int, b: Int): MaterialInstance = materials.getOrPut(key) {
        view.materialLoader.createColorInstance(Color.rgb(r, g, b), 0f, 0.95f, 0.05f)
    }

    fun look(x: Float, found: Boolean) {
        if (found && x.isFinite()) {
            lookX = x
            lookSeenAt = System.nanoTime()
        } else lookSeenAt = 0L
    }

    /** The car's speed (CAR_STATE): the road and trees move at it; 0 = stopped. */
    fun setSpeed(mph: Int) {
        speedTarget = (mph / CRUISE_MPH).coerceIn(0f, 2f)
        if (!shown) { // not on screen yet: start there, rather than drive off and ease to a stop
            speed = speedTarget
            parked = if (speedTarget <= 0f) 1f else 0f
        }
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
        buildStage()
        applyLook()
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
        if (preview) {
            previewDrift = (previewDrift + 360.0 / PARKED_ORBIT_PERIOD_S * dt) % 360.0
            val angle = (lookOrbit.update(lookX, nanos - lookSeenAt < 500_000_000L, dt) + previewDrift) % 360.0
            val (x, y, z) = CarMotion.orbit(angle, PREVIEW_RADIUS, PREVIEW_HEIGHT)
            view.cameraNode.setShift(0.0, 0.0)
            view.cameraNode.position = Float3(x, y, z)
            view.cameraNode.lookAt(Float3(0f, CAR_LIFT, 0f))
            return // parked, upright model; only the camera turns (slowly, and where the eyes steer it)
        }
        // Cruising, or stopped for Pull over; always eased, never a jolt.
        val target = if (nanos < stillUntilNanos) 0f else 1f
        drive += (target - drive) * (1f - exp(-dt / DRIVE_EASE_S))
        speed += (speedTarget - speed) * (1f - exp(-dt / SPEED_EASE_S))
        travel += CarMotion.DRIVE_SPEED * speed * drive * dt
        parked += ((if (speedTarget <= 0f) 1f else 0f) - parked) * (1f - exp(-dt / PARK_EASE_S))
        val period = CarMotion.ORBIT_PERIOD_S + (PARKED_ORBIT_PERIOD_S - CarMotion.ORBIT_PERIOD_S) * parked
        orbitDeg += 360.0 / period * drive * dt
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
     *  places the car in the upper-middle of the screen, above the tiles, with room to spare). Parked,
     *  the circle is smaller; the look point moves up with it, so the car keeps its place, only bigger. */
    private fun placeCamera(push: Float) {
        val radius = ORBIT_RADIUS + (PARKED_RADIUS - ORBIT_RADIUS) * parked
        val (x, y, z) = CarMotion.orbit(orbitDeg, radius * (1f - PUSH_SCALE * push), ORBIT_HEIGHT)
        view.cameraNode.position = Float3(x, y, z)
        view.cameraNode.lookAt(Float3(0f, ORBIT_HEIGHT - (ORBIT_HEIGHT - LOOK_Y) * radius / ORBIT_RADIUS, 0f))
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
        private const val PARKED_RADIUS = 1.8f // parked (boarding, pulled over): closer to the car
        private const val PARKED_ORBIT_PERIOD_S = 90.0 // parked: a lap twice as slow as while driving
        private const val PARK_EASE_S = 1.5f // how gently the camera moves in when the car stops
        private const val PREVIEW_RADIUS = 1.55f // the onboarding preview: closer than before (1.9)
        private const val PREVIEW_HEIGHT = 0.4f
        private const val ORBIT_HEIGHT = 0.2f
        private const val LOOK_Y = -0.52f
        private const val PUSH_SCALE = 0.9f // CarMotion's push, as a share of the orbit radius
    }
}
