// From a stream of screen points to "which tile is looked at": the one pipeline the board
// (usePointing) and the gaze test page (/gaze-test) share, so what the test measures is what the
// board does. Pure, unit tested.
//
//   point -> One Euro filter (optional) -> chooseTile (sticky edges) -> hold (the new tile must stay
//   the candidate for `holdMs` before the highlight moves) -> dwell (optional: the same tile held for
//   `dwellMs` fires one pick)
//
// The head keeps its old behavior (no filter here, it is smoothed in tracker.ts; no hold). Gaze uses
// the filter and a 300 ms hold by default: eyes flick to a tile and back far more than a head does.

import { OneEuroFilter2D, ONE_EURO_DEFAULTS, type OneEuroParams } from './oneEuro'
import type { ScreenPoint } from './pose'
import { chooseTile, STICKY_MARGIN, type Rect } from './tiles'

export interface PointerTuning {
  oneEuro: boolean
  euro: OneEuroParams
  /** ms a new tile must stay the candidate before the highlight moves there (0 = at once) */
  holdMs: number
  /** share of a tile's size the point must be inside it (tile_switch_margin) */
  margin: number
}

export const HEAD_TUNING: PointerTuning = { oneEuro: false, euro: ONE_EURO_DEFAULTS, holdMs: 0, margin: STICKY_MARGIN }
export const GAZE_HOLD_MS = 300
export const GAZE_TUNING: PointerTuning = { oneEuro: true, euro: ONE_EURO_DEFAULTS, holdMs: GAZE_HOLD_MS, margin: STICKY_MARGIN }

export interface PointerStep {
  /** the point after the filter */
  point: ScreenPoint
  /** the tile the point is on right now (sticky edges, before the hold) */
  candidate: number | null
  /** the highlighted tile (after the hold) */
  tile: number | null
  /** the highlight moved on this step */
  changed: boolean
}

export class TilePointer {
  tuning: PointerTuning
  /** the highlighted tile */
  tile: number | null = null
  private pending: number | null = null
  private pendingSince = 0
  private filter = new OneEuroFilter2D()

  constructor(tuning: PointerTuning) {
    this.tuning = tuning
  }

  /** New tiles (a new SCREEN): start from `tile` (the Core's highlight), keep the filter. */
  reset(tile: number | null): void {
    this.tile = tile
    this.pending = null
  }

  /** The person is not seen: the next point starts the filter over and any pending move is dropped. */
  lost(): void {
    this.filter.reset()
    this.pending = null
  }

  update(raw: ScreenPoint, t: number, rects: Rect[]): PointerStep {
    const { oneEuro, euro, holdMs, margin } = this.tuning
    this.filter.params = euro
    const point = oneEuro ? this.filter.filter(raw, t) : raw
    const candidate = chooseTile(point, rects, this.tile, margin)
    const before = this.tile
    if (candidate === this.tile || candidate === null) {
      this.pending = null
    } else if (this.tile === null || holdMs <= 0) {
      this.tile = candidate
      this.pending = null
    } else if (this.pending !== candidate) {
      this.pending = candidate
      this.pendingSince = t
    } else if (t - this.pendingSince >= holdMs) {
      this.tile = candidate
      this.pending = null
    }
    return { point, candidate, tile: this.tile, changed: this.tile !== before }
  }
}

export const DWELL_MS = 1500

/**
 * Dwell select: the highlighted tile held for `dwellMs` fires one pick. After it fires it waits for
 * the highlight to move (or reset(), for a new screen) before it can fire again, so a person who
 * keeps looking at the same spot does not pick twice.
 */
export class DwellTimer {
  dwellMs: number
  private tile: number | null = null
  private since = 0
  private fired = false

  constructor(dwellMs = DWELL_MS) {
    this.dwellMs = dwellMs
  }

  reset(): void {
    this.tile = null
    this.fired = false
  }

  /**
   * One step: the highlighted tile at `t` (null = nothing, or dwell not allowed right now).
   * Returns the progress 0..1 for the ring and whether to pick now.
   */
  update(tile: number | null, t: number): { progress: number; fire: boolean } {
    if (tile === null) {
      this.reset()
      return { progress: 0, fire: false }
    }
    if (tile !== this.tile) {
      this.tile = tile
      this.since = t
      this.fired = false
    }
    if (this.fired) return { progress: 0, fire: false }
    const progress = Math.min(1, (t - this.since) / this.dwellMs)
    if (progress >= 1) {
      this.fired = true
      return { progress: 1, fire: true }
    }
    return { progress, fire: false }
  }
}
