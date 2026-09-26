package com.clench.eyetrack.ui.component

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp

@Composable
fun GazeDot(x: Float, y: Float, modifier: Modifier = Modifier) {
    val density = LocalDensity.current
    val size = 36.dp
    val halfPx = with(density) { (size / 2).toPx() }

    val animX by animateFloatAsState(
        targetValue = x,
        animationSpec = spring(dampingRatio = 0.7f, stiffness = Spring.StiffnessMediumLow),
        label = "gazeX",
    )
    val animY by animateFloatAsState(
        targetValue = y,
        animationSpec = spring(dampingRatio = 0.7f, stiffness = Spring.StiffnessMediumLow),
        label = "gazeY",
    )

    // Outer glow
    Box(
        modifier = modifier
            .offset(
                x = with(density) { (animX - halfPx - 6.dp.toPx()).toDp() },
                y = with(density) { (animY - halfPx - 6.dp.toPx()).toDp() },
            )
            .size(size + 12.dp)
            .background(Color(0x203B82F6), CircleShape),
    )

    // Inner dot
    Box(
        modifier = modifier
            .offset(
                x = with(density) { (animX - halfPx).toDp() },
                y = with(density) { (animY - halfPx).toDp() },
            )
            .size(size)
            .background(Color(0x803B82F6), CircleShape)
            .border(2.dp, Color(0xCC3B82F6), CircleShape),
    )
}
