// Head pose -> a point on the screen (PRD A3.3a "Webcam"). Pure functions, unit tested; MediaPipe
// itself stays out of here.
//
// Angles are in degrees. yaw > 0: the face turns toward the camera image's right, which is the
// person's own left (the camera image is not mirrored). pitch > 0: the face tilts up. The
// calibrated HeadRange says which angles mean the screen's edges, so the sign convention only
// matters for the defaults used before the first calibration.

import type { HeadRange } from '../contracts'

export interface Angles {
  yaw: number
  pitch: number
}

/** A point on the screen as a fraction of the window: x 0 = left edge, y 0 = top edge. */
export interface ScreenPoint {
  x: number
  y: number
}

const DEG = 180 / Math.PI

/**
 * Yaw and pitch from MediaPipe's facial transformation matrix: 4x4, column-major (as three.js
 * `Matrix4.fromArray` reads it). The third column is where the face points in camera space
 * (x right in the image, y up, z toward the camera). Null when the data is not a 4x4 matrix.
 */
export function matrixToAngles(data: ArrayLike<number>): Angles | null {
  if (data.length < 16) return null
  const fx = data[8]
  const fy = data[9]
  const fz = data[10]
  if (![fx, fy, fz].every(Number.isFinite) || Math.hypot(fx, fy, fz) === 0) return null
  return {
    yaw: Math.atan2(fx, fz) * DEG,
    pitch: Math.atan2(fy, Math.hypot(fx, fz)) * DEG,
  }
}

/** Exponential smoothing: `alpha` of the new reading, the rest from before (1 = no smoothing). */
export function smooth(prev: Angles | null, next: Angles, alpha: number): Angles {
  if (prev === null) return next
  return {
    yaw: prev.yaw + alpha * (next.yaw - prev.yaw),
    pitch: prev.pitch + alpha * (next.pitch - prev.pitch),
  }
}

/**
 * Before the first calibration: a laptop camera above the screen, the person facing it a little
 * below the camera, 18 degrees to each side and 12 up or down reach the screen's edges.
 */
export const DEFAULT_RANGE: HeadRange = {
  center_yaw: 0,
  center_pitch: -8,
  left_yaw: 18, // turning to the person's left moves the face toward the image's right: yaw > 0
  right_yaw: -18,
  up_pitch: 4,
  down_pitch: -20,
}

/**
 * Where `value` falls between `low` (-> 0), `mid` (-> 0.5) and `high` (-> 1), each half scaled on its
 * own so an uneven range works; clamped to 0..1. `low` and `high` lie on opposite sides of `mid`.
 */
export function axis(value: number, low: number, mid: number, high: number): number {
  const d = value - mid
  const towardHigh = d * (high - mid) >= 0
  const t = towardHigh ? 0.5 + (0.5 * d) / (high - mid) : 0.5 - (0.5 * d) / (low - mid)
  return Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0.5))
}

/** Calibrated yaw -> x and pitch -> y: the point on the screen the person is facing. */
export function poseToPoint(a: Angles, r: HeadRange): ScreenPoint {
  return {
    x: axis(a.yaw, r.left_yaw, r.center_yaw, r.right_yaw),
    y: axis(a.pitch, r.up_pitch, r.center_pitch, r.down_pitch),
  }
}
