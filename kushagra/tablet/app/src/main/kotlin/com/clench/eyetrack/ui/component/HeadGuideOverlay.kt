package com.clench.eyetrack.ui.component

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import com.clench.eyetrack.model.CalibrationResult
import com.clench.eyetrack.model.DriftLevel
import com.clench.eyetrack.model.FacePoseSnapshot
import com.clench.eyetrack.model.HeadDrift
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Draws the visual head-repositioning guide on the camera preview.
 *
 * - Green dashed outline: where the face was during calibration (ghost)
 * - Yellow/red outline: where the face is now
 * - Arrow from current nose to calibrated nose
 * - Green crosshair at the calibrated nose target
 *
 * Only visible when [drift] is not ALIGNED.
 */
@Composable
fun HeadGuideOverlay(
    calibration: CalibrationResult,
    currentFace: FacePoseSnapshot?,
    drift: HeadDrift,
    modifier: Modifier = Modifier,
) {
    if (drift.level == DriftLevel.ALIGNED || currentFace == null) return

    val calFace = calibration.faceSnapshot
    val driftColor = if (drift.level == DriftLevel.WARNING) Color(0xB3EF4444) else Color(0x99FACC15)
    val ghostColor = Color(0x8022C55E)
    val alpha = if (drift.level == DriftLevel.WARNING) 0.9f else 0.5f

    Canvas(modifier = modifier.fillMaxSize()) {
        val w = size.width
        val h = size.height

        // Ghost (calibrated) face outline
        drawFaceOutline(calFace, w, h, ghostColor, dashed = true)

        // Current face outline
        drawFaceOutline(currentFace, w, h, driftColor, dashed = false)

        // Arrow from current nose to calibrated nose
        val curNose = Offset(currentFace.noseX * w, currentFace.noseY * h)
        val calNose = Offset(calFace.noseX * w, calFace.noseY * h)
        val dx = calNose.x - curNose.x
        val dy = calNose.y - curNose.y
        val dist = sqrt(dx * dx + dy * dy)

        if (dist > 3f) {
            drawLine(driftColor, curNose, calNose, strokeWidth = 3f)

            // Arrowhead
            val angle = atan2(dy, dx)
            val headLen = min(14f, dist * 0.4f)
            drawLine(
                driftColor,
                calNose,
                Offset(calNose.x - headLen * cos(angle - 0.4f), calNose.y - headLen * sin(angle - 0.4f)),
                strokeWidth = 3f,
            )
            drawLine(
                driftColor,
                calNose,
                Offset(calNose.x - headLen * cos(angle + 0.4f), calNose.y - headLen * sin(angle + 0.4f)),
                strokeWidth = 3f,
            )
        }

        // Green crosshair at calibrated nose
        val cr = 12f
        drawCircle(Color(0xCC22C55E), cr, calNose, style = androidx.compose.ui.graphics.drawscope.Stroke(2f))
        drawLine(Color(0xCC22C55E), Offset(calNose.x - cr - 5, calNose.y), Offset(calNose.x + cr + 5, calNose.y), strokeWidth = 1.5f)
        drawLine(Color(0xCC22C55E), Offset(calNose.x, calNose.y - cr - 5), Offset(calNose.x, calNose.y + cr + 5), strokeWidth = 1.5f)
    }
}

private fun DrawScope.drawFaceOutline(
    face: FacePoseSnapshot,
    w: Float,
    h: Float,
    color: Color,
    dashed: Boolean,
) {
    val points = listOf(
        Offset(face.foreheadX * w, face.foreheadY * h),
        Offset(face.leftEyeX * w, face.leftEyeY * h),
        Offset(face.leftMouthX * w, face.leftMouthY * h),
        Offset(face.chinX * w, face.chinY * h),
        Offset(face.rightMouthX * w, face.rightMouthY * h),
        Offset(face.rightEyeX * w, face.rightEyeY * h),
        Offset(face.foreheadX * w, face.foreheadY * h), // close the loop
    )

    val pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(8f, 6f)) else null

    for (i in 0 until points.size - 1) {
        drawLine(color, points[i], points[i + 1], strokeWidth = 2.5f, pathEffect = pathEffect)
    }

    // Dots at each landmark
    for (point in points) {
        drawCircle(color, 5f, point)
    }
}
