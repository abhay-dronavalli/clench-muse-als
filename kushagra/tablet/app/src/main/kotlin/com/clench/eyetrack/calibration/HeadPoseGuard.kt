package com.clench.eyetrack.calibration

import com.clench.eyetrack.model.DriftLevel
import com.clench.eyetrack.model.HeadDrift
import com.clench.eyetrack.model.HeadPose
import kotlin.math.abs

/**
 * Detects how far the current head pose has drifted from the calibrated pose
 * and produces a [HeadDrift] with the drift level and per-axis deltas.
 */
object HeadPoseGuard {

    // Degrees of tolerance before each level triggers
    private const val NUDGE_YAW = 4f
    private const val WARN_YAW = 8f
    private const val NUDGE_PITCH = 4f
    private const val WARN_PITCH = 8f
    private const val NUDGE_ROLL = 3f
    private const val WARN_ROLL = 6f

    fun check(current: HeadPose, calibrated: HeadPose): HeadDrift {
        val dYaw = current.yaw - calibrated.yaw
        val dPitch = current.pitch - calibrated.pitch
        val dRoll = current.roll - calibrated.roll

        val level = when {
            abs(dYaw) > WARN_YAW || abs(dPitch) > WARN_PITCH || abs(dRoll) > WARN_ROLL ->
                DriftLevel.WARNING
            abs(dYaw) > NUDGE_YAW || abs(dPitch) > NUDGE_PITCH || abs(dRoll) > NUDGE_ROLL ->
                DriftLevel.NUDGE
            else ->
                DriftLevel.ALIGNED
        }

        return HeadDrift(level, dYaw, dPitch, dRoll)
    }
}
