package com.clench.eyetrack.calibration

import com.clench.eyetrack.model.*

/**
 * Manages the 9-point calibration flow.
 *
 * Call [recordPoint] each time the user taps a calibration dot while looking at it.
 * After all 9 points, [result] holds the fitted coefficients, the average head pose,
 * and the average face landmark snapshot.
 */
class CalibrationManager {

    /** The 9 calibration dot positions as screen fractions (0..1). */
    val dotPositions: List<Pair<Float, Float>> = listOf(
        0.10f to 0.10f, 0.50f to 0.10f, 0.90f to 0.10f,
        0.10f to 0.50f, 0.50f to 0.50f, 0.90f to 0.50f,
        0.10f to 0.90f, 0.50f to 0.90f, 0.90f to 0.90f,
    )

    private val gazeSamples = mutableListOf<CalibrationSample>()

    /** Read-only view for accuracy computation. */
    val samples: List<CalibrationSample> get() = gazeSamples
    private val headSamples = mutableListOf<HeadPose>()
    private val faceSamples = mutableListOf<FacePoseSnapshot>()

    var currentIndex: Int = 0
        private set

    val step: CalibrationStep
        get() = when {
            currentIndex == 0 && gazeSamples.isEmpty() -> CalibrationStep.IDLE
            currentIndex >= dotPositions.size -> CalibrationStep.DONE
            else -> CalibrationStep.IN_PROGRESS
        }

    var result: CalibrationResult? = null
        private set

    /** Record a sample at the current dot. Returns true when calibration is complete. */
    fun recordPoint(
        smoothH: Float,
        smoothV: Float,
        headPose: HeadPose?,
        faceSnapshot: FacePoseSnapshot?,
    ): Boolean {
        if (currentIndex >= dotPositions.size) return true

        val (sx, sy) = dotPositions[currentIndex]
        gazeSamples.add(CalibrationSample(smoothH, smoothV, sx, sy))
        headPose?.let { headSamples.add(it) }
        faceSnapshot?.let { faceSamples.add(it) }

        currentIndex++

        if (currentIndex >= dotPositions.size) {
            result = buildResult()
            return true
        }
        return false
    }

    fun reset() {
        gazeSamples.clear()
        headSamples.clear()
        faceSamples.clear()
        currentIndex = 0
        result = null
    }

    private fun buildResult(): CalibrationResult {
        val coeffs = CalibrationRegression.fit(gazeSamples)
        val avgHead = averageHeadPose(headSamples)
        val avgFace = averageFaceSnapshot(faceSamples)
        return CalibrationResult(coeffs, avgHead, avgFace)
    }

    private fun averageHeadPose(samples: List<HeadPose>): HeadPose {
        if (samples.isEmpty()) return HeadPose(0f, 0f, 0f)
        val n = samples.size.toFloat()
        return HeadPose(
            yaw = samples.sumOf { it.yaw.toDouble() }.toFloat() / n,
            pitch = samples.sumOf { it.pitch.toDouble() }.toFloat() / n,
            roll = samples.sumOf { it.roll.toDouble() }.toFloat() / n,
        )
    }

    private fun averageFaceSnapshot(samples: List<FacePoseSnapshot>): FacePoseSnapshot {
        if (samples.isEmpty()) return FacePoseSnapshot(0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f)
        val n = samples.size.toFloat()
        return FacePoseSnapshot(
            noseX = samples.sumOf { it.noseX.toDouble() }.toFloat() / n,
            noseY = samples.sumOf { it.noseY.toDouble() }.toFloat() / n,
            foreheadX = samples.sumOf { it.foreheadX.toDouble() }.toFloat() / n,
            foreheadY = samples.sumOf { it.foreheadY.toDouble() }.toFloat() / n,
            chinX = samples.sumOf { it.chinX.toDouble() }.toFloat() / n,
            chinY = samples.sumOf { it.chinY.toDouble() }.toFloat() / n,
            leftEyeX = samples.sumOf { it.leftEyeX.toDouble() }.toFloat() / n,
            leftEyeY = samples.sumOf { it.leftEyeY.toDouble() }.toFloat() / n,
            rightEyeX = samples.sumOf { it.rightEyeX.toDouble() }.toFloat() / n,
            rightEyeY = samples.sumOf { it.rightEyeY.toDouble() }.toFloat() / n,
            leftMouthX = samples.sumOf { it.leftMouthX.toDouble() }.toFloat() / n,
            leftMouthY = samples.sumOf { it.leftMouthY.toDouble() }.toFloat() / n,
            rightMouthX = samples.sumOf { it.rightMouthX.toDouble() }.toFloat() / n,
            rightMouthY = samples.sumOf { it.rightMouthY.toDouble() }.toFloat() / n,
        )
    }
}
