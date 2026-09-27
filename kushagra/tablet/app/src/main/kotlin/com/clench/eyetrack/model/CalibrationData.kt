package com.clench.eyetrack.model

/** A single calibration sample: gaze ratios at a known screen position. */
data class CalibrationSample(
    val gazeH: Float,
    val gazeV: Float,
    val screenX: Float, // 0..1 fraction
    val screenY: Float, // 0..1 fraction
)

/** Linear coefficients mapping gaze ratios to screen fractions.
 *  screenX = ax * gazeH + bx
 *  screenY = ay * gazeV + by */
data class CalibrationCoeffs(
    val ax: Float,
    val bx: Float,
    val ay: Float,
    val by: Float,
)

/** A snapshot of key face landmark positions (normalized 0..1) during calibration. */
data class FacePoseSnapshot(
    val noseX: Float, val noseY: Float,
    val foreheadX: Float, val foreheadY: Float,
    val chinX: Float, val chinY: Float,
    val leftEyeX: Float, val leftEyeY: Float,
    val rightEyeX: Float, val rightEyeY: Float,
    val leftMouthX: Float, val leftMouthY: Float,
    val rightMouthX: Float, val rightMouthY: Float,
)

/** Everything stored after a completed calibration. */
data class CalibrationResult(
    val coeffs: CalibrationCoeffs,
    val headPose: HeadPose,
    val faceSnapshot: FacePoseSnapshot,
)

/** Accuracy stats computed after calibration completes. */
data class CalibrationAccuracy(
    val meanErrorPx: Float,
    val maxErrorPx: Float,
    val meanErrorPct: Float,
)

/** The 9-point calibration flow state. */
enum class CalibrationStep {
    IDLE,
    IN_PROGRESS,
    DONE,
}
