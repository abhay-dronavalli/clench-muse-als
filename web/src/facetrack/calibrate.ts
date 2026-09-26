// Head-range calibration math (PRD A3.3a "Calibrate center and edges", A7 head_range). The overlay
// shows a dot at the center, then the left, right, top and bottom edges, about 1.5 s each, and keeps
// the head angles seen after the head has settled. The range is the median of each step. Pure, unit
// tested.

import type { HeadRange } from '../contracts'
import type { Angles } from './pose'

export const STEPS = ['center', 'left', 'right', 'up', 'down'] as const
export type Step = (typeof STEPS)[number]

/** Where each step's dot is, as fractions of the window. */
export const DOT: Record<Step, { x: number; y: number }> = {
  center: { x: 0.5, y: 0.5 },
  left: { x: 0.06, y: 0.5 },
  right: { x: 0.94, y: 0.5 },
  up: { x: 0.5, y: 0.08 },
  down: { x: 0.5, y: 0.92 },
}

export const STEP_MS = 1500
/** Samples in the first part of a step are skipped: the head is still moving to the dot. */
export const SETTLE_MS = 500
/** Fewer steady samples than this in a step means the face was not seen well enough. */
export const MIN_SAMPLES = 5
/** Same as MIN_HEAD_SPAN_DEG in core/contracts.py: an edge closer than this to the center failed. */
export const MIN_SPAN_DEG = 2

export function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b)
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

export type Samples = Record<Step, Angles[]>

export function emptySamples(): Samples {
  return { center: [], left: [], right: [], up: [], down: [] }
}

/** Which calibration check failed, so the overlay can say it in the board's language. */
export type CalibrationProblem = { kind: 'no_face'; step: Step } | { kind: 'too_small'; axis: 'yaw' | 'pitch' }

export type CalibrationResult = { ok: true; range: HeadRange } | { ok: false; problem: CalibrationProblem }

/**
 * The head range from the samples of every step, or what went wrong: a step where the face was
 * barely seen, or a side that is not clearly apart from the center (the person did not turn, or
 * turned the wrong way). The same rule the Core checks before saving.
 */
export function rangeFromSamples(samples: Samples): CalibrationResult {
  for (const step of STEPS) {
    if (samples[step].length < MIN_SAMPLES) return { ok: false, problem: { kind: 'no_face', step } }
  }
  const yaw = (step: Step) => median(samples[step].map((a) => a.yaw))
  const pitch = (step: Step) => median(samples[step].map((a) => a.pitch))
  const range: HeadRange = {
    center_yaw: yaw('center'),
    center_pitch: pitch('center'),
    left_yaw: yaw('left'),
    right_yaw: yaw('right'),
    up_pitch: pitch('up'),
    down_pitch: pitch('down'),
  }
  if (!aroundCenter(range.left_yaw, range.center_yaw, range.right_yaw))
    return { ok: false, problem: { kind: 'too_small', axis: 'yaw' } }
  if (!aroundCenter(range.up_pitch, range.center_pitch, range.down_pitch))
    return { ok: false, problem: { kind: 'too_small', axis: 'pitch' } }
  return { ok: true, range }
}

/** `a` and `b` on opposite sides of `mid`, each at least MIN_SPAN_DEG away. */
export function aroundCenter(a: number, mid: number, b: number): boolean {
  return (a - mid) * (b - mid) < 0 && Math.min(Math.abs(a - mid), Math.abs(b - mid)) >= MIN_SPAN_DEG
}
