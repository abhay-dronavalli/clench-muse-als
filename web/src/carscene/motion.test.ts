import { describe, expect, it } from 'vitest'
import * as Motion from './motion'

const box = Motion.boxFromSize([4.5, 1.4, 1.9])

describe('scenery wrap', () => {
  it('keeps pieces inside their span and repeats every span', () => {
    for (let travel = -50; travel <= 50; travel += 0.7) {
      const x = Motion.wrap(1.3, travel, 10)
      expect(x).toBeGreaterThanOrEqual(-5)
      expect(x).toBeLessThan(5)
      expect(Motion.wrap(1.3, travel + 10, 10)).toBeCloseTo(x, 9)
    }
  })
})

describe('camera orbit', () => {
  it('stays on its circle, at its height', () => {
    for (let deg = 0; deg < 360; deg += 15) {
      const [x, y, z] = Motion.orbit(deg, 2.3, 0.2)
      expect(Math.hypot(x, z)).toBeCloseTo(2.3, 6)
      expect(y).toBe(0.2)
    }
  })

  it('starts a front three-quarter view and runs one lap in 45 s', () => {
    expect(Motion.ORBIT_START_DEG).toBe(35)
    expect(Motion.ORBIT_PERIOD_S).toBe(45)
  })
})

describe('push toward the car', () => {
  it('starts and ends at no push for a routine control', () => {
    expect(Motion.push('warmer', 0)).toBeCloseTo(0, 9)
    expect(Motion.push('warmer', 1)).toBeCloseTo(0, 9)
    expect(Motion.push('warmer', 0.5)).toBeCloseTo(0.14, 9)
  })

  it('pull over pushes in and holds', () => {
    expect(Motion.push('pull_over', 0)).toBe(0)
    expect(Motion.push('pull_over', 0.35)).toBeCloseTo(0.08, 9)
    expect(Motion.push('pull_over', 1)).toBeCloseTo(0.08, 9)
  })
})

describe('control effects', () => {
  it('no effect lines for Pull over, Slow down or Support', () => {
    for (const action of ['pull_over', 'slow_down', 'support']) {
      expect(Motion.lines(action, 0.5, box)).toEqual([])
    }
  })

  it('every line starts and ends at nothing (no pop)', () => {
    for (const action of ['window_up', 'window_down', 'warmer', 'cooler', 'louder', 'softer']) {
      for (const p of [0, 1]) {
        for (const line of Motion.lines(action, p, box)) expect(line.length).toBeCloseTo(0, 9)
      }
    }
  })

  it('no line jumps in size between frames (no flicker)', () => {
    for (const action of ['window_up', 'warmer', 'cooler', 'louder', 'softer']) {
      const frames = 120
      let before = Motion.lines(action, 0, box)
      let worst = 0
      for (let f = 1; f <= frames; f++) {
        const now = Motion.lines(action, f / frames, box)
        now.forEach((l, k) => (worst = Math.max(worst, Math.abs(l.length - before[k].length))))
        before = now
      }
      expect(worst).toBeLessThan(0.02)
    }
  })

  it('window controls trace only their own door', () => {
    const front = Motion.lines('window_up', 0.5, box, 'front_left')
    expect(front.length).toBe(5)
    expect(front.every((l) => l.z < 0)).toBe(true)
    const rightOnly = Motion.lines('window_down', 0.5, box, 'rear_right')
    expect(rightOnly.every((l) => l.z > 0 && l.x < 0)).toBe(true)
  })
})

describe('the car box', () => {
  it('scales the longest side to 1 unit and keeps the car along x', () => {
    const b = Motion.boxFromSize([4.5, 1.4, 1.9])
    expect(b.halfLength).toBeCloseTo(0.5, 9)
    expect(b.halfWidth).toBeCloseTo(0.5 * 1.9 / 4.5, 9)
    expect(b.halfHeight).toBeCloseTo(0.5 * 1.4 / 4.5, 9)
  })

  it('falls back to the tablet default when there is no model', () => {
    expect(Motion.DEFAULT_BOX).toEqual({ halfLength: 0.5, halfWidth: 0.22, halfHeight: 0.17 })
  })
})
