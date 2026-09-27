import { describe, expect, it } from 'vitest'
import type { Signal } from '../contracts'
import { canEnableMuse, effectiveTripLayout, LAUNCH_MS, nextSetupStep, SetupBlinks, SetupCountdown } from './onboardingFlow'

const signal: Signal = { type: 'SIGNAL', t: 100, ch: [10, 20, 30, 40], connected: true }

describe('setup timers and permissions', () => {
  it('counts down launch, pauses while hidden and cannot go below zero', () => {
    const timer = new SetupCountdown(LAUNCH_MS, 1000)
    expect(timer.tick(4000, false)).toBe(5000)
    expect(timer.tick(64000, true)).toBe(5000)
    expect(timer.tick(68999, false)).toBe(1)
    expect(timer.tick(69000, false)).toBe(0)
    expect(timer.tick(90000, false)).toBe(0)
  })
  it('skips failed hardware on expiry, never inventing a successful clench', () => {
    expect(nextSetupStep('welcome', true, false, 0, false)).toBe('eyes')
    expect(nextSetupStep('eyes', true, false, 0, false)).toBe('band')
    expect(nextSetupStep('band', true, false, 0, false)).toBe('done')
    expect(nextSetupStep('clench', true, true, 0, false)).toBe('done')
    expect(nextSetupStep('done', true, true, 0, false)).toBe('exit')
    expect(canEnableMuse(false, signal, 100)).toBe(false)
  })
  it('successful checks progress early, but short/unstable signal does not', () => {
    expect(nextSetupStep('eyes', false, true, 0, false)).toBe('band')
    expect(nextSetupStep('band', false, true, 1999, false)).toBe(null)
    expect(nextSetupStep('band', false, true, 2000, false)).toBe('clench')
    expect(nextSetupStep('clench', false, true, 0, true)).toBe('done')
    expect(canEnableMuse(true, signal, 100)).toBe(true)
  })
  it.each([
    null, { ...signal, connected: false }, { ...signal, t: 90 },
    { ...signal, blocked: 'Head moving' }, { ...signal, ch: [10, 20, 0, 30] },
    { ...signal, ch: [10, 20, 30] }, { ...signal, ch: [NaN, 20, 30, 40] },
  ])('never enables the headband with an unavailable signal: %j', (bad) => {
    expect(canEnableMuse(true, bad, 100)).toBe(false)
  })
})

describe('setup double blinks', () => {
  it('requires two bilateral blinks and debounces after selection', () => {
    const b = new SetupBlinks()
    expect(b.blink(0, 'start', true, true)).toBe(false)
    expect(b.blink(20, 'start', true, true)).toBe(false)
    expect(b.blink(300, 'start', true, true)).toBe(true)
    expect(b.blink(600, 'start', true, true)).toBe(false)
    expect(b.blink(900, 'start', true, true)).toBe(false)
  })
  it('cannot carry the first blink into another option or step', () => {
    const b = new SetupBlinks()
    b.blink(0, 'welcome:continue', true, true)
    expect(b.blink(400, 'eyes:continue', true, true)).toBe(false)
  })
  it('rejects stale tracking, one eye, and unrelated natural blinks', () => {
    const b = new SetupBlinks()
    expect(b.blink(0, 'go', true, false)).toBe(false)
    expect(b.blink(300, 'go', true, true)).toBe(false)
    expect(b.blink(600, 'go', false, true)).toBe(false)
    expect(b.blink(1000, 'go', true, true)).toBe(false)
    expect(b.blink(2500, 'go', true, true)).toBe(false)
  })
})

it('restores the preferred layout after scanning without changing the preference', () => {
  for (const preferred of ['car', 'split', 'map'] as const) {
    expect(effectiveTripLayout(preferred, 'scan')).toBe('car')
    expect(effectiveTripLayout(preferred, 'gaze')).toBe(preferred)
  }
})
