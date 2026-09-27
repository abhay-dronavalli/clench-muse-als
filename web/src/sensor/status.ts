import type { Signal } from '../contracts'

/** A sample older than this means the headband (or its service) has gone quiet. */
const STALE_S = 2
/**
 * How far a sample may be stamped AFTER the page's clock and still count. The page clock ticks every
 * 250 ms and SIGNAL arrives every ~285 ms, so the newest sample is routinely a little "in the
 * future". Requiring `now >= t` read that as Disconnected and flickered the panel several times a
 * second. Anything further ahead than this is a broken clock, not jitter.
 */
const AHEAD_S = 2

export function museFresh(signal: Signal | null, now: number): boolean {
  if (!signal?.connected) return false
  const age = now - signal.t
  return age > -AHEAD_S && age < STALE_S
}

export function museState(signal: Signal | null, now: number, enabled: boolean): string {
  if (!museFresh(signal, now)) return 'Disconnected'
  if (!enabled) return 'Paused'
  return signal?.blocked || 'Ready'
}
