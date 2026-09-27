package com.clench.eyetrack.model

/** Head orientation estimated from face landmarks (degrees). */
data class HeadPose(
    val yaw: Float,
    val pitch: Float,
    val roll: Float,
)

/** How far the head has drifted from the calibrated pose. */
data class HeadDrift(
    val level: DriftLevel,
    val deltaYaw: Float,
    val deltaPitch: Float,
    val deltaRoll: Float,
) {
    companion object {
        val NONE = HeadDrift(DriftLevel.ALIGNED, 0f, 0f, 0f)
    }
}

enum class DriftLevel { ALIGNED, NUDGE, WARNING }
