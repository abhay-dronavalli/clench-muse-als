package com.clench.eyetrack.model

/** Raw iris gaze ratios (0..1) for each eye and the smoothed average. */
data class GazeRatios(
    val leftH: Float,
    val leftV: Float,
    val rightH: Float,
    val rightV: Float,
) {
    val avgH: Float get() = (leftH + rightH) / 2f
    val avgV: Float get() = (leftV + rightV) / 2f
}

/** Smoothed gaze mapped to screen coordinates (pixels), or null if not calibrated. */
data class ScreenGaze(
    val x: Float,
    val y: Float,
)

/** Full gaze state emitted each frame. */
data class GazeState(
    val ratios: GazeRatios,
    val smoothH: Float,
    val smoothV: Float,
    val screen: ScreenGaze?,
    val headPose: HeadPose,
    val headDrift: HeadDrift,
)
