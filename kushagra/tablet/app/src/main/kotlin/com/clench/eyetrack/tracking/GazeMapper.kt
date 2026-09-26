package com.clench.eyetrack.tracking

import com.clench.eyetrack.model.CalibrationCoeffs
import com.clench.eyetrack.model.ScreenGaze

/**
 * Maps smoothed gaze ratios to screen pixel coordinates using
 * the linear regression from calibration.
 *
 *   screenX = ax * gazeH + bx   (in 0..1, then scaled by screen width)
 *   screenY = ay * gazeV + by
 */
object GazeMapper {

    fun map(
        gazeH: Float,
        gazeV: Float,
        coeffs: CalibrationCoeffs,
        screenWidth: Float,
        screenHeight: Float,
    ): ScreenGaze {
        val sx = (coeffs.ax * gazeH + coeffs.bx).coerceIn(0f, 1f)
        val sy = (coeffs.ay * gazeV + coeffs.by).coerceIn(0f, 1f)
        return ScreenGaze(sx * screenWidth, sy * screenHeight)
    }
}
