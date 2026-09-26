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
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.clench.eyetrack.calibration.CalibrationManager

@Composable
fun CalibrationOverlay(
    manager: CalibrationManager,
    collecting: Boolean,
    collectionProgress: Float,
    onDotTap: (index: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(Color(0xEB000000)),
    ) {
        val parentWidth = maxWidth
        val parentHeight = maxHeight
        val dotSize = 48.dp
        val tapTarget = 72.dp
        val ringSize = 64.dp

        manager.dotPositions.forEachIndexed { index, (fx, fy) ->
            val isDone = index < manager.currentIndex
            val isActive = index == manager.currentIndex
            val isCollecting = isActive && collecting

            val color = when {
                isDone -> Color(0xFF22C55E)
                isCollecting -> Color(0xFFFFFFFF)
                isActive -> Color(0xFFFACC15)
                else -> Color(0x66888888)
            }

            val scale by if (isActive && !isCollecting) {
                rememberInfiniteTransition(label = "pulse$index").animateFloat(
                    initialValue = 1f,
                    targetValue = 1.2f,
                    animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse),
                    label = "dotScale$index",
                )
            } else {
                androidx.compose.runtime.rememberUpdatedState(1f)
            }

            val offsetX = parentWidth * fx - tapTarget / 2
            val offsetY = parentHeight * fy - tapTarget / 2

            Box(
                modifier = Modifier
                    .offset(x = offsetX, y = offsetY)
                    .size(tapTarget)
                    .clickable(enabled = isActive && !collecting) { onDotTap(index) },
                contentAlignment = Alignment.Center,
            ) {
                // Progress ring when collecting
                if (isCollecting) {
                    val sweepAngle = collectionProgress * 360f
                    Box(
                        modifier = Modifier
                            .size(ringSize)
                            .drawBehind {
                                // Background ring
                                drawArc(
                                    color = Color(0x33FFFFFF),
                                    startAngle = -90f,
                                    sweepAngle = 360f,
                                    useCenter = false,
                                    style = Stroke(width = 5.dp.toPx(), cap = StrokeCap.Round),
                                )
                                // Progress arc
                                drawArc(
                                    color = Color(0xFF22C55E),
                                    startAngle = -90f,
                                    sweepAngle = sweepAngle,
                                    useCenter = false,
                                    style = Stroke(width = 5.dp.toPx(), cap = StrokeCap.Round),
                                )
                            },
                    )
                }

                // Dot
                Box(
                    modifier = Modifier
                        .size(if (isDone) 24.dp else dotSize * scale)
                        .background(color, CircleShape)
                        .then(
                            if (isActive && !isCollecting)
                                Modifier.border(2.dp, Color.White, CircleShape)
                            else Modifier
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    if (isDone) {
                        Text("✓", color = Color.White, fontSize = 14.sp)
                    }
                }
            }
        }

        // Instructions
        val instructionText = when {
            collecting -> "Hold still... collecting samples"
            else -> "Look at the yellow dot and tap it (${manager.currentIndex + 1}/9)"
        }
        Text(
            text = instructionText,
            color = Color(0xFFCCCCCC),
            fontSize = 18.sp,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 48.dp),
        )
    }
}
