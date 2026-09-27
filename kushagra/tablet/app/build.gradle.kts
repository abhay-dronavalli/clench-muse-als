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
// A -PNAME=value on the Gradle command line wins over local.properties.
fun local(name: String, default: String): String =
    (providers.gradleProperty(name).orNull ?: localProps.getProperty(name))?.trim().takeUnless { it.isNullOrEmpty() } ?: default
fun quoted(s: String) = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

// The Muse on the tablet (com.clench.eyetrack.muse): MUSE_PROFILE names a jaw calibration made by the
// Muse bench, read here from the repository's test/calibration.<name>.json (git-ignored, the same file
// `python -m sensor.main --profile <name>` loads). Unset = the tablet leaves the Muse to the laptop.
val museProfile = local("MUSE_PROFILE", "")
val museCalibration: Map<*, *> = if (museProfile.isEmpty()) emptyMap<String, Any>() else {
    require(Regex("[A-Za-z0-9_-]{1,32}").matches(museProfile)) { "MUSE_PROFILE must be letters, digits, - or _" }
    val file = rootProject.file("../../test/calibration.$museProfile.json")
    require(file.isFile) { "MUSE_PROFILE=$museProfile but ${file.canonicalPath} does not exist (calibrate with the Muse bench first)" }
    val json = groovy.json.JsonSlurper().parse(file) as Map<*, *>
    require(json["board"] == "MUSE_2_BOARD" && (json["fs"] as Number).toInt() == 256) { "$file is not a Muse 2 profile at 256 Hz" }
    json
}
fun museNumber(key: String): String =
    (museCalibration[key] as Number?)?.toDouble()?.takeIf { it.isFinite() }?.toString() ?: "Double.NaN"

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

        // The Muse on the tablet (see museProfile above). MUSE_NAME picks one headband ("Muse-1234").
        buildConfigField("String", "MUSE_PROFILE", quoted(museProfile))
        buildConfigField("double", "MUSE_EMG_REST", museNumber("emg_rest"))
        buildConfigField("double", "MUSE_EMG_THRESHOLD", museNumber("emg_threshold"))
        buildConfigField("double", "MUSE_EMG_PEAK", museNumber("emg_peak"))
        buildConfigField("String", "MUSE_NAME", quoted(local("MUSE_NAME", "")))
        buildConfigField("double", "MUSE_MOTION_LIMIT", local("MUSE_MOTION_LIMIT", "30.0"))

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

    // The Muse sensor's WebSocket to the Core
    implementation(libs.okhttp)

    testImplementation(libs.junit)
    testImplementation(libs.json) // org.json is Android's; the JVM unit tests need a real one
}
