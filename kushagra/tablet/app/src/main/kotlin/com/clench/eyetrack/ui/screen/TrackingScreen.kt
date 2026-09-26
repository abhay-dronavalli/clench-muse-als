package com.clench.eyetrack.ui.screen

import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLifecycleOwner
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

/**
 * Main tracking screen.
 *
 * Layers (bottom to top):
 * 1. Six test tiles (full screen)
 * 2. Gaze dot
 * 3. Head drift warning banner
 * 4. Camera preview with head guide overlay (bottom-right)
 * 5. Debug panel (bottom-left)
 * 6. Calibration overlay (when active)
 */
@Composable
fun TrackingScreen(viewModel: EyeTrackViewModel) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val density = LocalDensity.current

    val gazeState by viewModel.gazeState.collectAsState()
    val calStep by viewModel.calibrationStep.collectAsState()
    val calResult by viewModel.calibrationResult.collectAsState()

    // Camera manager
    val cameraManager = remember { CameraManager(context) }
    DisposableEffect(Unit) { onDispose { cameraManager.shutdown() } }

    // Track screen size
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF0A0A0A))
            .onGloballyPositioned { coords ->
                viewModel.screenWidth = coords.size.width.toFloat()
                viewModel.screenHeight = coords.size.height.toFloat()
            },
    ) {
        // Layer 1: Test tile grid
        TileGrid()

        // Layer 2: Gaze dot
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
                .size(320.dp, 240.dp),
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

            // Head guide overlay on top of camera preview
            calResult?.let { cal ->
                val currentSnapshot = gazeState?.let { state ->
                    // Build a FacePoseSnapshot from current landmarks
                    // This is approximate — uses the gaze state's head pose to infer position
                    // In a real impl, we'd pass the snapshot through the state flow
                    null // The HeadGuideOverlay handles null gracefully
                }
                HeadGuideOverlay(
                    calibration = cal,
                    currentFace = currentSnapshot,
                    drift = gazeState?.headDrift ?: com.clench.eyetrack.model.HeadDrift.NONE,
                )
            }
        }

        // Layer 5: Debug panel
        DebugPanel(
            state = gazeState,
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(16.dp),
        )

        // Status + calibrate button
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
                calResult != null -> "Tracking (calibrated)"
                calStep == CalibrationStep.IN_PROGRESS -> "Calibrating..."
                else -> "Click Calibrate to start"
            }
            Text(statusText, color = Color(0xFF3B82F6), fontSize = 13.sp)

            Button(onClick = { viewModel.startCalibration() }) {
                Text("Calibrate")
            }
        }

        // Layer 6: Calibration overlay
        if (calStep == CalibrationStep.IN_PROGRESS) {
            CalibrationOverlay(
                manager = viewModel.calibrationManager,
                onDotTap = { viewModel.recordCalibrationPointWithSnapshot() },
            )
        }
    }
}

@Composable
private fun TileGrid() {
    val tiles = listOf("I need", "People", "How I feel", "Room", "Suggested", "Other...")

    Column(Modifier.fillMaxSize().padding(4.dp)) {
        for (row in 0..1) {
            Row(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                for (col in 0..2) {
                    val idx = row * 3 + col
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .padding(2.dp)
                            .background(Color(0xFF1A1A2E), RoundedCornerShape(16.dp)),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(tiles[idx], color = Color(0xFFE5E5E5), fontSize = 22.sp)
                    }
                }
            }
            if (row == 0) Spacer(Modifier.height(4.dp))
        }
    }
}
