// The Android tablet shell (kushagra/tablet, BoardActivity) around this page: a native eye tracker
// (Eyedid) that owns the front camera and feeds the gaze slot. docs/eye-tracking.md, "Native shell".
// It also speaks with Android's text-to-speech (WebView has no speechSynthesis; board/nativeSpeech.ts)
// and can run the Muse Sensor Service itself (sensor/service.ts).
//
//   shell -> page   window.clenchGaze.feed({ x, y, found, confidence, state })   about 30 times a second
//                   window.clenchNativeEvent({ type: 'blink' | 'tracker' | 'calibration', ... })
//   page -> shell   window.ClenchNative.*  (an Android JavascriptInterface, present before the page loads)
//
// Only one owner of the camera: while the shell's tracker is active the page never opens it for
// MediaPipe (usePointing's head camera is off, Auto uses the gaze or scans).

import { browserCar } from '../carscene/carHub'
import { webglAvailable } from '../carscene/CarScene'

export interface ClenchNativeBridge {
  /** the shell's eye tracker is running (or starting) and owns the front camera */
  gazeActive(): boolean
  /** Ready to calibrate (active alone also includes SDK initialization). */
  gazeReady?(): boolean
  /**
   * The page's pointing mode, or "off" (not started, or a page that does not point). The shell runs
   * its tracker only in camera modes (auto, webcam, gaze) and claims the camera before returning.
   */
  setPointingMode(mode: string): void
  /** open the shell's calibration screen for this person; it saves the result under that name */
  calibrate(person: string): void
  /** the page's onboarding is running: the shell skips its own startup calibration prompt */
  setOnboarding?(on: boolean): void
  /** Setup-only parked car, camera orbit controlled horizontally by the native gaze. */
  carPreview?(on: boolean): void
  /** Stop calibration and remove its targets (including when setup times out). */
  cancelCalibration?(): void
  /** whose calibration is loaded ("" = none) */
  person(): string
  /** the SDK's own gaze filter (on by default) */
  gazeFilter(): boolean
  setGazeFilter(on: boolean): void

  // Optional: older shells do not have these.

  /** Say `text` with Android's voice (volume 0..1); a 'speech' event follows. False = cannot now. */
  speak?(id: string, text: string, lang: string, volume: number): boolean
  /** stop whatever the Android voice is saying (no 'speech' event is needed for it) */
  stopSpeaking?(): void
  /** the shell has its own Muse sensor (the build names a calibration profile) */
  museAvailable?(): boolean
  /** the shell's sensor as the Core's /api/sensor status (JSON) */
  museStatus?(): string
  /** start or stop the shell's sensor; both answer with museStatus() */
  museConnect?(): string
  museDisconnect?(): string
  /** show (true) or hide the 3D car the shell draws behind this page (trip screen) */
  carScene?(on: boolean): void
  /** play a trip control's effect on the 3D car for `ms` (CAR_ACTION); `window` "" when none */
  carEffect?(action: string, ms: number, window: string): void
  /** the car's speed (CAR_STATE): the 3D scene drives at it, 0 = stopped */
  carSpeed?(mph: number): void
  /** the trip screen's layout ("car", "split", "map"): the 3D car recentres in the right half for split */
  carLayout?(mode: string): void
  /** Reset app: forget the saved eye calibration and the shell's own settings (lib/resetApp.ts) */
  resetApp?(): void
}

export type NativeEvent =
  | { type: 'calibration_progress'; progress: number }
  | { type: 'blink'; t: number; left: boolean; right: boolean }
  | { type: 'tracker'; state: 'starting' | 'on' | 'off' | 'error'; detail?: string }
  | {
      type: 'calibration'
      state: 'started' | 'finished' | 'canceled' | 'loaded' | 'validation_passed' | 'validation_failed'
      person: string
    }
  | { type: 'speech'; id: string; state: 'done' | 'error'; detail?: string }

declare global {
  interface Window {
    ClenchNative?: ClenchNativeBridge
    clenchNativeEvent?: (e: NativeEvent) => void
  }
}

