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

export interface ClenchNativeBridge {
  /** the shell's eye tracker is running (or starting) and owns the front camera */
  gazeActive(): boolean
  /**
   * The page's pointing mode, or "off" (not started, or a page that does not point). The shell runs
   * its tracker only in camera modes (auto, webcam, gaze) and claims the camera before returning.
   */
  setPointingMode(mode: string): void
  /** open the shell's calibration screen for this person; it saves the result under that name */
  calibrate(person: string): void
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
}

export type NativeEvent =
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

/** The shell draws the trip screen's 3D car behind the page (the page must be see-through there). */
export function nativeCarAvailable(): boolean {
  return typeof nativeBridge()?.carScene === 'function'
}

/** Show or hide the shell's 3D car. No-op in a normal browser. */
export function showNativeCar(on: boolean): void {
  try {
    nativeBridge()?.carScene?.(on)
  } catch (e) {
    console.warn('tablet shell: carScene failed', e)
  }
}

/** Play a trip control's effect on the shell's 3D car. No-op in a normal browser. */
export function playNativeCarEffect(action: string, ms: number, window: string | null = null): void {
  try {
    nativeBridge()?.carEffect?.(action, ms, window ?? '')
  } catch (e) {
    console.warn('tablet shell: carEffect failed', e)
  }
}

/** Tell the shell's 3D scene how fast the car goes. No-op in a normal browser. */
export function setNativeCarSpeed(mph: number): void {
  try {
    nativeBridge()?.carSpeed?.(mph)
  } catch (e) {
    console.warn('tablet shell: carSpeed failed', e)
  }
}

/** Tell the shell's 3D scene the trip layout (the car moves into the right half for "split"). */
export function setNativeCarLayout(mode: string): void {
  try {
    nativeBridge()?.carLayout?.(mode)
  } catch (e) {
    console.warn('tablet shell: carLayout failed', e)
  }
}
