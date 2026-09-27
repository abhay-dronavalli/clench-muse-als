// Eyedid web: VisualCamp's browser eye tracker (npm `seeso`, Eyedid's name before the rename) on the
// laptop's webcam, feeding the gaze slot (gaze.ts) exactly as the tablet shell does (native.ts).
// docs/eye-tracking.md, "Eyedid web (laptop)".
//
//   runs     only in Auto and Gaze mode, after "Click to start", with a key (VITE_EYEDID_WEB_KEY), in
//            a normal browser (the tablet shell has its own Eyedid), on a cross-origin isolated page
//   camera   while it starts or runs it owns the webcam: the board's head tracker stays off
//            (gazeOwnsCamera in cameraOwner.ts), as with the tablet's tracker
//   network  the SDK checks the key online and downloads its engine (~14 MB, cached) from
//            cdn.seeso.io at start. Without either, gaze is unavailable and Auto points with the head.
//   calib.   five points (EyeCalibrationOverlay), saved in this browser per person
//
// The SDK itself is imported only when it is about to run, so the board never loads it otherwise.

import type { PointingMode } from '../contracts'
import { gaze, type GazeInput } from './gaze'
import { nativeBridge } from './native'

export type EyedidState = 'off' | 'starting' | 'on' | 'error'

/** The browser key from the SeeSo / Eyedid console. Vite only exposes VITE_* names to the page. */
export const EYEDID_WEB_KEY: string = (import.meta.env.VITE_EYEDID_WEB_KEY as string | undefined)?.trim() ?? ''

// The SDK's TrackingState numbers (seeso 0.2.4): SUCCESS 0, LOW_CONFIDENCE 1, UNSUPPORTED 2, FACE_MISSING 3.
const TRACKING_STATE = ['SUCCESS', 'LOW_CONFIDENCE', 'UNSUPPORTED', 'FACE_MISSING'] as const

/** What one SDK gaze sample means for the gaze slot. x, y are page pixels from the top left. */
export function toGazeInput(g: { x: number; y: number; trackingState: number }, width: number, height: number): GazeInput {
  const state = TRACKING_STATE[g.trackingState] ?? `STATE_${g.trackingState}`
  const usable = (g.trackingState === 0 || g.trackingState === 1) && Number.isFinite(g.x) && Number.isFinite(g.y)
  return {
    x: usable && width > 0 ? g.x / width : 0.5,
    y: usable && height > 0 ? g.y / height : 0.5,
    found: usable,
    // LOW_CONFIDENCE sits exactly at the slot's threshold (0.5): still used, but Auto's head
    // fallback wins the moment it drops further.
    confidence: g.trackingState === 0 ? 1 : g.trackingState === 1 ? 0.5 : 0,
    state,
  }
}

/** Why Eyedid web would not run here, or null when it should (for this mode). */
export function whyNotRunning(
  mode: PointingMode | 'off' | null,
  env: { key: string; nativeShell: boolean; isolated: boolean },
): string | null {
  if (mode !== 'auto' && mode !== 'gaze') return 'not a gaze mode'
  if (env.nativeShell) return 'the tablet shell tracks the eyes'
  if (!env.key) return 'no key (VITE_EYEDID_WEB_KEY)'
  if (!env.isolated) return 'the page is not cross-origin isolated (restart the web dev server)'
  return null
}

const CALIBRATION_KEY = 'clench.eyedidWeb.calibration'

function loadCalibration(): string | null {
  try {
    return localStorage.getItem(CALIBRATION_KEY)
  } catch {
    return null
  }
}

function saveCalibration(data: string) {
  try {
    localStorage.setItem(CALIBRATION_KEY, data)
  } catch (e) {
    console.warn('Eyedid web: calibration not saved', e)
  }
}

/** The parts of the SDK used here (seeso 0.2.4 ships no types). */
interface SeesoSdk {
  initialize(key: string, options?: object): Promise<number>
  startTracking(stream: MediaStream): boolean
  stopTracking(): void
  addGazeCallback(fn: (g: { x: number; y: number; trackingState: number }) => void): void
  removeGazeCallback(fn: unknown): void
  startCalibration(points: number, criteria: number): boolean
  stopCalibration(): boolean
  startCollectSamples(): void
  addCalibrationNextPointCallback(fn: (x: number, y: number) => void): void
  removeCalibrationNextPointCallback(fn: unknown): void
  addCalibrationProgressCallback(fn: (progress: number) => void): void
  removeCalibrationProgressCallback(fn: unknown): void
  addCalibrationFinishCallback(fn: (data: string) => void): void
  removeCalibrationFinishCallback(fn: unknown): void
  setCalibrationData(data: string): Promise<void>
}

export interface CalibrationUi {
  /** show the next target at these page pixels; call `ready()` once it is drawn */
  point(x: number, y: number, ready: () => void): void
  progress(p: number): void
  done(ok: boolean): void
}

