package com.clench.eyetrack.board

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import camp.visual.eyedid.gazetracker.metrics.state.TrackingState
import com.clench.eyetrack.BuildConfig
import com.clench.eyetrack.board.GazeMath.Frac

/**
 * The patient board in a WebView (BOARD_URL, by default http://localhost:5173/ through
 * `adb reverse tcp:5173 tcp:5173`), with the Eyedid gaze tracker feeding the page's gaze slot.
 * docs/eye-tracking.md, "Native shell".
 *
 *   - The tracker owns the front camera. While it runs (or starts) the page is told so
 *     (ClenchNative.gazeActive()) and any camera request from the page is denied. If the tracker
 *     cannot start (no key, no network, auth error), the page may use the camera for head pointing.
 *   - Gaze goes to the page as fractions of the WebView, about 30 times a second.
 *   - Calibration: five points on a native screen, saved per person and reloaded at start, then
 *     checked with one target; a miss asks to recalibrate.
 */
class BoardActivity : ComponentActivity(), EyedidGaze.Listener {

    private lateinit var web: WebView
    private lateinit var gazeOverlay: GazeOverlay
    private lateinit var gaze: EyedidGaze
    private val prefs by lazy { getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    private val main = Handler(Looper.getMainLooper())

    /** whose calibration is in use ("" = none) */
    @Volatile private var person: String = ""
    private var calibratingFor: String? = null
    private var calSession = 0 // bumps per calibration, so a delayed step from an old one does nothing
    private var destroyed = false
    private var calPoint: Pair<Float, Float>? = null
    private var startupChecked = false
    private var validation: Validation? = null

    private class Validation(val person: String, val target: Frac, val cols: Int, val rows: Int) {
        val samples = mutableListOf<Frac>()
        var collecting = false
    }

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startTracker() else onTrackerState("error", "camera permission denied")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestedOrientation = if (BuildConfig.BOARD_ORIENTATION == "portrait") {
            ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        } else {
            ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        }
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).let {
            it.hide(WindowInsetsCompat.Type.systemBars())
            it.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        person = prefs.getString(KEY_PERSON, "") ?: ""
        gaze = EyedidGaze(applicationContext, this)

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG) // chrome://inspect on the laptop
        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true // localStorage: gaze settings, /gaze-test results
            settings.mediaPlaybackRequiresUserGesture = false
            addJavascriptInterface(Bridge(), "ClenchNative")
            webViewClient = object : WebViewClient() {
                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (!request.isForMainFrame) return
                    gazeOverlay.prompt(
                        "The board is not reachable at ${BuildConfig.BOARD_URL}\n(${error.description}).\n" +
                            "Is the web app running, and did you run adb reverse tcp:5173 tcp:5173?",
                        "Retry" to { gazeOverlay.hide(); web.reload() },
                    )
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    // One camera owner: never give the page the camera while the tracker has it.
                    val video = PermissionRequest.RESOURCE_VIDEO_CAPTURE in request.resources
                    if (video && (gaze.active || !hasCamera())) request.deny() else request.grant(request.resources)
                }

