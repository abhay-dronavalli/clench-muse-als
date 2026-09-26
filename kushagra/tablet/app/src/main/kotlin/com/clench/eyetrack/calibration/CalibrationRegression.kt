package com.clench.eyetrack.calibration

import com.clench.eyetrack.model.CalibrationCoeffs
import com.clench.eyetrack.model.CalibrationSample

/**
 * Fits a linear regression from gaze ratios to screen fractions.
 *
 *   screenX = ax * gazeH + bx
 *   screenY = ay * gazeV + by
 *
 * Uses ordinary least squares (closed-form). Needs at least 3 samples.
 */
object CalibrationRegression {

    fun fit(samples: List<CalibrationSample>): CalibrationCoeffs {
        require(samples.size >= 3) { "Need at least 3 calibration points" }

        val n = samples.size.toDouble()
        var sumH = 0.0; var sumV = 0.0; var sumSx = 0.0; var sumSy = 0.0
        var sumHH = 0.0; var sumVV = 0.0; var sumHSx = 0.0; var sumVSy = 0.0

        for (s in samples) {
            sumH += s.gazeH; sumV += s.gazeV
            sumSx += s.screenX; sumSy += s.screenY
            sumHH += s.gazeH * s.gazeH; sumVV += s.gazeV * s.gazeV
            sumHSx += s.gazeH * s.screenX; sumVSy += s.gazeV * s.screenY
        }

        val detH = n * sumHH - sumH * sumH
        val detV = n * sumVV - sumV * sumV
        require(detH > 1e-9 && detV > 1e-9) { "Degenerate calibration data" }

        val ax = ((n * sumHSx - sumH * sumSx) / detH).toFloat()
        val bx = ((sumSx - ax * sumH) / n).toFloat()
        val ay = ((n * sumVSy - sumV * sumSy) / detV).toFloat()
        val by = ((sumSy - ay * sumV) / n).toFloat()

        return CalibrationCoeffs(ax, bx, ay, by)
    }
}
