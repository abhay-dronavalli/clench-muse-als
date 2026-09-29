package com.clench.eyetrack.board

import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import camp.visual.eyedid.gazetracker.GazeTracker
import camp.visual.eyedid.gazetracker.callback.CalibrationCallback
import camp.visual.eyedid.gazetracker.callback.StatusCallback
import camp.visual.eyedid.gazetracker.callback.TrackingCallback
import camp.visual.eyedid.gazetracker.constant.AccuracyCriteria
import camp.visual.eyedid.gazetracker.constant.CalibrationModeType
import camp.visual.eyedid.gazetracker.constant.GazeTrackerOptions
import camp.visual.eyedid.gazetracker.constant.InitializationErrorType
import camp.visual.eyedid.gazetracker.constant.StatusErrorType
import camp.visual.eyedid.gazetracker.device.CameraPosition
import camp.visual.eyedid.gazetracker.metrics.BlinkInfo
import camp.visual.eyedid.gazetracker.metrics.FaceInfo
import camp.visual.eyedid.gazetracker.metrics.GazeInfo
import camp.visual.eyedid.gazetracker.metrics.UserStatusInfo
import camp.visual.eyedid.gazetracker.metrics.state.TrackingState
import com.clench.eyetrack.BuildConfig

/**
 * The Eyedid gaze tracker for the board shell. It owns the front camera while it runs.
 *
 *   init   authenticate the license key (needs the network), set the camera position for this
 *          tablet if the SDK does not know it, start tracking
 *   gaze   every tracking frame, throttled to about 30 a second, as screen pixels -> [Listener.onGaze]
 *   blink  the moment a blink starts -> [Listener.onBlink] (recorded on /gaze-test, never used to pick)
 *   calibration  five points; the result is handed back for saving per person
 *
 * Callbacks from the SDK arrive on its own threads; everything reaches the Listener on the main thread.
 */
class EyedidGaze(private val context: Context, private val listener: Listener) {

    interface Listener {
        fun onTrackerState(state: String, detail: String = "")
        fun onGaze(x: Float, y: Float, state: TrackingState)
        fun onBlink(timestamp: Long, left: Boolean, right: Boolean)
        fun onCalibrationPoint(x: Float, y: Float)
        fun onCalibrationProgress(progress: Float)
        fun onCalibrationFinished(data: DoubleArray)
        fun onCalibrationCanceled()
    }

    enum class State { OFF, STARTING, ON, ERROR }

    /** Read from the page's JavascriptInterface thread. */
    @Volatile var state: State = State.OFF
        private set

    /** STARTING or ON: the page must not open the camera. */
    val active: Boolean get() = state == State.STARTING || state == State.ON

    /** A start was asked for and not released since (main thread). */
    val running: Boolean get() = wantTracking

    @Volatile var gazeFilter: Boolean = true
        private set

    private val main = Handler(Looper.getMainLooper())
    private val retryToken = Any()
    /** Bumps on every start and release: callbacks from an older tracker are dropped. */
    @Volatile private var generation = 0
    private var tracker: GazeTracker? = null
    private var wantTracking = false
    private var retries = 0
    private var blinking = false
    private val throttle = GazeMath.Throttle(FEED_INTERVAL_MS)

    /**
     * The page is about to need the camera (any thread): report STARTING at once, so that from this
     * call on ClenchNative.gazeActive() is true and the page does not open the camera itself. The
     * actual start follows on the main thread.
     */
    fun claim() {
        if (state == State.OFF || state == State.ERROR) setState(State.STARTING, "requested")
    }