                override fun onConsoleMessage(m: ConsoleMessage): Boolean {
                    Log.d("BoardPage", "${m.messageLevel()}: ${m.message()} (${m.sourceId()}:${m.lineNumber()})")
                    return true
                }
            }
        }
        gazeOverlay = GazeOverlay(this)
        setContentView(
            FrameLayout(this).apply {
                addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
                addView(gazeOverlay, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            },
        )
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = cancelTrackerScreens()
        })

        web.loadUrl(BuildConfig.BOARD_URL)
        if (hasCamera()) startTracker() else cameraPermission.launch(Manifest.permission.CAMERA)
    }

    private fun hasCamera() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    private fun startTracker() = gaze.start(prefs.getBoolean(KEY_FILTER, true))

    override fun onResume() {
        super.onResume()
        web.onResume()
        gaze.resume()
    }

    override fun onPause() {
        gaze.pause()
        web.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        destroyed = true
        gaze.release()
        web.destroy()
        super.onDestroy()
    }

    // --- the page ---------------------------------------------------------------------------

    private fun js(code: String) {
        if (!destroyed) web.evaluateJavascript(code, null)
    }

    private fun event(type: String, vararg fields: Pair<String, String>) = js(GazeMath.eventJs(type, mapOf(*fields)))

    private fun calibrationEvent(state: String, who: String) =
        event("calibration", "state" to GazeMath.str(state), "person" to GazeMath.str(who))

    private fun webRect(): GazeMath.ViewRect {
        val loc = IntArray(2)
        web.getLocationOnScreen(loc)
        return GazeMath.ViewRect(loc[0].toFloat(), loc[1].toFloat(), web.width.toFloat(), web.height.toFloat())
    }

    /** window.ClenchNative in the page. Called on a WebView thread, not the main thread. */
    private inner class Bridge {
        @JavascriptInterface fun gazeActive(): Boolean = gaze.active

        @JavascriptInterface fun person(): String = person

        @JavascriptInterface fun gazeFilter(): Boolean = gaze.gazeFilter

        @JavascriptInterface fun setGazeFilter(on: Boolean) {
            main.post {
                prefs.edit().putBoolean(KEY_FILTER, on).apply()
                if (on != gaze.gazeFilter) gaze.start(on) // an init option: the tracker restarts
            }
        }

        @JavascriptInterface fun calibrate(who: String) {
            main.post { calibrate(who.trim().ifEmpty { DEFAULT_PERSON }) }
        }
    }

    // --- tracker ----------------------------------------------------------------------------

    override fun onTrackerState(state: String, detail: String) {
        Log.i(TAG, "tracker $state $detail")
        event("tracker", "state" to GazeMath.str(state), "detail" to GazeMath.str(detail))
        if (state == "error") {
            // Never leave the board covered by a calibration or check the tracker can no longer finish.
            val who = calibratingFor ?: validation?.person
            val busy = who != null || calPoint != null
            calibratingFor = null
            calPoint = null
            validation = null
            if (who != null) calibrationEvent("canceled", who)
            if (busy || gazeOverlay.visibility == android.view.View.VISIBLE) {
                gazeOverlay.prompt("The eye tracker stopped: $detail", "OK" to { gazeOverlay.hide() })
            }
            return
        }
        if (state != "on") return
        if (!startupChecked) {
            startupChecked = true
            checkSavedCalibration()
        } else {
            savedCalibration(person)?.let { gaze.setCalibrationData(it) } // after a restart (filter switch)
        }
    }

    override fun onGaze(x: Float, y: Float, state: TrackingState) {
        val found = state == TrackingState.SUCCESS
        val v = validation
        if (v != null || calibratingFor != null) {
            if (v != null && v.collecting && found) v.samples += GazeMath.toFraction(x, y, gazeOverlay.screenRect())
            js(GazeMath.feedJs(Frac(0.5f, 0.5f), false, "CALIBRATING")) // the board holds still meanwhile
            return
        }
        js(GazeMath.feedJs(GazeMath.toFraction(x, y, webRect()), found, state.name))
    }

    override fun onBlink(timestamp: Long, left: Boolean, right: Boolean) {
        event("blink", "t" to timestamp.toString(), "left" to left.toString(), "right" to right.toString())
    }

    // --- calibration ------------------------------------------------------------------------

    private fun savedCalibration(who: String): DoubleArray? =
        if (who.isEmpty()) null else GazeMath.decode(prefs.getString(KEY_CAL + who, null))

    private fun calibrate(who: String) {
        if (gaze.state != EyedidGaze.State.ON) {
            gazeOverlay.prompt("The eye tracker is not running (${gaze.state}).", "OK" to { gazeOverlay.hide() })
            return
        }
        validation = null
        calibratingFor = who
        calSession++
        gazeOverlay.showDot(-1f, -1f, 0f, "Calibrating $who: look at each dot until it fills")
        val r = gazeOverlay.screenRect()
        if (!gaze.startCalibration(r.left, r.top, r.left + r.width, r.top + r.height)) {
            calibratingFor = null
            gazeOverlay.prompt("Calibration could not start.", "OK" to { gazeOverlay.hide() })
            return
        }
        calibrationEvent("started", who)
    }

    override fun onCalibrationPoint(x: Float, y: Float) {
        calPoint = x to y
        gazeOverlay.showDot(x, y, 0f)
        val session = calSession
        main.postDelayed({ if (calibratingFor != null && session == calSession && calPoint == (x to y)) gaze.collectSamples() }, SETTLE_BEFORE_SAMPLES_MS)
    }

    override fun onCalibrationProgress(progress: Float) {
        calPoint?.let { (x, y) -> gazeOverlay.showDot(x, y, progress) }
    }

    override fun onCalibrationFinished(data: DoubleArray) {
        val who = calibratingFor ?: return
        calibratingFor = null
        calPoint = null
        prefs.edit().putString(KEY_CAL + who, GazeMath.encode(data)).putString(KEY_PERSON, who).apply()
        person = who
        gaze.setCalibrationData(data)
        gazeOverlay.hide()
        calibrationEvent("finished", who)
    }

    override fun onCalibrationCanceled() {
        val who = calibratingFor ?: return
        calibratingFor = null
        calPoint = null
        gazeOverlay.hide()
        calibrationEvent("canceled", who)
    }

    private fun cancelTrackerScreens() {
        if (calibratingFor != null) {
            gaze.stopCalibration() // onCalibrationCanceled follows
            return
        }
        validation = null
        gazeOverlay.hide()
    }

    /** At start: reload the last person's calibration and check it with one target. */
    private fun checkSavedCalibration() {
        val who = person
        val data = savedCalibration(who)
        if (data == null) {
            gazeOverlay.prompt(
                "The eye tracker is not calibrated yet.",
                "Calibrate now" to { calibrate(who.ifEmpty { DEFAULT_PERSON }) },
                "Later" to { gazeOverlay.hide() },
            )
            return
        }
        gaze.setCalibrationData(data)
        calibrationEvent("loaded", who)
        validate(who)
    }

    private fun validate(who: String) {
        val portrait = gazeOverlay.height > gazeOverlay.width
        val (cols, rows) = if (portrait) 2 to 3 else 3 to 2
        val tile = (0 until cols * rows).random()
        val target = Frac((tile % cols + 0.5f) / cols, (tile / cols + 0.5f) / rows)
        val v = Validation(who, target, cols, rows)
        validation = v
        val r = gazeOverlay.screenRect()
        gazeOverlay.showDot(r.left + target.x * r.width, r.top + target.y * r.height, 0f, "Checking $who's calibration: look at the dot")
        main.postDelayed({ if (validation === v) v.collecting = true }, VALIDATE_SETTLE_MS)
        main.postDelayed({ if (validation === v) finishValidation(v) }, VALIDATE_SETTLE_MS + VALIDATE_COLLECT_MS)
    }

    private fun finishValidation(v: Validation) {
        validation = null
        val ok = GazeMath.validationPasses(v.samples, v.target, 0.5f / v.cols, 0.5f / v.rows)
        Log.i(TAG, "calibration check for ${v.person}: ${if (ok) "passed" else "failed"} (${v.samples.size} samples)")
        calibrationEvent(if (ok) "validation_passed" else "validation_failed", v.person)
        if (ok) {
            gazeOverlay.hide()
        } else {
            gazeOverlay.prompt(
                "${v.person}'s calibration looks off from here.",
                "Recalibrate" to { calibrate(v.person) },
                "Try the check again" to { validate(v.person) },
                "Keep it" to { gazeOverlay.hide() },
            )
        }
    }

    companion object {
        private const val TAG = "BoardActivity"
        private const val PREFS = "clench.gaze"
        private const val KEY_PERSON = "person"
        private const val KEY_FILTER = "gazeFilter"
        private const val KEY_CAL = "cal."
        private const val DEFAULT_PERSON = "patient"
        private const val SETTLE_BEFORE_SAMPLES_MS = 1_000L // the SDK sample waits 1 s on each dot
        private const val VALIDATE_SETTLE_MS = 800L
        private const val VALIDATE_COLLECT_MS = 1_500L
    }
}
