package com.clench.eyetrack.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val DarkColors = darkColorScheme(
    primary = Color(0xFF3B82F6),
    onPrimary = Color.White,
    surface = Color(0xFF0A0A0A),
    onSurface = Color(0xFFE5E5E5),
    background = Color(0xFF0A0A0A),
    onBackground = Color(0xFFE5E5E5),
    error = Color(0xFFEF4444),
)

@Composable
fun EyeTrackTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColors,
        content = content,
    )
}
