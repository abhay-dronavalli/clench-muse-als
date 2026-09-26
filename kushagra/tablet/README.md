# Clench Eye Track — Android Tablet

Standalone eye tracking app using MediaPipe FaceLandmarker. All computation
runs on-device, no server needed.

## Setup

1. Download the MediaPipe face landmarker model:

   ```
   curl -L -o app/src/main/assets/face_landmarker.task \
     https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
   ```

2. Open `kushagra/tablet/` in Android Studio.

3. Build and run on a tablet (API 26+).

## Architecture

```
com.clench.eyetrack/
  model/          Data classes (GazeState, HeadPose, CalibrationData)
  tracking/       MediaPipe wrapper, iris gaze estimator, head pose,
                  Savitzky-Golay filter, gaze-to-screen mapper
  calibration/    9-point calibration manager, linear regression,
                  head pose drift guard
  camera/         CameraX setup and frame dispatch
  ui/             Jetpack Compose screens and components
```

## How it works

1. CameraX feeds front camera frames to MediaPipe FaceLandmarker
2. 478-landmark face mesh gives iris center positions (landmarks 468, 473)
3. Iris position relative to eye corners = gaze ratio (0..1)
4. Savitzky-Golay filter (window=15, poly=3) smooths the ratios
5. 9-point calibration fits a linear regression: gaze ratios -> screen coords
6. Head pose (yaw/pitch/roll) stored during calibration; drift guide shown
   when the head moves too far from the calibrated position
