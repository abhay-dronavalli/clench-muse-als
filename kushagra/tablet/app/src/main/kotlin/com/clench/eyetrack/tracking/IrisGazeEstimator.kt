package com.clench.eyetrack.tracking

import com.clench.eyetrack.model.FacePoseSnapshot
import com.clench.eyetrack.model.GazeRatios
import com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarkerResult

/**
 * Computes iris gaze ratios from MediaPipe's 478-landmark face mesh.
 *
 * For each eye, the iris center position is expressed as a fraction of the
 * eye opening width (horizontal) and height (vertical):
 *   0.0 = looking fully toward the outer corner / top
 *   1.0 = looking fully toward the inner corner / bottom
 *   0.5 = centered
 */
object IrisGazeEstimator {

    // Iris center landmarks (indices 468-477 are the iris ring)
    private const val L_IRIS_CENTER = 468
    private const val R_IRIS_CENTER = 473

    // Eye corner and lid landmarks
    private const val L_EYE_OUTER = 33
    private const val L_EYE_INNER = 133
    private const val L_EYE_TOP = 159
    private const val L_EYE_BOT = 145

    private const val R_EYE_INNER = 362
    private const val R_EYE_OUTER = 263
    private const val R_EYE_TOP = 386
    private const val R_EYE_BOT = 374

    // Landmarks used for the calibration face snapshot
    private const val NOSE_TIP = 1
    private const val FOREHEAD = 10
    private const val CHIN = 152
    private const val L_MOUTH = 61
    private const val R_MOUTH = 291

    fun estimate(result: FaceLandmarkerResult): GazeRatios? {
        val lm = result.faceLandmarks().firstOrNull() ?: return null
        if (lm.size < 478) return null

        val lIris = lm[L_IRIS_CENTER]
        val rIris = lm[R_IRIS_CENTER]

        // Left eye horizontal
        val lEyeW = lm[L_EYE_INNER].x() - lm[L_EYE_OUTER].x()
        val leftH = if (lEyeW > 0f) (lIris.x() - lm[L_EYE_OUTER].x()) / lEyeW else 0.5f

        // Right eye horizontal
        val rEyeW = lm[R_EYE_OUTER].x() - lm[R_EYE_INNER].x()
        val rightH = if (rEyeW > 0f) (rIris.x() - lm[R_EYE_INNER].x()) / rEyeW else 0.5f

        // Left eye vertical
        val lEyeH = lm[L_EYE_BOT].y() - lm[L_EYE_TOP].y()
        val leftV = if (lEyeH > 0f) (lIris.y() - lm[L_EYE_TOP].y()) / lEyeH else 0.5f

        // Right eye vertical
        val rEyeH = lm[R_EYE_BOT].y() - lm[R_EYE_TOP].y()
        val rightV = if (rEyeH > 0f) (rIris.y() - lm[R_EYE_TOP].y()) / rEyeH else 0.5f

        return GazeRatios(leftH, leftV, rightH, rightV)
    }

    /** Capture a snapshot of key face landmarks for the head-position guide. */
    fun snapshot(result: FaceLandmarkerResult): FacePoseSnapshot? {
        val lm = result.faceLandmarks().firstOrNull() ?: return null
        return FacePoseSnapshot(
            noseX = lm[NOSE_TIP].x(), noseY = lm[NOSE_TIP].y(),
            foreheadX = lm[FOREHEAD].x(), foreheadY = lm[FOREHEAD].y(),
            chinX = lm[CHIN].x(), chinY = lm[CHIN].y(),
            leftEyeX = lm[L_EYE_OUTER].x(), leftEyeY = lm[L_EYE_OUTER].y(),
            rightEyeX = lm[R_EYE_OUTER].x(), rightEyeY = lm[R_EYE_OUTER].y(),
            leftMouthX = lm[L_MOUTH].x(), leftMouthY = lm[L_MOUTH].y(),
            rightMouthX = lm[R_MOUTH].x(), rightMouthY = lm[R_MOUTH].y(),
        )
    }
}