/** The bridge, or null in a normal browser. */
export function nativeBridge(): ClenchNativeBridge | null {
  return typeof window !== 'undefined' && window.ClenchNative ? window.ClenchNative : null
}

/**
 * Tell the shell which pointing mode is on ("off" = none). Call it BEFORE rendering with the new
 * mode: in a camera mode the shell claims the camera synchronously, so the render that follows sees
 * nativeGazeActive() true and does not open the camera for the head. No-op in a normal browser.
 */
export function reportPointingMode(mode: string): void {
  try {
    nativeBridge()?.setPointingMode(mode)
  } catch (e) {
    console.warn('tablet shell: setPointingMode failed', e)
  }
}

/** The shell's eye tracker owns the camera: the page must not open it. */
export function nativeGazeActive(): boolean {
  try {
    return nativeBridge()?.gazeActive() === true
  } catch {
    return false
  }
}

const listeners = new Set<(e: NativeEvent) => void>()

export const nativeEvents = {
  subscribe(fn: (e: NativeEvent) => void) {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  },
}

/**
 * For useSyncExternalStore(subscribeNativeGaze, nativeGazeActive): re-check who owns the camera
 * whenever the shell reports its tracker starting, stopping or failing.
 */
export function subscribeNativeGaze(fn: () => void) {
  return nativeEvents.subscribe((e) => {
    if (e.type === 'tracker') fn()
  })
}

if (typeof window !== 'undefined') {
  window.clenchNativeEvent = (e: NativeEvent) => listeners.forEach((fn) => fn(e))
}

// The 3D car is drawn by the tablet shell (SceneView, behind the page) when the shell has it, and by the
// browser (web/src/carscene, three.js) otherwise. The page's calls below go to whichever draws it.

/** The shell itself draws the 3D car (older shells without carScene leave it to the browser). */
function shellDrawsCar(): boolean {
  return typeof nativeBridge()?.carScene === 'function'
}

let browserCanDraw: boolean | null = null

/**
 * The 3D car is drawn here: by the tablet shell, or by this browser (WebGL). The page must be
 * see-through there (the car is behind it); without it the page shows its own backdrop.
 */
export function carSceneAvailable(): boolean {
  if (shellDrawsCar()) return true
  if (browserCanDraw === null) browserCanDraw = webglAvailable()
  return browserCanDraw
}

/** Show or hide the 3D car. */
export function showNativeCar(on: boolean): void {
  if (!shellDrawsCar()) return browserCar.show(on)
  try {
    nativeBridge()?.carScene?.(on)
  } catch (e) {
    console.warn('tablet shell: carScene failed', e)
  }
}

/** Play a trip control's effect on the 3D car. */
export function playNativeCarEffect(action: string, ms: number, window: string | null = null): void {
  if (!shellDrawsCar()) return browserCar.play(action, ms, window ?? '')
  try {
    nativeBridge()?.carEffect?.(action, ms, window ?? '')
  } catch (e) {
    console.warn('tablet shell: carEffect failed', e)
  }
}

/** Tell the 3D scene how fast the car goes (0 = stopped). */
export function setNativeCarSpeed(mph: number): void {
  if (!shellDrawsCar()) return browserCar.setSpeed(mph)
  try {
    nativeBridge()?.carSpeed?.(mph)
  } catch (e) {
    console.warn('tablet shell: carSpeed failed', e)
  }
}

/** Tell the 3D scene the trip layout (the car moves into the right half for "split"). */
export function setNativeCarLayout(mode: string): void {
  if (!shellDrawsCar()) return browserCar.setLayout(mode)
  try {
    nativeBridge()?.carLayout?.(mode)
  } catch (e) {
    console.warn('tablet shell: carLayout failed', e)
  }
}

/** The car setup's preview: the car alone on a platform, revolving (no-op without a 3D car). */
export function setNativeCarPreview(on: boolean): void {
  if (!shellDrawsCar()) return browserCar.setPreview(on)
  try {
    nativeBridge()?.carPreview?.(on)
  } catch (e) {
    console.warn('tablet shell: carPreview failed', e)
  }
}
