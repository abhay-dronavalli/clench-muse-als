// The gaze slot: where an eye tracker plugs into the board's pointing (docs/eye-tracking.md).
//
// An eye tracker running in this page calls gaze.feed({ x, y, found, confidence }) for every frame
// (x, y = where the person looks, as fractions of the window: 0,0 top left, 1,1 bottom right). The
// board then points with it exactly as it does with the head: the tile under the point (sticky
// edges), POINT with source "gaze", FACE_OK from `found`. Nothing here touches a camera.
//
//   Gaze mode   follows the gaze only; no fresh gaze = "face lost" (the highlight stays put).
//   Auto mode   gaze while it is available (fresh, found, confident enough), else the head, else scan.

import type { ScreenPoint } from './pose'
import type { PointSample } from './source'

export interface GazeInput {
  /** 0..1 across the window, 0 = left edge (values outside are clamped) */
  x: number
  /** 0..1 down the window, 0 = top edge */
  y: number
  /** the tracker sees the eyes this frame */
  found: boolean
  /** 0..1, how sure the tracker is about this point */
  confidence: number
  /** the tracker's own state word, shown on /gaze-test only (Eyedid: SUCCESS, GAZE_MISSING, FACE_MISSING) */
  state?: string
}

/** A gaze sample older than this means the eye tracker stopped: gaze is not available. */
export const GAZE_STALE_MS = 500
/** Below this confidence a gaze point is not used (Auto falls back to the head). */
export const GAZE_MIN_CONFIDENCE = 0.5

const clamp01 = (v: number) => Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0.5))

export class GazeFeed {
  private last: PointSample | null = null
  /** the last sample's `state` (debugging) */
  lastState: string | null = null
  private listeners = new Set<(s: PointSample) => void>()
  private readonly now: () => number

  constructor(now: () => number = () => performance.now()) {
    this.now = now
  }

  /** One frame from the eye tracker. Call it as often as the tracker runs (15 to 60 times a second). */
  feed = (input: GazeInput): void => {
    const confidence = clamp01(input.confidence)
    const found = input.found && confidence >= GAZE_MIN_CONFIDENCE
    const point: ScreenPoint | null = input.found ? { x: clamp01(input.x), y: clamp01(input.y) } : null
    const sample: PointSample = { source: 'gaze', t: this.now(), found, point, confidence }
    this.last = sample
    this.lastState = input.state ?? null
    this.listeners.forEach((fn) => fn(sample))
  }

  /** The eye tracker stopped: gaze is unavailable at once (instead of after GAZE_STALE_MS). */
  clear = (): void => {
    this.last = null
  }

  /** Fresh, found and confident enough: Auto uses the gaze instead of the head. */
  available = (at: number = this.now()): boolean =>
    this.last !== null && this.last.found && at - this.last.t <= GAZE_STALE_MS

  /** Samples arrive recently at all (found or not): an eye tracker is plugged in and running. */
  connected = (at: number = this.now()): boolean => this.last !== null && at - this.last.t <= GAZE_STALE_MS

  subscribe = (fn: (s: PointSample) => void) => {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }
}

/** The one gaze slot on this page. */
export const gaze = new GazeFeed()

// For quick tests from the browser console, and for a tracker loaded as a separate script:
//   window.clenchGaze.feed({ x: 0.2, y: 0.3, found: true, confidence: 0.9 })
declare global {
  interface Window {
    clenchGaze?: GazeFeed
  }
}
if (typeof window !== 'undefined') window.clenchGaze = gaze
