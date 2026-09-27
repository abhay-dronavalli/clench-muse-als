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
import com.clench.eyetrack.muse.ClenchProfile
import com.clench.eyetrack.muse.MuseSensor
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The patient board in a WebView (BOARD_URL, by default http://localhost:5173/ through
 * `adb reverse tcp:5173 tcp:5173`), with the Eyedid gaze tracker feeding the page's gaze slot.
 * docs/eye-tracking.md, "Native shell".
 *
 *   - The tracker runs only while the page's pointing mode needs the camera (Auto, Webcam, Gaze;
 *     the page reports it with ClenchNative.setPointingMode). It is off before "Click to start",
 *     after every page load, and in Scan and Head tilt.
 *   - The tracker owns the front camera. While it runs (or starts) the page is told so
 *     (ClenchNative.gazeActive()) and any camera request from the page is denied. If the tracker
 *     cannot start (no key, no network, auth error), the page may use the camera for head pointing.
 *   - Gaze goes to the page as fractions of the WebView, about 30 times a second.
 *   - Calibration: five points on a native screen, saved per person and reloaded at start, then
 *     checked with one target; a miss asks to recalibrate.
 *   - With MUSE_PROFILE set, the Muse panel's Connect headband pairs the Muse 2 with the tablet over
 *     Bluetooth and its clenches go to the Core's /ws/sensor (com.clench.eyetrack.muse.MuseSensor),
 *     in place of the laptop's sensor. Nothing connects until then: the board works without it.
 *   - The page's "browser speech" is Android's text-to-speech (NativeSpeech): WebView has none.
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
    /** The page's pointing mode needs the camera (set from the page's thread, acted on in reconcile). */
    @Volatile private var cameraWanted = false
    private var calPoint: Pair<Float, Float>? = null
    private var startupChecked = false
    private var validation: Validation? = null

    private class Validation(val person: String, val target: Frac, val cols: Int, val rows: Int) {
        val samples = mutableListOf<Frac>()
        var collecting = false
    }

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) gaze.fail("camera permission denied") else reconcile()
    }

    private lateinit var speech: NativeSpeech

    // The tablet's Muse sensor: main thread only, except the log (locked) and museWanted.
    private var muse: MuseSensor? = null
    @Volatile private var museWanted = false
    private val museLog = ArrayDeque<String>()

    private val bluetoothPermission = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted.values.all { it }) {
            startMuse()
        } else {
            museWanted = false
            museLine("Bluetooth permission denied: allow Nearby devices for Clench Board, then Connect again")
        }
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
        speech = NativeSpeech(this) { id, state, detail ->
            main.post { event("speech", "id" to GazeMath.str(id), "state" to GazeMath.str(state), "detail" to GazeMath.str(detail)) }
        }

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG) // chrome://inspect on the laptop
        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true // localStorage: gaze settings, /gaze-test results
            settings.mediaPlaybackRequiresUserGesture = false
            addJavascriptInterface(Bridge(), "ClenchNative")
            webViewClient = object : WebViewClient() {
                override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
                    // A new page has not chosen a pointing mode yet: the tracker is off until it does.
                    cameraWanted = false
                    reconcile()
                }

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
        // Ask for the camera now, so the prompt does not interrupt the person later. The tracker itself
        // starts only when the page's pointing mode needs it.
        if (!hasCamera()) cameraPermission.launch(Manifest.permission.CAMERA)
    }

    private val bluetoothPermissions =
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }

    /** Connect headband on the tablet: the Muse sensor with this build's profile (MUSE_PROFILE). Main thread. */
    private fun startMuse() {
        if (muse != null || destroyed || !museWanted) return
        val missing = bluetoothPermissions.filter { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) {
            museLine("Asking for Bluetooth permission")
            return bluetoothPermission.launch(missing.toTypedArray())
        }
        val profile = try {
            ClenchProfile(BuildConfig.MUSE_PROFILE, BuildConfig.MUSE_EMG_REST, BuildConfig.MUSE_EMG_THRESHOLD,
                BuildConfig.MUSE_EMG_PEAK.takeIf { it.isFinite() })
        } catch (e: IllegalArgumentException) {
            museWanted = false
            museLine("Profile ${BuildConfig.MUSE_PROFILE} is unusable: ${e.message}")
            return
        }
        val origin = android.net.Uri.parse(BuildConfig.BOARD_URL).let { "${it.scheme}://${it.encodedAuthority}" }
        museLine("Starting: profile ${profile.name}, threshold ${String.format(Locale.US, "%.1f", profile.emgThreshold)} uV, clenches only")
        muse = MuseSensor(applicationContext, profile, origin, BuildConfig.MUSE_NAME.ifEmpty { null }, BuildConfig.MUSE_MOTION_LIMIT, ::museLine)
            .also { it.start() }
    }

    /** Disconnect: release the headband; the Core pauses Muse input when the sensor goes. Main thread. */
    private fun stopMuse() {
        // museWanted is the bridge's to set: a Connect right after this Disconnect must still start.
        muse?.let {
            it.stop()
            museLine("Stopped: the headband is released")
        }
        muse = null
        if (museWanted) startMuse() // Disconnect then Connect again, both queued before this ran
    }

    private fun museLine(text: String) {
        val stamp = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
        synchronized(museLog) {
            museLog.addLast("$stamp $text")
            while (museLog.size > MUSE_LOG_LINES) museLog.removeFirst()
        }
    }

    /** The Core's /api/sensor status shape, for the Muse panel (web/src/sensor/service.ts). */
    private fun museStatusJson(): String = JSONObject()
        .put("running", museWanted)
        .put("pid", JSONObject.NULL)
        .put("profile", BuildConfig.MUSE_PROFILE)
        .put("source", "tablet")
        .put("blink", "off")
        .put("exit_code", JSONObject.NULL)
        .put("profiles", JSONArray(listOf(BuildConfig.MUSE_PROFILE)))
        .put("log", JSONArray(synchronized(museLog) { museLog.toList() }))
        .toString()

    private fun hasCamera() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    /** Start or stop the tracker to match what the page wants. Main thread. */
    private fun reconcile() {
        if (destroyed) return
        if (cameraWanted) {
            if (gaze.running) return
            if (hasCamera()) gaze.start(prefs.getBoolean(KEY_FILTER, true)) else gaze.fail("camera permission denied")
        } else if (gaze.running || gaze.state != EyedidGaze.State.OFF) {
            dropTrackerScreens()
            gaze.release()
        }
    }

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
        museWanted = false
        stopMuse()
        speech.shutdown()
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

        /**
         * The page's pointing mode ("off" before "Click to start", and on pages without a board).
         * Camera modes claim the camera synchronously, so gazeActive() is already true when this
         * returns and the page will not open the camera itself.
         */
        @JavascriptInterface fun setPointingMode(mode: String) {
            val want = mode in CAMERA_MODES
            cameraWanted = want
            if (want) gaze.claim()
            main.post { reconcile() }
        }

        @JavascriptInterface fun person(): String = person

        @JavascriptInterface fun gazeFilter(): Boolean = prefs.getBoolean(KEY_FILTER, true)

        @JavascriptInterface fun setGazeFilter(on: Boolean) {
            main.post {
                prefs.edit().putBoolean(KEY_FILTER, on).apply()
                // An init option: a running tracker restarts with it; otherwise it applies at the next start.
                if (on != gaze.gazeFilter && gaze.running) gaze.start(on)
            }
        }

        @JavascriptInterface fun calibrate(who: String) {
            main.post { calibrate(who.trim().ifEmpty { DEFAULT_PERSON }) }
        }

        // Android's voice for the page's "browser speech" (web/src/board/nativeSpeech.ts).
        @JavascriptInterface fun speak(id: String, text: String, lang: String, volume: Double): Boolean =
            speech.speak(id, text, lang, volume.toFloat())

        @JavascriptInterface fun stopSpeaking() = speech.stop()

        // The Muse panel's Connect / Disconnect on the tablet (web/src/sensor/service.ts).
        @JavascriptInterface fun museAvailable(): Boolean = BuildConfig.MUSE_PROFILE.isNotEmpty()

        @JavascriptInterface fun museStatus(): String = museStatusJson()

        @JavascriptInterface fun museConnect(): String {
            if (BuildConfig.MUSE_PROFILE.isNotEmpty() && !museWanted) {
                museWanted = true
                main.post { startMuse() }
            }
            return museStatusJson()
        }

        @JavascriptInterface fun museDisconnect(): String {
            museWanted = false
            main.post { stopMuse() }
            return museStatusJson()
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

    /** The tracker is going away: close its screens now (its callbacks will not come any more). */
    private fun dropTrackerScreens() {
        calibratingFor?.let { calibrationEvent("canceled", it) }
        calibratingFor = null
        calPoint = null
        validation = null
        gazeOverlay.hide()
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
        private val CAMERA_MODES = setOf("auto", "webcam", "gaze")
        private const val SETTLE_BEFORE_SAMPLES_MS = 1_000L // the SDK sample waits 1 s on each dot
        private const val VALIDATE_SETTLE_MS = 800L
        private const val VALIDATE_COLLECT_MS = 1_500L
        private const val MUSE_LOG_LINES = 60
    }
}