    /** Start (or restart with new options). Needs the CAMERA permission. Main thread. */
    fun start(useGazeFilter: Boolean) {
        gazeFilter = useGazeFilter
        releaseTracker()
        wantTracking = true
        if (BuildConfig.EYEDID_LICENSE_KEY.isBlank()) {
            fail("no license key: set EYEDID_LICENSE_KEY in kushagra/tablet/local.properties")
            return
        }
        val gen = generation
        setState(State.STARTING, "authenticating")
        val options = GazeTrackerOptions.Builder()
            .setUseBlink(true)
            .setUseUserStatus(false)
            .setUseGazeFilter(useGazeFilter)
            .build()
        GazeTracker.initGazeTracker(context, BuildConfig.EYEDID_LICENSE_KEY, { t, error ->
            main.post { onInitialized(t, error, gen) }
        }, options)
    }

    private fun onInitialized(t: GazeTracker?, error: InitializationErrorType, gen: Int) {
        if (!wantTracking || gen != generation) {
            t?.let { GazeTracker.releaseGazeTracker(it) }
            return
        }
        if (t == null || error != InitializationErrorType.ERROR_NONE) {
            // A network problem at start-up: try again (the demo may run on a phone hotspot).
            val retry = error == InitializationErrorType.AUTH_SERVER_ERROR ||
                error == InitializationErrorType.AUTH_CANNOT_FIND_HOST
            if (retry && retries < MAX_RETRIES) {
                retries++
                setState(State.STARTING, "${error.name}: retry $retries of $MAX_RETRIES in ${RETRY_MS / 1000} s")
                main.postDelayed({ if (wantTracking && gen == generation) start(gazeFilter) }, retryToken, RETRY_MS)
            } else {
                fail(error.name)
            }
            return
        }
        retries = 0
        tracker = t
        setCameraPosition(t)
        t.setTrackingFPS(TRACKING_FPS)
        t.setStatusCallback(statusCallback)
        t.setTrackingCallback(TrackingFor(gen))
        t.setCalibrationCallback(calibrationCallback)
        t.startTracking()
    }

    /** The SDK knows many phones and tablets; for this one (SM-X910) we may have to tell it. */
    private fun setCameraPosition(t: GazeTracker) {
        if (t.hasCameraPositions()) {
            val cp = t.cameraPosition
            Log.i(TAG, "camera position from the SDK: ${cp?.modelName} ${cp?.screenOriginX} ${cp?.screenOriginY}")
            return
        }
        val cp = CameraPosition(
            Build.MODEL,
            BuildConfig.SCREEN_WIDTH_PX,
            BuildConfig.SCREEN_HEIGHT_PX,
            BuildConfig.CAMERA_ORIGIN_X_MM,
            BuildConfig.CAMERA_ORIGIN_Y_MM,
            BuildConfig.CAMERA_ON_LONGER_AXIS,
        )
        t.addCameraPosition(cp)
        Log.i(TAG, "camera position added for ${Build.MODEL}: ${cp.screenOriginX} mm, ${cp.screenOriginY} mm")
    }

    fun pause() {
        tracker?.let { if (it.isTracking) it.stopTracking() }
    }

    fun resume() {
        val t = tracker ?: return
        if (!t.isTracking) t.startTracking()
    }

    /** Stop tracking and give the camera back. Main thread. */
    fun release() {
        releaseTracker()
        retries = 0 // not in releaseTracker: a retry restarts through start()
        setState(State.OFF)
    }

    private fun releaseTracker() {
        wantTracking = false
        generation++
        main.removeCallbacksAndMessages(retryToken) // only the retry: state posts must still reach the page
        tracker?.let {
            // Swap in callbacks that ignore everything rather than removing them: the SDK's gaze thread
            // can still deliver a frame it had queued, and a null callback crashes it (NPE in
            // GazeTrackerCore, seen when switching the pointing mode to Scan mid-tracking).
            it.setTrackingCallback(TrackingFor(-1)) // generation is never -1: nothing gets through
            it.setStatusCallback(ignoreStatus)
            it.setCalibrationCallback(ignoreCalibration)
            GazeTracker.releaseGazeTracker(it)
        }
        tracker = null
    }

