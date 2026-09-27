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

## Board shell with Eyedid gaze (`board/BoardActivity`)

The "Clench Board" launcher entry shows the patient board in a WebView and points with the eyes
using the Eyedid SDK (VisualCamp). "Clench Eye Track (MediaPipe)" is the prototype above, kept as
the fallback. Page side and bridge: `docs/eye-tracking.md`, "Native shell".

`local.properties` (git-ignored, next to this README):

```
sdk.dir=C\:/Users/<you>/AppData/Local/Android/Sdk
EYEDID_LICENSE_KEY=<dev key from https://manage.eyedid.ai>
# optional
BOARD_URL=http://localhost:5173/        # /gaze-test for the test bench
BOARD_ORIENTATION=landscape             # or portrait (the board goes 2 across x 3 down)
CAMERA_ORIGIN_X_MM=-157.2               # used only if the SDK has no camera position for this model
CAMERA_ORIGIN_Y_MM=-1.0
```

Run (PowerShell, tablet on USB with USB debugging on; the SDK does not run on an emulator):

```powershell
$adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
.\gradlew.bat installDebug                 # from kushagra/tablet
& $adb reverse tcp:5173 tcp:5173           # tablet localhost:5173 -> this laptop's web app
#   (your own dev server on 5174: & $adb reverse tcp:5173 tcp:5174)
& $adb shell am start -n com.clench.eyetrack/.board.BoardActivity
& $adb logcat -s BoardActivity EyedidGaze BoardPage   # tracker state, calibration, page console
```

- First start: allow the camera; the key is checked online (retries for a minute on network errors).
  Then "not calibrated yet": calibrate (five dots, look at each until its ring fills).
- Later starts: the last person's calibration is loaded and checked with one dot; a miss offers to
  recalibrate. Back cancels a calibration or the check.
- `/gaze-test` in the same app has "Calibrate <name>" (saved per name) and the SDK filter switch.
- The page is debuggable from the laptop at `chrome://inspect`.

## The Muse on the tablet (`muse/`)

With `MUSE_PROFILE` set, the "Clench Board" app reads the Muse 2 over Bluetooth itself and sends its
clenches to the Core, in place of the laptop's `python -m sensor.main`. Same detector as `sensor/`
(checked against BrainFlow's numbers), same events on `/ws/sensor`. No double blink yet.

1. Calibrate on the laptop with the Muse bench (`test/clench_detect.py`), which writes
   `test/calibration.<name>.json`. Disconnect the Muse from the laptop afterwards: it pairs with one
   device at a time.
2. In `local.properties` (or `-PMUSE_PROFILE=<name>` on the Gradle command line):

   ```
   MUSE_PROFILE=<name>          # reads ../../test/calibration.<name>.json at build time
   # optional
   MUSE_NAME=Muse-1234          # this headband only; otherwise the first Muse seen
   MUSE_MOTION_LIMIT=30.0       # gyroscope degrees/s above which clenches are blocked
   ```

3. Run `run-tablet.bat`. The board works as before: nothing touches Bluetooth until you ask.
4. Turn the Muse on, open the Muse panel on the tablet (the "Muse ," tab on the left edge) and press
   **Connect headband**. The first time, allow Bluetooth ("Nearby devices"). The panel's output shows
   the search and the connection; Disconnect releases the headband. In a tablet build the panel's
   Connect runs the tablet's sensor, never the laptop's, so the two cannot fight over the Core.
5. Enable Muse input in the same panel once the live signal shows.

`adb logcat -s MuseSensor MuseBle` shows the search, the connection, the clock offset to the Core,
and every gesture sent. Pure logic lives in `EmgFilter`, `ClenchInput`, `MuseWindow`, `SensorLoop`
(unit tested: `.\gradlew.bat testDebugUnitTest`); `MuseBle` and `MuseSensor` are the Android side.
After changing `sensor/detect/clench.py`'s filter, rerun `tools/muse_golden.py` and the tests.
