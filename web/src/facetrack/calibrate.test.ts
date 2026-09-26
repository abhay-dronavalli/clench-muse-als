import { describe, expect, it } from 'vitest'
import { aroundCenter, emptySamples, median, MIN_SAMPLES, rangeFromSamples, type Samples } from './calibrate'
import type { Angles } from './pose'

function steady(yaw: number, pitch: number, n = 10, noise = 0.5): Angles[] {
  return Array.from({ length: n }, (_, i) => ({ yaw: yaw + (i % 2 ? noise : -noise), pitch: pitch + (i % 2 ? -noise : noise) }))
}

function good(): Samples {
  return {
    center: steady(1, -6),
    left: steady(19, -6),
    right: steady(-16, -5),
    up: steady(0, 6),
    down: steady(2, -22),
  }
}

describe('median', () => {
  it('odd and even counts', () => {
    expect(median([3, 1, 2])).toBe(2)
    expect(median([4, 1, 3, 2])).toBe(2.5)
  })

  it('ignores a wild frame', () => {
    expect(median([10, 10.2, 9.9, 60, 10.1])).toBeCloseTo(10.1)
  })
})

describe('rangeFromSamples', () => {
  it('takes the median of each step', () => {
    const r = rangeFromSamples(good())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.range.center_yaw).toBeCloseTo(1)
    expect(r.range.left_yaw).toBeCloseTo(19)
    expect(r.range.right_yaw).toBeCloseTo(-16)
    expect(r.range.up_pitch).toBeCloseTo(6)
    expect(r.range.down_pitch).toBeCloseTo(-22)
    expect(r.range.center_pitch).toBeCloseTo(-6)
  })

  it('accepts a mirrored camera (left is the negative side)', () => {
    const s = good()
    ;[s.left, s.right] = [s.right, s.left]
    expect(rangeFromSamples(s).ok).toBe(true)
  })

  it('says which step did not see the face', () => {
    const s = good()
    s.up = steady(0, 6, MIN_SAMPLES - 1)
    expect(rangeFromSamples(s)).toEqual({ ok: false, problem: { kind: 'no_face', step: 'up' } })
    expect(rangeFromSamples(emptySamples())).toEqual({ ok: false, problem: { kind: 'no_face', step: 'center' } })
  })

  it('rejects a side that did not turn away from the center', () => {
    const s = good()
    s.right = steady(2, -6) // barely moved
    expect(rangeFromSamples(s)).toEqual({ ok: false, problem: { kind: 'too_small', axis: 'yaw' } })
  })

  it('rejects both sides on the same side of the center', () => {
    const s = good()
    s.down = steady(0, 1) // looked up both times
    expect(rangeFromSamples(s)).toEqual({ ok: false, problem: { kind: 'too_small', axis: 'pitch' } })
  })
})

describe('aroundCenter', () => {
  it('needs opposite sides at least 2 degrees out', () => {
    expect(aroundCenter(-5, 0, 5)).toBe(true)
    expect(aroundCenter(5, 0, -5)).toBe(true)
    expect(aroundCenter(-1.5, 0, 5)).toBe(false)
    expect(aroundCenter(3, 0, 5)).toBe(false)
  })
})