    fun setCalibrationData(data: DoubleArray) {
        tracker?.setCalibrationData(data)
    }

    /** Five-point calibration inside the given screen rectangle (pixels). */
    fun startCalibration(left: Float, top: Float, right: Float, bottom: Float): Boolean {
        val t = tracker ?: return false
        return t.startCalibration(CalibrationModeType.FIVE_POINT, AccuracyCriteria.DEFAULT, left, top, right, bottom)
    }

    fun collectSamples() {
        tracker?.startCollectSamples()
    }

    fun stopCalibration() {
        tracker?.stopCalibration()
    }

    val calibrating: Boolean get() = tracker?.isCalibrating == true

    /** The tracker cannot run (also used by the activity, e.g. camera permission denied). */
    fun fail(detail: String) {
        Log.w(TAG, "gaze tracker not available: $detail")
        state = State.ERROR
        main.post { listener.onTrackerState("error", detail) }
    }

    private fun setState(s: State, detail: String = "") {
        state = s
        val word = when (s) {
            State.OFF -> "off"
            State.STARTING -> "starting"
            State.ON -> "on"
            State.ERROR -> "error"
        }
        main.post { listener.onTrackerState(word, detail) }
    }

    private val statusCallback = object : StatusCallback {
        override fun onStarted() = setState(State.ON)
        override fun onStopped(error: StatusErrorType) {
            if (error == StatusErrorType.ERROR_NONE) {
                // Paused (the app went to the background); resume() starts it again.
                setState(State.STARTING, "paused")
            } else {
                fail(error.name)
            }
        }
    }

    /** Tracking callbacks for one tracker; frames from a released one never reach the listener. */
    private inner class TrackingFor(private val gen: Int) : TrackingCallback {
        private fun post(block: () -> Unit) = main.post { if (gen == generation) block() }

        override fun onMetrics(timestamp: Long, gaze: GazeInfo, face: FaceInfo?, blink: BlinkInfo?, user: UserStatusInfo?) {
            if (blink != null) {
                if (blink.isBlink && !blinking) {
                    val (l, r) = blink.isBlinkLeft to blink.isBlinkRight
                    post { listener.onBlink(timestamp, l, r) }
                }
                blinking = blink.isBlink
            }
            if (!throttle.allow(System.currentTimeMillis())) return
            val (x, y, st) = Triple(gaze.x, gaze.y, gaze.trackingState)
            post { listener.onGaze(x, y, st) }
        }

        override fun onDrop(timestamp: Long) = Unit
    }

    /** For a released tracker: whatever it still says is ignored. */
    private val ignoreStatus = object : StatusCallback {
        override fun onStarted() {}
        override fun onStopped(error: StatusErrorType) {}
    }

    private val ignoreCalibration = object : CalibrationCallback {
        override fun onCalibrationProgress(progress: Float) {}
        override fun onCalibrationNextPoint(x: Float, y: Float) {}
        override fun onCalibrationFinished(data: DoubleArray) {}
        override fun onCalibrationCanceled(data: DoubleArray?) {}
    }

    private val calibrationCallback = object : CalibrationCallback {
        override fun onCalibrationProgress(progress: Float) {
            main.post { listener.onCalibrationProgress(progress) }
        }

        override fun onCalibrationNextPoint(x: Float, y: Float) {
            main.post { listener.onCalibrationPoint(x, y) }
        }

        override fun onCalibrationFinished(data: DoubleArray) {
            main.post { listener.onCalibrationFinished(data) }
        }

        override fun onCalibrationCanceled(data: DoubleArray?) {
            main.post { listener.onCalibrationCanceled() }
        }
    }

    companion object {
        private const val TAG = "EyedidGaze"
        const val TRACKING_FPS = 30
        const val FEED_INTERVAL_MS = 33L
        const val MAX_RETRIES = 12
        const val RETRY_MS = 5_000L
    }
}
