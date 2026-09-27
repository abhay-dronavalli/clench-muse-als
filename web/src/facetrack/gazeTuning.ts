// Gaze settings that are not the Core's business: the One Euro filter, the 300 ms hold and dwell
// select. Set on /gaze-test (and dwell in the dev panel), remembered in this browser, used by the
// board. Pure, unit tested.

import { ONE_EURO_DEFAULTS, type OneEuroParams } from './oneEuro'
import { DWELL_MS, GAZE_HOLD_MS, type PointerTuning } from './tilePointer'

export interface GazeTuning {
  oneEuro: boolean
  euro: OneEuroParams
  holdMs: number
  /** dwell select: off by default; never on the confirm screen or the help countdown */
  dwell: boolean
  dwellMs: number
}

export const DEFAULT_GAZE_TUNING: GazeTuning = {
  oneEuro: true,
  euro: ONE_EURO_DEFAULTS,
  holdMs: GAZE_HOLD_MS,
  dwell: false,
  dwellMs: DWELL_MS,
}

export const LIMITS = {
  holdMs: [0, 1000],
  dwellMs: [500, 4000],
  minCutoff: [0.05, 10],
  beta: [0, 100],
  dCutoff: [0.1, 10],
} as const

const num = (v: unknown, [lo, hi]: readonly [number, number], fallback: number) =>
  typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback

/** Saved JSON (or anything) to valid settings; bad or missing fields fall back to the defaults. */
export function parseTuning(raw: string | null): GazeTuning {
  const d = DEFAULT_GAZE_TUNING
  let o: Record<string, unknown> = {}
  try {
    const v: unknown = raw ? JSON.parse(raw) : {}
    if (v && typeof v === 'object') o = v as Record<string, unknown>
  } catch {
    return d
  }
  const e = (o.euro && typeof o.euro === 'object' ? o.euro : {}) as Record<string, unknown>
  return {
    oneEuro: typeof o.oneEuro === 'boolean' ? o.oneEuro : d.oneEuro,
    euro: {
      minCutoff: num(e.minCutoff, LIMITS.minCutoff, d.euro.minCutoff),
      beta: num(e.beta, LIMITS.beta, d.euro.beta),
      dCutoff: num(e.dCutoff, LIMITS.dCutoff, d.euro.dCutoff),
    },
    holdMs: num(o.holdMs, LIMITS.holdMs, d.holdMs),
    dwell: typeof o.dwell === 'boolean' ? o.dwell : d.dwell,
    dwellMs: num(o.dwellMs, LIMITS.dwellMs, d.dwellMs),
  }
}

/** The pointer settings for gaze, with the Core's sticky margin. */
export function gazePointerTuning(t: GazeTuning, margin: number): PointerTuning {
  return { oneEuro: t.oneEuro, euro: t.euro, holdMs: t.holdMs, margin }
}