class EyedidWeb {
  state: EyedidState = 'off'
  detail = ''
  calibrated = loadCalibration() !== null
  private sdk: SeesoSdk | null = null
  private stream: MediaStream | null = null
  private wanted = false
  private listeners = new Set<() => void>()
  private calibrating: CalibrationUi | null = null
  private unhook: (() => void) | null = null  // removes the calibration callbacks from the SDK

  subscribe = (fn: () => void) => {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  /** STARTING or ON: the page must not open the camera for the head. */
  active = (): boolean => this.state === 'starting' || this.state === 'on'

  /** A snapshot for useSyncExternalStore. */
  snapshot = (): string => `${this.state}|${this.detail}|${this.calibrated}`

  private set(state: EyedidState, detail = '') {
    this.state = state
    this.detail = detail
    this.listeners.forEach((fn) => fn())
  }

  /**
   * The board's pointing mode ("off" before "Click to start" and when leaving). Call it BEFORE the
   * render that decides whether the head opens the camera: a gaze mode claims the camera at once.
   */
  setMode = (mode: PointingMode | 'off' | null): void => {
    const why = whyNotRunning(mode, {
      key: EYEDID_WEB_KEY,
      nativeShell: nativeBridge() !== null,
      isolated: typeof window !== 'undefined' && window.crossOriginIsolated === true,
    })
    const want = why === null
    if (want === this.wanted) return
    this.wanted = want
    if (want) {
      this.set('starting')
      void this.start()
    } else {
      this.stop(why)
    }
  }

  private onGaze = (g: { x: number; y: number; trackingState: number }) => {
    if (!this.wanted || this.calibrating) return
    gaze.feed(toGazeInput(g, window.innerWidth, window.innerHeight))
  }

  private async start() {
    try {
      if (!this.sdk) {
        const mod = await import('seeso')
        const Seeso = mod.default as unknown as new () => SeesoSdk
        const sdk = new Seeso()
        // No attention, blink or drowsiness signals: gaze only (blinks come from the headband).
        const code = await sdk.initialize(EYEDID_WEB_KEY, new mod.UserStatusOption(false, false, false))
        if (code !== 0) throw new Error(`the key was refused or could not be checked (error ${code})`)
        sdk.addGazeCallback(this.onGaze)
        this.sdk = sdk
      }
      if (!this.wanted) return this.stop('stopped while starting')
      const saved = loadCalibration()
      if (saved) await this.sdk.setCalibrationData(saved)
      this.stream = await navigator.mediaDevices.getUserMedia({ video: true })
      if (!this.wanted) return this.stop('stopped while starting')
      if (!this.sdk.startTracking(this.stream)) throw new Error('tracking did not start')
      this.set('on', saved ? '' : 'not calibrated yet')
    } catch (e) {
      console.warn('Eyedid web: could not start', e)
      this.releaseCamera()
      this.wanted = false
      gaze.clear()
      // The camera is free again: Auto points with the head, Gaze says no eye tracker.
      this.set('error', e instanceof Error ? e.message : String(e))
    }
  }

  private releaseCamera() {
    try {
      this.sdk?.stopTracking()
    } catch {
      // not tracking
    }
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
  }

  private stop(why: string | null) {
    this.cancelCalibration()
    this.releaseCamera()
    gaze.clear()
    this.set('off', why ?? '')
  }

  /** Five targets; the result is kept in this browser and used from the next start on. */
  calibrate = (ui: CalibrationUi): boolean => {
    const sdk = this.sdk
    if (!sdk || this.state !== 'on' || this.calibrating) return false
    const next = (x: number, y: number) => ui.point(x, y, () => sdk.startCollectSamples())
    const progress = (p: number) => ui.progress(p)
    const unhook = () => {
      sdk.removeCalibrationNextPointCallback(next)
      sdk.removeCalibrationProgressCallback(progress)
      sdk.removeCalibrationFinishCallback(finish)
    }
    const finish = (data: string) => {
      unhook()
      this.unhook = null
      this.calibrating = null
      saveCalibration(data)
      this.calibrated = true
      this.set('on', '')
      ui.done(true)
    }
    sdk.addCalibrationNextPointCallback(next)
    sdk.addCalibrationProgressCallback(progress)
    sdk.addCalibrationFinishCallback(finish)
    // 5 points, default accuracy criteria (0). While calibrating the board gets no gaze: it holds still.
    if (!sdk.startCalibration(5, 0)) {
      unhook()
      return false
    }
    this.calibrating = ui
    this.unhook = unhook
    return true
  }

  cancelCalibration = (): void => {
    if (!this.calibrating) return
    const ui = this.calibrating
    this.calibrating = null
    this.unhook?.()
    this.unhook = null
    try {
      this.sdk?.stopCalibration()
    } catch {
      // already stopped
    }
    ui.done(false)
  }
}

/** The one Eyedid web tracker on this page. */
export const eyedidWeb = new EyedidWeb()
