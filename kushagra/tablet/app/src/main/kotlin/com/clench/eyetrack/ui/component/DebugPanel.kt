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
import com.clench.eyetrack.model.GazeState

/** Shows live iris tracking and head pose values for debugging. */
@Composable
fun DebugPanel(state: GazeState?, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .background(Color(0xE6141414), RoundedCornerShape(12.dp))
            .padding(12.dp)
            .width(220.dp),
    ) {
        Text("Iris Tracking", color = Color(0xFF3B82F6), fontSize = 13.sp, fontFamily = FontFamily.Monospace)
        Spacer(Modifier.height(4.dp))

        if (state == null) {
            Row("No face", "-")
            return@Column
        }

        Row("L gaze H", "%.3f".format(state.ratios.leftH))
        Row("R gaze H", "%.3f".format(state.ratios.rightH))
        Row("L gaze V", "%.3f".format(state.ratios.leftV))
        Row("R gaze V", "%.3f".format(state.ratios.rightV))
        Row("Smooth H", "%.3f".format(state.smoothH))
        Row("Smooth V", "%.3f".format(state.smoothV))

        state.screen?.let {
            Row("Screen X", "%.0f".format(it.x))
            Row("Screen Y", "%.0f".format(it.y))
        } ?: Row("Screen", "not cal'd")

        Spacer(Modifier.height(4.dp))
        Row("Yaw", "%.1f\u00B0".format(state.headPose.yaw))
        Row("Pitch", "%.1f\u00B0".format(state.headPose.pitch))
        Row("Roll", "%.1f\u00B0".format(state.headPose.roll))
        Row("Drift", state.headDrift.level.name)
    }
}

@Composable
private fun Row(label: String, value: String) {
    androidx.compose.foundation.layout.Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = Color(0xFF888888), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
        Text(value, color = Color(0xFFCCCCCC), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
    }
}
