package com.clench.eyetrack.tracking

import android.content.Context
import com.google.mediapipe.framework.image.MPImage
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarker
import com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarkerResult

/**
 * Wraps MediaPipe FaceLandmarker setup and frame processing.
 *
 * Runs in VIDEO mode (synchronous, one result per frame). The caller
 * feeds camera frames via [detect] and gets back the landmark result.
 */
class FaceLandmarkerHelper(context: Context) {

    private val landmarker: FaceLandmarker

    init {
        val baseOptions = BaseOptions.builder()
            .setModelAssetPath("face_landmarker.task")
            .build()

        val options = FaceLandmarker.FaceLandmarkerOptions.builder()
            .setBaseOptions(baseOptions)
            .setRunningMode(RunningMode.VIDEO)
            .setNumFaces(1)
            .setOutputFaceBlendshapes(false)
            .setOutputFacialTransformationMatrixes(false)
            .build()

        landmarker = FaceLandmarker.createFromOptions(context, options)
    }

    /**
     * Run detection on a camera frame.
     *
     * @param image    The camera frame as an MPImage.
     * @param timestampMs  Frame timestamp in milliseconds (must increase).
     * @return The landmark result, or null if no face was found.
     */
    fun detect(image: MPImage, timestampMs: Long): FaceLandmarkerResult? {
        val result = landmarker.detectForVideo(image, timestampMs)
        if (result.faceLandmarks().isEmpty()) return null
        return result
    }

    fun close() {
        landmarker.close()
    }
}
