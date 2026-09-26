package com.clench.eyetrack.ui.screen

import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.clench.eyetrack.calibration.CalibrationManager

/**
 * Full-screen overlay showing the 9 calibration dots.
 *
 * The active dot pulses yellow. Completed dots turn green and dim.
 * The user looks at the yellow dot and taps it to record a sample.
 */
@Composable
fun CalibrationOverlay(
    manager: CalibrationManager,
    onDotTap: (index: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Color(0xEB000000)),
    ) {
        // Calibration dots
        manager.dotPositions.forEachIndexed { index, (fx, fy) ->
            val isDone = index < manager.currentIndex
            val isActive = index == manager.currentIndex

            CalibrationDot(
                x = fx,
                y = fy,
                isActive = isActive,
                isDone = isDone,
                onClick = { if (isActive) onDotTap(index) },
            )
        }

        // Instructions
        Text(
            text = "Look at the yellow dot and tap it (${manager.currentIndex + 1}/9)",
            color = Color(0xFFCCCCCC),
            fontSize = 16.sp,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 48.dp),
        )
    }
}

@Composable
private fun CalibrationDot(
    x: Float,
    y: Float,
    isActive: Boolean,
    isDone: Boolean,
    onClick: () -> Unit,
) {
    val color = when {
        isDone -> Color(0x6622C55E)
        isActive -> Color(0xFFFACC15)
        else -> Color(0xFFEF4444)
    }

    val scale by if (isActive) {
        rememberInfiniteTransition(label = "pulse").animateFloat(
            initialValue = 1f,
            targetValue = 1.3f,
            animationSpec = infiniteRepeatable(tween(600), RepeatMode.Reverse),
            label = "dotScale",
        )
    } else {
        rememberUpdatedState(1f)
    }

    BoxWithConstraints(Modifier.fillMaxSize()) {
        val dotSize = 32.dp
        val offsetX = maxWidth * x - dotSize / 2
        val offsetY = maxHeight * y - dotSize / 2

        Box(
            modifier = Modifier
                .offset(x = offsetX, y = offsetY)
                .size(dotSize * scale)
                .background(color, CircleShape)
                .then(
                    if (isActive) Modifier.border(2.dp, Color.White, CircleShape) else Modifier
                )
                .clickable(enabled = isActive, onClick = onClick),
        )
    }
}

@Composable
private fun <T> rememberUpdatedState(value: T): State<T> {
    return androidx.compose.runtime.rememberUpdatedState(value)
}
