import { describe, expect, it } from 'vitest'
import { OneEuroFilter2D, ONE_EURO_RESET_MS } from './oneEuro'
import { DwellTimer, GAZE_TUNING, HEAD_TUNING, TilePointer } from './tilePointer'
import type { Rect } from './tiles'

// Two tiles side by side: left half, right half.
const RECTS: Rect[] = [
  { left: 0, top: 0, right: 0.5, bottom: 1 },
  { left: 0.5, top: 0, right: 1, bottom: 1 },
]
const LEFT = { x: 0.25, y: 0.5 }
const RIGHT = { x: 0.75, y: 0.5 }

describe('OneEuroFilter2D', () => {
  it('passes the first point through and smooths jitter at rest', () => {
    const f = new OneEuroFilter2D()
    expect(f.filter({ x: 0.5, y: 0.5 }, 0)).toEqual({ x: 0.5, y: 0.5 })
    let out = { x: 0, y: 0 }
    for (let i = 1; i <= 30; i++) out = f.filter({ x: 0.5 + (i % 2 ? 0.02 : -0.02), y: 0.5 }, i * 33)
    expect(Math.abs(out.x - 0.5)).toBeLessThan(0.01) // the +/-0.02 jitter is mostly gone
  })

  it('follows a jump quickly (the cutoff rises with speed)', () => {
    const f = new OneEuroFilter2D()
    for (let i = 0; i < 10; i++) f.filter(LEFT, i * 33)
    let out = LEFT
    for (let i = 10; i < 20; i++) out = f.filter(RIGHT, i * 33) // 330 ms after the jump
    expect(out.x).toBeGreaterThan(0.7)
  })

  it('starts over after a gap (eyes lost) instead of dragging the old point', () => {
    const f = new OneEuroFilter2D()
    for (let i = 0; i < 10; i++) f.filter(LEFT, i * 33)
    expect(f.filter(RIGHT, 9 * 33 + ONE_EURO_RESET_MS + 1)).toEqual(RIGHT)
  })
})

describe('TilePointer', () => {
  it('head settings: moves at once, as before', () => {
    const p = new TilePointer(HEAD_TUNING)
    p.reset(0)
    expect(p.update(RIGHT, 0, RECTS)).toMatchObject({ tile: 1, changed: true })
  })

  it('gaze: moves only after the new tile holds for holdMs', () => {
    const p = new TilePointer({ ...GAZE_TUNING, oneEuro: false, holdMs: 300 })
    p.reset(0)
    expect(p.update(RIGHT, 0, RECTS)).toMatchObject({ candidate: 1, tile: 0, changed: false })
    expect(p.update(RIGHT, 299, RECTS).tile).toBe(0)
    expect(p.update(RIGHT, 300, RECTS)).toMatchObject({ tile: 1, changed: true })
  })

  it('gaze: a flick shorter than the hold never moves the highlight', () => {
    const p = new TilePointer({ ...GAZE_TUNING, oneEuro: false, holdMs: 300 })
    p.reset(0)
    p.update(RIGHT, 0, RECTS)
    p.update(RIGHT, 200, RECTS)
    p.update(LEFT, 250, RECTS) // back before 300 ms
    expect(p.update(RIGHT, 400, RECTS).tile).toBe(0) // the hold starts again at 400
    expect(p.update(RIGHT, 700, RECTS).tile).toBe(1)
  })

  it('losing the eyes drops a pending move', () => {
    const p = new TilePointer({ ...GAZE_TUNING, oneEuro: false, holdMs: 300 })
    p.reset(0)
    p.update(RIGHT, 0, RECTS)
    p.lost()
    expect(p.update(RIGHT, 350, RECTS).tile).toBe(0) // the hold counts from 350, not 0
  })

  it('no highlight yet: takes the tile under the point at once', () => {
    const p = new TilePointer({ ...GAZE_TUNING, oneEuro: false })
    p.reset(null)
    expect(p.update(RIGHT, 0, RECTS).tile).toBe(1)
  })
})

describe('DwellTimer', () => {
  it('fires once after dwellMs on the same tile, then waits for the tile to change', () => {
    const d = new DwellTimer(1500)
    expect(d.update(2, 0)).toEqual({ progress: 0, fire: false })
    expect(d.update(2, 750).progress).toBeCloseTo(0.5)
    expect(d.update(2, 1500)).toEqual({ progress: 1, fire: true })
    expect(d.update(2, 5000).fire).toBe(false)
    d.update(3, 5000)
    expect(d.update(3, 6500).fire).toBe(true)
  })

  it('no tile (not seen, or dwell not allowed) starts over', () => {
    const d = new DwellTimer(1500)
    d.update(2, 0)
    d.update(null, 1000)
    expect(d.update(2, 1400).fire).toBe(false)
    expect(d.update(2, 2900).fire).toBe(true)
  })
})
