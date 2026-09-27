// The 10-target gaze test (/gaze-test): how we judge an eye tracker. Pure, unit tested.
//
// A tile is prompted; it is a hit when the highlight (the same TilePointer the board uses, so with
// its filter and hold) lands on it within 2 s. Between prompts a short pause, with the eyes still on
// the last target. Targets never repeat back to back, and the first is never the tile already
// highlighted, so every prompt needs a real eye movement.
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

/** `n` targets over `tiles` tiles, never the same twice in a row and never `avoid` first. */
export function makeTargets(n: number, tiles: number, avoid: number | null, random: () => number = Math.random): number[] {
  const out: number[] = []
  let prev = avoid
  for (let i = 0; i < n; i++) {
    const choices = [...Array(tiles).keys()].filter((t) => t !== prev)
    const next = choices[Math.min(choices.length - 1, Math.floor(random() * choices.length))]
    out.push(next)
    prev = next
  }
  return out
}

export class TestRun {
  readonly targets: number[]
  readonly trials: Trial[] = []
  phase: Phase
  private i = 0
  private wrong = 0
  private lastTile: number | null = null

  constructor(targets: number[], start: number) {
    this.targets = targets
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
        this.phase = { kind: 'prompt', target: this.targets[this.i], since: t }
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
    this.i++
    this.phase = this.i >= this.targets.length ? { kind: 'done' } : { kind: 'gap', until: t + GAP_MS }
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
