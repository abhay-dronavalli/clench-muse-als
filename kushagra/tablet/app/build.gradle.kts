import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

apply(from = "download_tasks.gradle")

// Local, git-ignored settings (kushagra/tablet/local.properties). The Eyedid key is never committed.
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun local(name: String, default: String): String = localProps.getProperty(name)?.trim().takeUnless { it.isNullOrEmpty() } ?: default
fun quoted(s: String) = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

android {
    namespace = "com.clench.eyetrack"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.clench.eyetrack"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        // Board shell (BoardActivity): the Eyedid key, where the board is served, and the tablet.
        buildConfigField("String", "EYEDID_LICENSE_KEY", quoted(local("EYEDID_LICENSE_KEY", "")))
        buildConfigField("String", "BOARD_URL", quoted(local("BOARD_URL", "http://localhost:5173/")))
        buildConfigField("String", "BOARD_ORIENTATION", quoted(local("BOARD_ORIENTATION", "landscape")))
        // Camera position, used only when the SDK has none for this model. Defaults: Galaxy Tab S9
        // Ultra (SM-X910), 2960 x 1848, front camera centered on the long edge. Estimated, not measured.
        buildConfigField("float", "SCREEN_WIDTH_PX", local("SCREEN_WIDTH_PX", "2960") + "f")
        buildConfigField("float", "SCREEN_HEIGHT_PX", local("SCREEN_HEIGHT_PX", "1848") + "f")
        buildConfigField("float", "CAMERA_ORIGIN_X_MM", local("CAMERA_ORIGIN_X_MM", "-157.2") + "f")
        buildConfigField("float", "CAMERA_ORIGIN_Y_MM", local("CAMERA_ORIGIN_Y_MM", "-1.0") + "f")
        buildConfigField("boolean", "CAMERA_ON_LONGER_AXIS", local("CAMERA_ON_LONGER_AXIS", "true"))

        // Eyedid ships ARM libraries only (no emulator).
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    kotlin {
        jvmToolchain(17)
    }
}

dependencies {
    // Compose
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.lifecycle.runtime.compose)
    debugImplementation(libs.compose.ui.tooling)

    // CameraX
    implementation(libs.camera.core)
    implementation(libs.camera.camera2)
    implementation(libs.camera.lifecycle)
    implementation(libs.camera.view)

    // MediaPipe Face Landmarker
    implementation(libs.mediapipe.vision)

    // Eyedid gaze SDK (owns the front camera while it tracks)
    implementation(libs.eyedid.gazetracker)

    // Coroutines
    implementation(libs.coroutines.android)

    testImplementation(libs.junit)
}
