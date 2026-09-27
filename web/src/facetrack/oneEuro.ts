// One Euro filter (Casiez, Roussel, Vogel 2012) for the gaze point: strong smoothing while the eyes
// rest on a tile (low jitter), little lag when they jump to another one (the cutoff rises with
// speed). Pure, unit tested.
//
// Units here: points are fractions of the window (0..1), time in ms. So `beta` is larger than the
// paper's pixel-based examples: a saccade across the board is about 1 to 3 window widths a second.

import type { ScreenPoint } from './pose'

export interface OneEuroParams {
  /** Hz: the cutoff while the point is still. Lower = smoother at rest, more lag. */
  minCutoff: number
  /** How fast the cutoff rises with speed (per window-width a second). Higher = less lag on jumps. */
  beta: number
  /** Hz: the cutoff for the speed estimate itself. */
  dCutoff: number
}

export const ONE_EURO_DEFAULTS: OneEuroParams = { minCutoff: 1.0, beta: 10, dCutoff: 1.0 }

/** A gap longer than this (eyes lost, tracker paused) starts the filter over at the next point. */
export const ONE_EURO_RESET_MS = 250

const alpha = (cutoffHz: number, dtS: number) => {
  const tau = 1 / (2 * Math.PI * cutoffHz)
  return 1 / (1 + tau / dtS)
}

class Axis {
  private x: number | null = null
  private dx = 0

  filter(value: number, dtS: number, p: OneEuroParams): number {
    if (this.x === null || dtS <= 0) {
      this.x = value
      this.dx = 0
      return value
    }
    const rawDx = (value - this.x) / dtS
    this.dx += alpha(p.dCutoff, dtS) * (rawDx - this.dx)
    const cutoff = p.minCutoff + p.beta * Math.abs(this.dx)
    this.x += alpha(cutoff, dtS) * (value - this.x)
    return this.x
  }

  reset() {
    this.x = null
    this.dx = 0
  }
}

export class OneEuroFilter2D {
  params: OneEuroParams
  private ax = new Axis()
  private ay = new Axis()
  private lastT: number | null = null

  constructor(params: OneEuroParams = ONE_EURO_DEFAULTS) {
    this.params = params
  }

  /** One point at time `t` (ms). Returns the smoothed point. */
  filter(p: ScreenPoint, t: number): ScreenPoint {
    if (this.lastT !== null && t - this.lastT > ONE_EURO_RESET_MS) this.reset()
    const dtS = this.lastT === null ? 0 : (t - this.lastT) / 1000
    this.lastT = t
    return { x: this.ax.filter(p.x, dtS, this.params), y: this.ay.filter(p.y, dtS, this.params) }
  }

  reset(): void {
    this.ax.reset()
    this.ay.reset()
    this.lastT = null
  }
}
