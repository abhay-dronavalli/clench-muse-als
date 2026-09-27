import type { Signal } from '../contracts'

export function museFresh(signal: Signal | null, now: number): boolean {
  return !!signal?.connected && now >= signal.t && now - signal.t < 2
}

export function museState(signal: Signal | null, now: number, enabled: boolean): string {
  if (!museFresh(signal, now)) return 'Disconnected'
  if (!enabled) return 'Paused'
  return signal?.blocked || 'Ready'
}
