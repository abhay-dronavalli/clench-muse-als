package com.clench.eyetrack.camera

import android.content.Context
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.framework.image.MPImage
import java.util.concurrent.Executors

/**
 * Sets up CameraX with a preview and an analysis stream that converts each
 * frame to an [MPImage] for MediaPipe.
 *
 * Call [start] with a [PreviewView] and a per-frame callback.
 */
class CameraManager(private val context: Context) {

    private val analysisExecutor = Executors.newSingleThreadExecutor()

    fun start(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        onFrame: (image: MPImage, timestampMs: Long) -> Unit,
    ) {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            val provider = cameraProviderFuture.get()

            val preview = Preview.Builder().build().also {
                it.surfaceProvider = previewView.surfaceProvider
            }

            val analysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(640, 480))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()

            analysis.setAnalyzer(analysisExecutor) { imageProxy ->
                processFrame(imageProxy, onFrame)
            }

            provider.unbindAll()
            provider.bindToLifecycle(
                lifecycleOwner,
                CameraSelector.DEFAULT_FRONT_CAMERA,
                preview,
                analysis,
            )
        }, ContextCompat.getMainExecutor(context))
    }

    private fun processFrame(
        imageProxy: ImageProxy,
        onFrame: (MPImage, Long) -> Unit,
    ) {
        val bitmap = imageProxy.toBitmap()
        val mpImage = BitmapImageBuilder(bitmap).build()
        val timestampMs = imageProxy.imageInfo.timestamp / 1_000 // microseconds -> ms
        onFrame(mpImage, timestampMs)
        imageProxy.close()
    }

    fun shutdown() {
        analysisExecutor.shutdown()
    }
}
