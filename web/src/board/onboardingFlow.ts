import type { Signal, TripLayout, ActivePointer } from '../contracts'
import { museFresh } from '../sensor/status'

export type SetupStep = 'welcome' | 'eyes' | 'band' | 'clench' | 'done'
export const LAUNCH_MS = 8000
export const STEP_MS: Record<SetupStep, number> = { welcome: 6000, eyes: 45000, band: 30000, clench: 20000, done: 6000 }
export const NEXT_STEP: Record<SetupStep, SetupStep | null> = { welcome: 'eyes', eyes: 'band', band: 'done', clench: 'done', done: null }

export function nextSetupStep(step: SetupStep, expired: boolean, eyesFinished: boolean,
  goodForMs: number, verifiedClench: boolean): SetupStep | 'exit' | null {
  if (step === 'eyes' && eyesFinished) return 'band'
  if (step === 'band' && goodForMs >= 2000) return 'clench'
  if (step === 'clench' && verifiedClench) return 'done'
  return expired ? NEXT_STEP[step] ?? 'exit' : null
}

/** Monotonic countdown; hidden pages and blocked setup never consume time. */
export class SetupCountdown {
  private previous: number
  remaining: number
  constructor(duration: number, now: number) { this.remaining = duration; this.previous = now }
  tick(now: number, paused: boolean): number {
    if (!paused) this.remaining = Math.max(0, this.remaining - Math.max(0, now - this.previous))
    this.previous = now
    return this.remaining
  }
}

export function signalReady(signal: Signal | null, now: number): boolean {
  return museFresh(signal, now) && !signal?.blocked && signal?.ch.length === 4 &&
    signal.ch.every((v) => Number.isFinite(v) && v >= 1 && v <= 200)
}

export function canEnableMuse(clenched: boolean, signal: Signal | null, now: number): boolean {
  return clenched && signalReady(signal, now)
}

/** A scan needs the complete board. The chosen layout is retained for when pointing resumes. */
export function effectiveTripLayout(preferred: TripLayout, pointer: ActivePointer | null): TripLayout {
  return pointer === 'scan' ? 'car' : preferred
}

/** Pair deliberate bilateral blinks, reject stale tracking and a pair spanning different options. */
export class SetupBlinks {
  private first: { at: number; option: string } | null = null
  private fired = -Infinity
  blink(at: number, option: string, recentlySeen: boolean, bilateral: boolean): boolean {
    if (!recentlySeen || !bilateral || at - this.fired < 1200) {
      this.first = null
      return false
    }
    const previous = this.first
    if (previous && at - previous.at < 100) return false
    if (previous && previous.option === option && at - previous.at <= 750) {
      this.first = null
      this.fired = at
      return true
    }
    this.first = { at, option }
    return false
  }
}
