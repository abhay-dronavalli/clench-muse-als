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
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

/**
 * The trip screen's 3D car (SceneView / Filament), drawn behind the transparent board WebView. The page
 * leaves the upper part of the trip screen empty for it (web/src/board/trip.tsx) and drives it through
 * ClenchNative.carScene(on) and carEffect(action, ms) (CAR_ACTION). Motion itself is CarMotion.
 *
 * Main thread only (Filament's rule). The GLB (app/src/main/assets/jaguar_i-pace.glb, not in git) is
 * read off the main thread the first time the car is shown; without it the area simply stays dark.
 */
class CarScene(activity: ComponentActivity, private val asset: String = ASSET) {
    val view: SceneView = SceneView(
        context = activity,
        isOpaque = true,
        cameraManipulator = null, // the page above takes every touch; the camera is ours
        sharedLifecycle = activity.lifecycle,
    )

    private var car: ModelNode? = null
    private var box = CarMotion.Box(0.5f, 0.2f, 0.15f)
    private var loading = false
    private var shown = false

    private class Effect(val action: String, val startNanos: Long, val durationNanos: Long, val lines: List<CubeNode>)
    private var effect: Effect? = null
    private var sway = 1f // 1 = idle sway, 0 = still (Pull over)
    private var stillUntilNanos = 0L
    private var lastNanos = 0L
    private val materials = HashMap<String, MaterialInstance>()

    init {
        view.visibility = View.GONE
        // The board's own near-black, so the car sits on the page's background with no seam.
        view.scene.skybox = Skybox.Builder().color(0.006f, 0.006f, 0.008f, 1f).build(view.engine)
        view.cameraNode.position = CAMERA
        view.cameraNode.lookAt(TARGET)
        view.onFrame = ::frame
    }

    /** Show or hide the car (the trip screen is up, or not). */
    fun show(on: Boolean) {
        shown = on
        view.visibility = if (on) View.VISIBLE else View.GONE
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
                // The longer of x and z is along the car; CarMotion works in car space (x along it).
                box = CarMotion.Box(
                    halfLength = 0.5f * max(e.x, e.z) / longest,
                    halfWidth = 0.5f * minOf(e.x, e.z) / longest,
                    halfHeight = 0.5f * e.y / longest,
                )
                alongZ = e.z > e.x
                view.addChildNode(node)
                car = node
                Log.i(TAG, "car model loaded (${bytes.size / 1024} KB, box $box)")
            }
        }
    }

    private var alongZ = false // the model's length runs along z, not x

    private fun frame(nanos: Long) {
        if (!shown) return
        val dt = if (lastNanos == 0L) 0f else ((nanos - lastNanos) / 1e9f).coerceIn(0f, 0.1f)
        lastNanos = nanos
        // Sway eases toward still during Pull over and back afterwards (never a jolt).
        val targetSway = if (nanos < stillUntilNanos) 0f else 1f
        sway += (targetSway - sway) * (1f - kotlin.math.exp(-dt / 0.35f))
        val yaw = CarMotion.yaw(nanos / 1e9, sway) + if (alongZ) 90f else 0f
        car?.rotation = Float3(0f, yaw, 0f)

        val e = effect
        var push = 0f
        if (e != null) {
            val progress = ((nanos - e.startNanos).toFloat() / e.durationNanos).coerceIn(0f, 1f)
            push = CarMotion.push(e.action, progress)
            val lines = CarMotion.lines(e.action, progress, box)
            val rad = Math.toRadians(yaw.toDouble() - if (alongZ) 90.0 else 0.0)
            for ((node, l) in e.lines.zip(lines)) {
                // Car space to world: turn with the car so the lines stay on its door / roof.
                val x = (l.x * cos(rad) + l.z * sin(rad)).toFloat()
                val z = (-l.x * sin(rad) + l.z * cos(rad)).toFloat()
                node.position = Float3(x, l.y, z)
                node.rotation = Float3(0f, yaw, l.tiltDeg)
                node.scale = Float3(1f, max(l.length / LINE_LENGTH, 0.0001f), 1f)
                node.isVisible = l.length > 1e-4f
            }
            if (progress >= 1f && e.action != "pull_over") clearEffect()
        }
        if (e?.action == "pull_over" && nanos >= stillUntilNanos) clearEffect()
        view.cameraNode.position = Float3(CAMERA.x, CAMERA.y, CAMERA.z - PUSH_SCALE * push)
        view.cameraNode.lookAt(TARGET)
    }

    private fun newLine(action: String): CubeNode =
        CubeNode(view.engine, Float3(0.008f, LINE_LENGTH, 0.008f), Float3(0f, 0f, 0f), material(action)).apply {
            isVisible = false
        }

    private fun material(action: String): MaterialInstance = materials.getOrPut(action) {
        val color = when (action) {
            "window_up", "window_down" -> Color.rgb(214, 232, 255) // soft white-blue
            "warmer" -> Color.rgb(255, 170, 110) // warm amber
            "cooler" -> Color.rgb(140, 200, 255) // cool blue
            "music" -> Color.rgb(200, 182, 255) // soft violet
            else -> Color.rgb(200, 200, 200)
        }
        view.materialLoader.createColorInstance(color, 0f, 0.6f, 0.2f)
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
        private const val STILL_AFTER_MS = 2_500L // the car stays still this long after Pull over
        // The view fills the screen; looking at a point below the car puts it in the page's car area
        // (about a quarter of the way down), at roughly the size of that area.
        private val CAMERA = Float3(0f, -0.08f, 1.85f)
        private val TARGET = Float3(0f, -0.36f, 0f)
        private const val PUSH_SCALE = 1.7f // CarMotion's push is sized for a camera about 1 unit away
    }
}
