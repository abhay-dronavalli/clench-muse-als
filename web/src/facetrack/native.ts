// The Android tablet shell (kushagra/tablet, BoardActivity) around this page: a native eye tracker
// (Eyedid) that owns the front camera and feeds the gaze slot. docs/eye-tracking.md, "Native shell".
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
  /** open the shell's calibration screen for this person; it saves the result under that name */
  calibrate(person: string): void
  /** whose calibration is loaded ("" = none) */
  person(): string
  /** the SDK's own gaze filter (on by default) */
  gazeFilter(): boolean
  setGazeFilter(on: boolean): void
}

export type NativeEvent =
  | { type: 'blink'; t: number; left: boolean; right: boolean }
  | { type: 'tracker'; state: 'starting' | 'on' | 'off' | 'error'; detail?: string }
  | {
      type: 'calibration'
      state: 'started' | 'finished' | 'canceled' | 'loaded' | 'validation_passed' | 'validation_failed'
      person: string
    }

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
