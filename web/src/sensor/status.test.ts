import { describe, expect, it } from 'vitest'
import type { Signal } from '../contracts'
import { museFresh, museState } from './status'

const signal = (t: number, over: Partial<Signal> = {}): Signal => ({
  type: 'SIGNAL', t, ch: [], connected: true, profile: 'taher', emg: 12, threshold: 35, blocked: null, ...over,
})

describe('museFresh', () => {
  it('treats a sample stamped slightly after the page clock as fresh', () => {
    // The page clock only ticks every 250 ms and a SIGNAL arrives every ~285 ms, so the newest
    // sample is routinely stamped after `now`. That used to read as Disconnected and flicker.
    expect(museFresh(signal(100.2), 100)).toBe(true)
  })

  it('is fresh for a recent sample', () => {
    expect(museFresh(signal(99), 100)).toBe(true)
  })

  it('is stale after two seconds without a sample', () => {
    expect(museFresh(signal(97.9), 100)).toBe(false)
  })

  it('is stale when the headband reports it is not connected', () => {
    expect(museFresh(signal(100, { connected: false }), 100)).toBe(false)
  })

  it('rejects a sample from far in the future (a broken clock, not jitter)', () => {
    expect(museFresh(signal(110), 100)).toBe(false)
  })
})

describe('museState', () => {
  it('stays Paused, not Disconnected, across the page clock lagging a new sample', () => {
    expect(museState(signal(100.2), 100, false)).toBe('Paused')
  })

  it('shows the sensor reason when enabled and blocked', () => {
    expect(museState(signal(100, { blocked: 'TP9 poor contact' }), 100, true)).toBe('TP9 poor contact')
  })

  it('is Ready when enabled and clear', () => {
    expect(museState(signal(100), 100, true)).toBe('Ready')
  })
})
