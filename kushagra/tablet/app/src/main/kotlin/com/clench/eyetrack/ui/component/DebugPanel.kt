package com.clench.eyetrack.ui.component

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.clench.eyetrack.model.CalibrationAccuracy
import com.clench.eyetrack.model.GazeState

@Composable
fun DebugPanel(
    state: GazeState?,
    fps: Int = 0,
    accuracy: CalibrationAccuracy? = null,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .background(Color(0xE6141414), RoundedCornerShape(12.dp))
            .padding(12.dp)
            .width(220.dp),
    ) {
        Text("Iris Tracking", color = Color(0xFF3B82F6), fontSize = 13.sp, fontFamily = FontFamily.Monospace)
        Spacer(Modifier.height(4.dp))

        Row("FPS", "$fps", if (fps >= 20) Color(0xFF22C55E) else Color(0xFFEF4444))

        if (state == null) {
            Row("Status", "No face")
            return@Column
        }

        Spacer(Modifier.height(2.dp))
        Row("Smooth H", "%.3f".format(state.smoothH))
        Row("Smooth V", "%.3f".format(state.smoothV))

        state.screen?.let {
            Row("Screen", "%.0f, %.0f".format(it.x, it.y))
        } ?: Row("Screen", "not cal'd")

        Spacer(Modifier.height(2.dp))
        Row("Yaw", "%.1f°".format(state.headPose.yaw))
        Row("Pitch", "%.1f°".format(state.headPose.pitch))
        Row("Roll", "%.1f°".format(state.headPose.roll))

        val driftColor = when (state.headDrift.level) {
            com.clench.eyetrack.model.DriftLevel.ALIGNED -> Color(0xFF22C55E)
            com.clench.eyetrack.model.DriftLevel.NUDGE -> Color(0xFFFACC15)
            com.clench.eyetrack.model.DriftLevel.WARNING -> Color(0xFFEF4444)
        }
        Row("Drift", state.headDrift.level.name, driftColor)

        accuracy?.let {
            Spacer(Modifier.height(4.dp))
            Text("Calibration", color = Color(0xFF3B82F6), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            Row("Mean err", "%.0fpx".format(it.meanErrorPx))
            Row("Max err", "%.0fpx".format(it.maxErrorPx))
            Row("Accuracy", "%.1f%%".format(100f - it.meanErrorPct),
                if (it.meanErrorPct < 5f) Color(0xFF22C55E) else Color(0xFFFACC15))
        }
    }
}

@Composable
private fun Row(label: String, value: String, valueColor: Color = Color(0xFFCCCCCC)) {
    androidx.compose.foundation.layout.Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = Color(0xFF888888), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
        Text(value, color = valueColor, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
    }
}
