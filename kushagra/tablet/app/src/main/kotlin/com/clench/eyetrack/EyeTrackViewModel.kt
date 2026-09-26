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

/**
 * Central ViewModel that owns all tracking state.
 *
 * The camera feeds frames here via [onFrame]. The UI observes [gazeState]
 * and [calibrationStep] to draw the gaze dot, head guide, and calibration overlay.
 */
class EyeTrackViewModel(app: Application) : AndroidViewModel(app) {

    // --- MediaPipe ---
    private val landmarkerHelper = FaceLandmarkerHelper(app)

    // --- Smoothing ---
    private val sgH = SavGolFilter(windowSize = 15, polyOrder = 3)
    private val sgV = SavGolFilter(windowSize = 15, polyOrder = 3)

    // --- Calibration ---
    val calibrationManager = CalibrationManager()

    // --- State flows for the UI ---
    private val _gazeState = MutableStateFlow<GazeState?>(null)
    val gazeState: StateFlow<GazeState?> = _gazeState.asStateFlow()

    private val _calibrationStep = MutableStateFlow(CalibrationStep.IDLE)
    val calibrationStep: StateFlow<CalibrationStep> = _calibrationStep.asStateFlow()

    private val _calibrationResult = MutableStateFlow<CalibrationResult?>(null)
    val calibrationResult: StateFlow<CalibrationResult?> = _calibrationResult.asStateFlow()

    // Screen dimensions (set by the UI layer)
    var screenWidth: Float = 1f
    var screenHeight: Float = 1f

    /** Called for every camera frame. Runs detection + gaze math. */
    fun onFrame(image: MPImage, timestampMs: Long) {
        val result = landmarkerHelper.detect(image, timestampMs) ?: return

        // Iris gaze ratios
        val ratios = IrisGazeEstimator.estimate(result) ?: return

        // Smooth
        sgH.push(ratios.avgH)
        sgV.push(ratios.avgV)
        val smoothH = sgH.get()
        val smoothV = sgV.get()

        // Head pose
        val headPose = HeadPoseEstimator.estimate(result) ?: HeadPose(0f, 0f, 0f)

        // Drift check
        val calResult = calibrationManager.result
        val drift = if (calResult != null) {
            HeadPoseGuard.check(headPose, calResult.headPose)
        } else {
            HeadDrift.NONE
        }

        // Map to screen
        val screen = calResult?.let {
            GazeMapper.map(smoothH, smoothV, it.coeffs, screenWidth, screenHeight)
        }

        _gazeState.value = GazeState(ratios, smoothH, smoothV, screen, headPose, drift)
    }

    /** Start or restart calibration. */
    fun startCalibration() {
        calibrationManager.reset()
        sgH.reset()
        sgV.reset()
        _calibrationStep.value = CalibrationStep.IN_PROGRESS
        _calibrationResult.value = null
    }

    /** Record the current gaze at the active calibration dot. */
    fun recordCalibrationPoint() {
        val state = _gazeState.value ?: return
        val snapshot = _gazeState.value?.let {
            // We need the latest FaceLandmarkerResult for the snapshot,
            // but we already extracted it into the gaze state. Build from current ratios.
            null // Will be filled by the overload below
        }
        recordCalibrationPoint(state.smoothH, state.smoothV, state.headPose, null)
    }

    /** Record with explicit face snapshot (called from onFrame context if needed). */
    fun recordCalibrationPoint(
        smoothH: Float,
        smoothV: Float,
        headPose: HeadPose?,
        faceSnapshot: FacePoseSnapshot?,
    ) {
        val done = calibrationManager.recordPoint(smoothH, smoothV, headPose, faceSnapshot)
        if (done) {
            _calibrationStep.value = CalibrationStep.DONE
            _calibrationResult.value = calibrationManager.result
        }
    }

    // Keep the latest FaceLandmarkerResult so we can grab a face snapshot on cal click
    private var latestLandmarkerResult: com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarkerResult? = null

    /** Full frame processing that also stores the result for snapshot access. */
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
        val drift = if (calResult != null) HeadPoseGuard.check(headPose, calResult.headPose) else HeadDrift.NONE
        val screen = calResult?.let { GazeMapper.map(smoothH, smoothV, it.coeffs, screenWidth, screenHeight) }
        _gazeState.value = GazeState(ratios, smoothH, smoothV, screen, headPose, drift)
    }

    /** Record calibration point using the latest stored landmarks for the face snapshot. */
    fun recordCalibrationPointWithSnapshot() {
        val state = _gazeState.value ?: return
        val snapshot = latestLandmarkerResult?.let { IrisGazeEstimator.snapshot(it) }
        val done = calibrationManager.recordPoint(state.smoothH, state.smoothV, state.headPose, snapshot)
        if (done) {
            _calibrationStep.value = CalibrationStep.DONE
            _calibrationResult.value = calibrationManager.result
        }
    }

    override fun onCleared() {
        landmarkerHelper.close()
    }
}
