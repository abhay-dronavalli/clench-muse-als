package com.clench.eyetrack

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import com.clench.eyetrack.calibration.CalibrationManager
import com.clench.eyetrack.calibration.HeadPoseGuard
import com.clench.eyetrack.model.*
import com.clench.eyetrack.tracking.*
import com.google.mediapipe.framework.image.MPImage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.sqrt

class EyeTrackViewModel(app: Application) : AndroidViewModel(app) {

    private val landmarkerHelper = FaceLandmarkerHelper(app)
    private val sgH = SavGolFilter(windowSize = 15, polyOrder = 3)
    private val sgV = SavGolFilter(windowSize = 15, polyOrder = 3)
    val calibrationManager = CalibrationManager()

    // --- UI state flows ---
    private val _gazeState = MutableStateFlow<GazeState?>(null)
    val gazeState: StateFlow<GazeState?> = _gazeState.asStateFlow()

    private val _calibrationStep = MutableStateFlow(CalibrationStep.IDLE)
    val calibrationStep: StateFlow<CalibrationStep> = _calibrationStep.asStateFlow()

    private val _calibrationResult = MutableStateFlow<CalibrationResult?>(null)
    val calibrationResult: StateFlow<CalibrationResult?> = _calibrationResult.asStateFlow()

    private val _calibrationAccuracy = MutableStateFlow<CalibrationAccuracy?>(null)
    val calibrationAccuracy: StateFlow<CalibrationAccuracy?> = _calibrationAccuracy.asStateFlow()

    // Tile highlighting
    private val _highlightedTile = MutableStateFlow(-1)
    val highlightedTile: StateFlow<Int> = _highlightedTile.asStateFlow()

    // FPS tracking
    private val _fps = MutableStateFlow(0)
    val fps: StateFlow<Int> = _fps.asStateFlow()

    var screenWidth: Float = 1f
    var screenHeight: Float = 1f

    // FPS counter
    private var frameCount = 0
    private var lastFpsTime = System.currentTimeMillis()

    // Latest landmarker result for face snapshots
    private var latestLandmarkerResult:
        com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarkerResult? = null

    fun onFrameFull(image: MPImage, timestampMs: Long) {
        val result = landmarkerHelper.detect(image, timestampMs) ?: return
        latestLandmarkerResult = result

        val ratios = IrisGazeEstimator.estimate(result) ?: return
        sgH.push(ratios.avgH)
        sgV.push(ratios.avgV)
        val smoothH = sgH.get()
        val smoothV = sgV.get()

        val headPose = HeadPoseEstimator.estimate(result) ?: HeadPose(0f, 0f, 0f)

        val calResult = calibrationManager.result
        val drift = if (calResult != null) {
            HeadPoseGuard.check(headPose, calResult.headPose)
        } else {
            HeadDrift.NONE
        }

        val screen = calResult?.let {
            GazeMapper.map(smoothH, smoothV, it.coeffs, screenWidth, screenHeight)
        }

        _gazeState.value = GazeState(ratios, smoothH, smoothV, screen, headPose, drift)

        // Update tile highlighting
        if (screen != null && _calibrationStep.value == CalibrationStep.DONE) {
            val col = ((screen.x / screenWidth) * 3).toInt().coerceIn(0, 2)
            val row = ((screen.y / screenHeight) * 2).toInt().coerceIn(0, 1)
            _highlightedTile.value = row * 3 + col
        } else {
            _highlightedTile.value = -1
        }

        // FPS
        frameCount++
        val now = System.currentTimeMillis()
        if (now - lastFpsTime >= 1000) {
            _fps.value = frameCount
            frameCount = 0
            lastFpsTime = now
        }
    }

    fun startCalibration() {
        calibrationManager.reset()
        sgH.reset()
        sgV.reset()
        _calibrationStep.value = CalibrationStep.IN_PROGRESS
        _calibrationResult.value = null
        _calibrationAccuracy.value = null
        _highlightedTile.value = -1
    }

    /** Tap a dot to instantly record the current smoothed gaze (matches index.html). */
    fun recordCalibrationDot() {
        val state = _gazeState.value ?: return
        val snapshot = latestLandmarkerResult?.let { IrisGazeEstimator.snapshot(it) }

        val done = calibrationManager.recordPoint(state.smoothH, state.smoothV, state.headPose, snapshot)

        if (done) {
            _calibrationStep.value = CalibrationStep.DONE
            _calibrationResult.value = calibrationManager.result
            computeAccuracy()
        }
    }

    private fun computeAccuracy() {
        val result = calibrationManager.result ?: return
        val samples = calibrationManager.samples
        if (samples.isEmpty()) return

        var totalErr = 0f
        var maxErr = 0f
        for (s in samples) {
            val mappedX = (result.coeffs.ax * s.gazeH + result.coeffs.bx).coerceIn(0f, 1f)
            val mappedY = (result.coeffs.ay * s.gazeV + result.coeffs.by).coerceIn(0f, 1f)
            val dx = (mappedX - s.screenX) * screenWidth
            val dy = (mappedY - s.screenY) * screenHeight
            val err = sqrt(dx * dx + dy * dy)
            totalErr += err
            if (err > maxErr) maxErr = err
        }
        val meanPx = totalErr / samples.size
        val diag = sqrt(screenWidth * screenWidth + screenHeight * screenHeight)
        _calibrationAccuracy.value = CalibrationAccuracy(
            meanErrorPx = meanPx,
            maxErrorPx = maxErr,
            meanErrorPct = (meanPx / diag) * 100f,
        )
    }

    override fun onCleared() {
        landmarkerHelper.close()
    }
}
