package com.clench.eyetrack.ui.component

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp

/** Blue dot drawn at the estimated gaze screen position. */
@Composable
fun GazeDot(x: Float, y: Float, modifier: Modifier = Modifier) {
    val density = LocalDensity.current
    val size = 40.dp
    val halfPx = with(density) { (size / 2).toPx() }

    Box(
        modifier = modifier
            .offset(
                x = with(density) { (x - halfPx).toDp() },
                y = with(density) { (y - halfPx).toDp() },
            )
            .size(size)
            .background(Color(0x803B82F6), CircleShape)
            .border(2.dp, Color(0xCC3B82F6), CircleShape)
    )
}
