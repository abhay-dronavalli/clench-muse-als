package com.clench.eyetrack.tracking

import com.clench.eyetrack.model.HeadPose
import com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarkerResult
import kotlin.math.abs
import kotlin.math.atan2

/**
 * Estimates head yaw, pitch, and roll from MediaPipe face landmarks.
 *
 * Uses the geometric relationship between nose, forehead, chin, and eye
 * corners — no 3D model or PnP solve needed, fast enough for every frame.
 */
object HeadPoseEstimator {

    // Landmark indices
    private const val NOSE_TIP = 1
    private const val FOREHEAD = 10
    private const val CHIN = 152
    private const val L_EYE = 33
    private const val R_EYE = 263

    fun estimate(result: FaceLandmarkerResult): HeadPose? {
        val landmarks = result.faceLandmarks().firstOrNull() ?: return null
        val nose = landmarks[NOSE_TIP]
        val forehead = landmarks[FOREHEAD]
        val chin = landmarks[CHIN]
        val lEye = landmarks[L_EYE]
        val rEye = landmarks[R_EYE]

        // Yaw: horizontal offset of nose from eye midpoint, normalized by eye span
        val eyeMidX = (lEye.x() + rEye.x()) / 2f
        val eyeSpan = abs(rEye.x() - lEye.x())
        val yaw = if (eyeSpan > 0f) ((nose.x() - eyeMidX) / eyeSpan) * 90f else 0f

        // Pitch: vertical offset of nose from forehead-chin midpoint
        val faceMidY = (forehead.y() + chin.y()) / 2f
        val faceH = abs(chin.y() - forehead.y())
        val pitch = if (faceH > 0f) ((nose.y() - faceMidY) / faceH) * 90f else 0f

        // Roll: angle of the eye-corner line
        val dx = rEye.x() - lEye.x()
        val dy = rEye.y() - lEye.y()
        val roll = Math.toDegrees(atan2(dy.toDouble(), dx.toDouble())).toFloat()

        return HeadPose(yaw, pitch, roll)
    }
}
