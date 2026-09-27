// The 10-target gaze test (/gaze-test): how we judge an eye tracker. Pure, unit tested.
//
// A tile is prompted; it is a hit when the highlight (the same TilePointer the board uses, so with
// its filter and hold) lands on it within 2 s. Between prompts a short pause, with the eyes still on
// the last target. Each target is picked when its prompt starts and is never the tile highlighted at
// that moment (after a miss the highlight can rest anywhere, not only on the last target) nor the
// previous target, so every prompt needs a real eye movement and no hit is free.
//
// Success for a person: hit rate >= 90% (docs/eye-tracking.md, "Gaze test").

export const TARGETS = 10
export const TIMEOUT_MS = 2000
export const GAP_MS = 700
export const PASS_RATE = 0.9

export interface Trial {
  target: number
  hit: boolean
  /** prompt to highlight, ms (null for a miss) */
  ms: number | null
  /** times the highlight landed on another tile during this prompt */
  wrong: number
}

export type Phase = { kind: 'gap'; until: number } | { kind: 'prompt'; target: number; since: number } | { kind: 'done' }

/** A random tile out of `tiles` that is none of `avoid` (nulls ignored). */
export function pickTarget(tiles: number, avoid: (number | null)[], random: () => number = Math.random): number {
  const choices = [...Array(tiles).keys()].filter((t) => !avoid.includes(t))
  if (choices.length === 0) return 0 // only with a single tile; never for the 6-tile grid
  return choices[Math.min(choices.length - 1, Math.floor(random() * choices.length))]
}

export class TestRun {
  readonly count: number
  readonly tiles: number
  readonly trials: Trial[] = []
  phase: Phase
  private wrong = 0
  private lastTile: number | null = null
  private previous: number | null = null
  private readonly random: () => number

  constructor(count: number, tiles: number, start: number, random: () => number = Math.random) {
    this.count = count
    this.tiles = tiles
    this.random = random
    this.phase = { kind: 'gap', until: start + GAP_MS }
  }

  get done(): boolean {
    return this.phase.kind === 'done'
  }

  /** The prompted tile right now, or null. */
  get target(): number | null {
    return this.phase.kind === 'prompt' ? this.phase.target : null
  }

  /** One step at `t` (ms) with the highlighted tile (null = not seen). */
  update(t: number, tile: number | null): void {
    const ph = this.phase
    if (ph.kind === 'done') return
    if (ph.kind === 'gap') {
      if (t >= ph.until) {
        const target = pickTarget(this.tiles, [tile, this.previous], this.random)
        this.phase = { kind: 'prompt', target, since: t }
        this.previous = target
        this.wrong = 0
        this.lastTile = tile
      }
      return
    }
    if (tile === ph.target) {
      this.finish({ target: ph.target, hit: true, ms: t - ph.since, wrong: this.wrong }, t)
      return
    }
    if (tile !== null && tile !== this.lastTile) this.wrong++
    this.lastTile = tile
    if (t - ph.since >= TIMEOUT_MS) this.finish({ target: ph.target, hit: false, ms: null, wrong: this.wrong }, t)
  }

  private finish(trial: Trial, t: number) {
    this.trials.push(trial)
    this.phase = this.trials.length >= this.count ? { kind: 'done' } : { kind: 'gap', until: t + GAP_MS }
  }
}

export interface Summary {
  trials: number
  hits: number
  hitRate: number
  /** mean prompt-to-highlight time over the hits, ms (null without hits) */
  avgMs: number | null
  wrong: number
  pass: boolean
}

export function summarize(trials: Trial[]): Summary {
  const hits = trials.filter((t) => t.hit)
  const hitRate = trials.length ? hits.length / trials.length : 0
  const avgMs = hits.length ? hits.reduce((s, t) => s + (t.ms ?? 0), 0) / hits.length : null
  return {
    trials: trials.length,
    hits: hits.length,
    hitRate,
    avgMs,
    wrong: trials.reduce((s, t) => s + t.wrong, 0),
    pass: trials.length > 0 && hitRate >= PASS_RATE,
  }
}
