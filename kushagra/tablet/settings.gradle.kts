pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // Eyedid (VisualCamp) gaze SDK: https://docs.eyedid.ai/docs/quick-start/android-quick-start
        maven("https://seeso.jfrog.io/artifactory/visualcamp-eyedid-sdk-android-release")
    }
}

rootProject.name = "ClenchEyeTrack"
include(":app")
