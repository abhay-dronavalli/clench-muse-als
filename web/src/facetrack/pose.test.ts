import { describe, expect, it } from 'vitest'
import type { HeadRange } from '../contracts'
import { axis, DEFAULT_RANGE, matrixToAngles, poseToPoint, smooth } from './pose'

/** A column-major 4x4 matrix for a head turned `yaw` degrees about y, then `pitch` about x. */
function headMatrix(yawDeg: number, pitchDeg: number): number[] {
  const y = (yawDeg * Math.PI) / 180
  const p = (pitchDeg * Math.PI) / 180
  // R = Ry(yaw) * Rx(-pitch): the forward axis (0, 0, 1) ends at (sin y cos p, sin p, cos y cos p).
  const forward = [Math.sin(y) * Math.cos(p), Math.sin(p), Math.cos(y) * Math.cos(p)]
  const right = [Math.cos(y), 0, -Math.sin(y)]
  const up = [-Math.sin(y) * Math.sin(p), Math.cos(p), -Math.cos(y) * Math.sin(p)]
  // Columns: right, up, forward, translation (the face 50 cm in front of the camera).
  return [...right, 0, ...up, 0, ...forward, 0, 0, 0, -50, 1]
}

const RANGE: HeadRange = { center_yaw: 2, center_pitch: -5, left_yaw: 20, right_yaw: -10, up_pitch: 5, down_pitch: -25 }

describe('matrixToAngles', () => {
  it('reads yaw and pitch from the forward column', () => {
    const a = matrixToAngles(headMatrix(15, -8))!
    expect(a.yaw).toBeCloseTo(15, 6)
    expect(a.pitch).toBeCloseTo(-8, 6)
  })

  it('is zero when facing the camera', () => {
    const a = matrixToAngles(headMatrix(0, 0))!
    expect(a.yaw).toBeCloseTo(0, 9)
    expect(a.pitch).toBeCloseTo(0, 9)
  })

  it('rejects anything that is not a 4x4 matrix', () => {
    expect(matrixToAngles([1, 2, 3])).toBeNull()
    expect(matrixToAngles(new Array(16).fill(0))).toBeNull()
    expect(matrixToAngles([...headMatrix(0, 0).slice(0, 8), NaN, 0, 1, 0, 0, 0, 0, 1])).toBeNull()
  })
})

describe('smooth', () => {
  it('starts at the first reading', () => {
    expect(smooth(null, { yaw: 10, pitch: 4 }, 0.3)).toEqual({ yaw: 10, pitch: 4 })
  })

  it('moves a fraction of the way each frame', () => {
    const s = smooth({ yaw: 0, pitch: 0 }, { yaw: 10, pitch: -20 }, 0.25)
    expect(s.yaw).toBeCloseTo(2.5)
    expect(s.pitch).toBeCloseTo(-5)
  })

  it('damps a one-frame jitter and follows a steady turn', () => {
    let a = { yaw: 0, pitch: 0 }
    a = smooth(a, { yaw: 12, pitch: 0 }, 0.3) // one noisy frame
    a = smooth(a, { yaw: 0, pitch: 0 }, 0.3)
    expect(Math.abs(a.yaw)).toBeLessThan(3)
    for (let i = 0; i < 20; i++) a = smooth(a, { yaw: 12, pitch: 0 }, 0.3)
    expect(a.yaw).toBeCloseTo(12, 1)
  })
})

describe('axis', () => {
  it('maps low, mid and high to 0, 0.5 and 1', () => {
    expect(axis(-10, -10, 0, 30)).toBe(0)
    expect(axis(0, -10, 0, 30)).toBe(0.5)
    expect(axis(30, -10, 0, 30)).toBe(1)
  })

  it('scales each side on its own', () => {
    expect(axis(-5, -10, 0, 30)).toBeCloseTo(0.25)
    expect(axis(15, -10, 0, 30)).toBeCloseTo(0.75)
  })

  it('works when low is the larger angle', () => {
    expect(axis(20, 20, 0, -20)).toBe(0)
    expect(axis(-10, 20, 0, -20)).toBeCloseTo(0.75)
  })

  it('clamps past the edges', () => {
    expect(axis(-50, -10, 0, 30)).toBe(0)
    expect(axis(90, -10, 0, 30)).toBe(1)
  })
})

describe('poseToPoint', () => {
  it('puts the calibrated center in the middle of the screen', () => {
    expect(poseToPoint({ yaw: 2, pitch: -5 }, RANGE)).toEqual({ x: 0.5, y: 0.5 })
  })

  it('puts the calibrated edges on the screen edges', () => {
    expect(poseToPoint({ yaw: 20, pitch: 5 }, RANGE)).toEqual({ x: 0, y: 0 })
    expect(poseToPoint({ yaw: -10, pitch: -25 }, RANGE)).toEqual({ x: 1, y: 1 })
  })

  it('uses the uneven range on each side', () => {
    const p = poseToPoint({ yaw: -4, pitch: -15 }, RANGE)
    expect(p.x).toBeCloseTo(0.75) // halfway from the center (2) to the right edge (-10)
    expect(p.y).toBeCloseTo(0.75) // halfway from the center (-5) to the bottom (-25)
  })

  it('has usable defaults: facing the camera lands near the middle, turning left goes left', () => {
    const straight = poseToPoint({ yaw: 0, pitch: DEFAULT_RANGE.center_pitch }, DEFAULT_RANGE)
    expect(straight).toEqual({ x: 0.5, y: 0.5 })
    expect(poseToPoint({ yaw: 9, pitch: -8 }, DEFAULT_RANGE).x).toBeCloseTo(0.25)
  })
})
