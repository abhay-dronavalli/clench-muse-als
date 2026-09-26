package com.clench.eyetrack.ui.screen

import androidx.camera.view.PreviewView
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.clench.eyetrack.EyeTrackViewModel
import com.clench.eyetrack.camera.CameraManager
import com.clench.eyetrack.model.CalibrationStep
import com.clench.eyetrack.model.DriftLevel
import com.clench.eyetrack.ui.component.DebugPanel
import com.clench.eyetrack.ui.component.GazeDot
import com.clench.eyetrack.ui.component.HeadGuideOverlay

@Composable
fun TrackingScreen(viewModel: EyeTrackViewModel) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    val gazeState by viewModel.gazeState.collectAsState()
    val calStep by viewModel.calibrationStep.collectAsState()
    val calResult by viewModel.calibrationResult.collectAsState()
    val calAccuracy by viewModel.calibrationAccuracy.collectAsState()
    val collecting by viewModel.collecting.collectAsState()
    val collectionProgress by viewModel.collectionProgress.collectAsState()
    val highlightedTile by viewModel.highlightedTile.collectAsState()
    val fps by viewModel.fps.collectAsState()

    val cameraManager = remember { CameraManager(context) }
    DisposableEffect(Unit) { onDispose { cameraManager.shutdown() } }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF0A0A0A))
            .onGloballyPositioned { coords ->
                viewModel.screenWidth = coords.size.width.toFloat()
                viewModel.screenHeight = coords.size.height.toFloat()
            },
    ) {
        // Layer 1: Tile grid with gaze highlighting
        TileGrid(highlightedTile = highlightedTile)

        // Layer 2: Gaze dot (smooth)
        gazeState?.screen?.let { screen ->
            GazeDot(x = screen.x, y = screen.y)
        }

        // Layer 3: Head drift banner
        val drift = gazeState?.headDrift
        if (drift != null && drift.level != DriftLevel.ALIGNED) {
            val bannerColor = if (drift.level == DriftLevel.WARNING) Color(0xE6EF4444) else Color(0xCCFACC15)
            val hints = buildList {
                if (kotlin.math.abs(drift.deltaYaw) > 4f) add(if (drift.deltaYaw > 0) "right" else "left")
                if (kotlin.math.abs(drift.deltaPitch) > 4f) add(if (drift.deltaPitch > 0) "up" else "down")
                if (kotlin.math.abs(drift.deltaRoll) > 3f) add(if (drift.deltaRoll > 0) "untilt right" else "untilt left")
            }
            val prefix = if (drift.level == DriftLevel.WARNING) "Move head: " else "Slight drift: "
            Box(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = 8.dp)
                    .background(bannerColor, RoundedCornerShape(10.dp))
                    .padding(horizontal = 24.dp, vertical = 10.dp),
            ) {
                Text(prefix + hints.joinToString(", "), color = Color.White, fontSize = 14.sp)
            }
        }

        // Layer 4: Camera preview + head guide
        Box(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(16.dp)
                .size(280.dp, 210.dp),
        ) {
            AndroidView(
                factory = { ctx ->
                    PreviewView(ctx).also { previewView ->
                        cameraManager.start(lifecycleOwner, previewView) { image, ts ->
                            viewModel.onFrameFull(image, ts)
                        }
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )
            calResult?.let { cal ->
                HeadGuideOverlay(
                    calibration = cal,
                    currentFace = null,
                    drift = gazeState?.headDrift ?: com.clench.eyetrack.model.HeadDrift.NONE,
                )
            }
        }

        // Layer 5: Debug panel
        DebugPanel(
            state = gazeState,
            fps = fps,
            accuracy = calAccuracy,
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(16.dp),
        )

        // Status bar + calibrate button
        Row(
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(8.dp)
                .background(Color(0xD90A0A0A), RoundedCornerShape(8.dp))
                .padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val statusText = when {
                calResult != null -> {
                    val accStr = calAccuracy?.let { "  •  Error: %.0fpx (%.1f%%)".format(it.meanErrorPx, it.meanErrorPct) } ?: ""
                    "Tracking$accStr"
                }
                calStep == CalibrationStep.IN_PROGRESS -> "Calibrating..."
                else -> "Tap Calibrate to start"
            }
            val statusColor = when {
                calResult != null -> Color(0xFF22C55E)
                calStep == CalibrationStep.IN_PROGRESS -> Color(0xFFFACC15)
                else -> Color(0xFF3B82F6)
            }
            Text(statusText, color = statusColor, fontSize = 13.sp)

            Button(
                onClick = { viewModel.startCalibration() },
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF3B82F6)),
            ) {
                Text(if (calResult != null) "Recalibrate" else "Calibrate")
            }
        }

        // Layer 6: Calibration overlay
        if (calStep == CalibrationStep.IN_PROGRESS) {
            CalibrationOverlay(
                manager = viewModel.calibrationManager,
                collecting = collecting,
                collectionProgress = collectionProgress,
                onDotTap = { viewModel.startCollectingDot() },
            )
        }
    }
}

@Composable
private fun TileGrid(highlightedTile: Int) {
    val tiles = listOf("I need", "People", "How I feel", "Room", "Suggested", "Other...")
    val tileColors = listOf(
        Color(0xFF1E3A5F), Color(0xFF2D1B4E), Color(0xFF1B4332),
        Color(0xFF3D2C1E), Color(0xFF1A1A3E), Color(0xFF2C2C2C),
    )

    Column(Modifier.fillMaxSize().padding(4.dp)) {
        for (row in 0..1) {
            Row(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                for (col in 0..2) {
                    val idx = row * 3 + col
                    val isHighlighted = idx == highlightedTile

                    val bgColor by animateColorAsState(
                        targetValue = if (isHighlighted) tileColors[idx].copy(alpha = 0.9f) else tileColors[idx].copy(alpha = 0.5f),
                        animationSpec = spring(stiffness = 300f),
                        label = "tileBg$idx",
                    )
                    val borderColor by animateColorAsState(
                        targetValue = if (isHighlighted) Color(0xFF3B82F6) else Color.Transparent,
                        animationSpec = spring(stiffness = 300f),
                        label = "tileBorder$idx",
                    )

                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .padding(2.dp)
                            .background(bgColor, RoundedCornerShape(16.dp))
                            .border(3.dp, borderColor, RoundedCornerShape(16.dp)),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            tiles[idx],
                            color = if (isHighlighted) Color.White else Color(0xFFAAAAAA),
                            fontSize = if (isHighlighted) 26.sp else 22.sp,
                        )
                    }
                }
            }
            if (row == 0) Spacer(Modifier.height(4.dp))
        }
    }
}
