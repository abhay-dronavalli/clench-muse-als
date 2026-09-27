import { describe, expect, it } from 'vitest'
import { boardGrid, tileCenter, validationPasses } from './gazeCheck'

// The same cases as the tablet's GazeMathTest (kushagra/tablet/.../GazeMathTest.kt).
const target = { x: 0.5, y: 0.25 }
const around = (x: number, y: number, n = 12) => Array.from({ length: n }, () => ({ x, y }))

describe('validationPasses', () => {
  it('passes when the gaze lands on the target tile', () => {
    expect(validationPasses(around(0.55, 0.3), target, 1 / 6, 1 / 4)).toBe(true)
  })

  it('fails when the gaze lands on another tile', () => {
    expect(validationPasses(around(0.9, 0.3), target, 1 / 6, 1 / 4)).toBe(false)
  })

  it('uses the median, so a few stray samples do not decide it', () => {
    const samples = [...around(0.5, 0.25, 10), { x: 0.99, y: 0.99 }, { x: 0.01, y: 0.99 }]
    expect(validationPasses(samples, target, 1 / 6, 1 / 4)).toBe(true)
  })

  it('fails with too few tracked samples', () => {
    expect(validationPasses(around(0.5, 0.25, 9), target, 1 / 6, 1 / 4)).toBe(false)
  })
})

describe('the board grid', () => {
  it('is 3 across x 2 down in landscape and 2 x 3 in portrait', () => {
    expect(boardGrid(1920, 1080)).toEqual({ cols: 3, rows: 2 })
    expect(boardGrid(800, 1280)).toEqual({ cols: 2, rows: 3 })
  })

  it('puts a tile center in the middle of its cell', () => {
    expect(tileCenter(0, 3, 2)).toEqual({ x: 1 / 6, y: 0.25 })
    expect(tileCenter(5, 3, 2)).toEqual({ x: 5 / 6, y: 0.75 })
  })
})
