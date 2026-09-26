import { describe, expect, it } from 'vitest'
import { chooseTile, contains, distance, shrink, STICKY_MARGIN, type Rect } from './tiles'

const M = 0.15 // the mechanism tests below use a wide margin so the numbers are easy to read

/** The board's 3x2 grid: tiles 0.3 wide and 0.4 tall with gaps, under a breadcrumb. */
const GRID: Rect[] = [0, 1].flatMap((row) =>
  [0, 1, 2].map((col) => ({
    left: 0.03 + col * 0.32,
    right: 0.33 + col * 0.32,
    top: 0.12 + row * 0.43,
    bottom: 0.52 + row * 0.43,
  })),
)

describe('rect helpers', () => {
  const r = { left: 0.2, top: 0.2, right: 0.6, bottom: 0.4 }

  it('contains and distance', () => {
    expect(contains(r, { x: 0.3, y: 0.3 })).toBe(true)
    expect(contains(r, { x: 0.1, y: 0.3 })).toBe(false)
    expect(distance(r, { x: 0.3, y: 0.3 })).toBe(0)
    expect(distance(r, { x: 0.1, y: 0.3 })).toBeCloseTo(0.1)
  })

  it('shrink takes a share of width and height from each side', () => {
    const s = shrink(r, 0.25)
    expect(s.left).toBeCloseTo(0.3)
    expect(s.right).toBeCloseTo(0.5)
    expect(s.top).toBeCloseTo(0.25)
    expect(s.bottom).toBeCloseTo(0.35)
  })
})

describe('chooseTile', () => {
  it('picks the tile holding the point when nothing is highlighted yet', () => {
    expect(chooseTile({ x: 0.5, y: 0.3 }, GRID, null)).toBe(1)
    expect(chooseTile({ x: 0.9, y: 0.8 }, GRID, null)).toBe(5)
  })

  it('picks the nearest tile when the point is off the grid', () => {
    expect(chooseTile({ x: 0.0, y: 0.05 }, GRID, null)).toBe(0)
    expect(chooseTile({ x: 1.0, y: 1.0 }, GRID, null)).toBe(5)
  })

  it('stays on the current tile while the point is just over the border', () => {
    // Tile 1 spans x 0.35..0.65; 15% of its width is 0.045.
    expect(chooseTile({ x: 0.36, y: 0.3 }, GRID, 0, M)).toBe(0)
    expect(chooseTile({ x: 0.39, y: 0.3 }, GRID, 0, M)).toBe(0)
  })

  it('moves once the point is clearly inside the new tile', () => {
    expect(chooseTile({ x: 0.4, y: 0.3 }, GRID, 0, M)).toBe(1)
  })

  it('uses each tile own size for the margin (a tall tile needs more vertical travel)', () => {
    // Tile 3 spans y 0.55..0.95; 15% of 0.4 is 0.06.
    expect(chooseTile({ x: 0.1, y: 0.58 }, GRID, 0, M)).toBe(0)
    expect(chooseTile({ x: 0.1, y: 0.62 }, GRID, 0, M)).toBe(3)
  })

  it('never moves on a point in the gap between tiles', () => {
    expect(chooseTile({ x: 0.34, y: 0.3 }, GRID, 0, M)).toBe(0)
    expect(chooseTile({ x: 0.34, y: 0.3 }, GRID, 1, M)).toBe(1)
  })

  it('reaches the edge tile when looking well past the grid', () => {
    expect(chooseTile({ x: 0.0, y: 0.3 }, GRID, 1, M)).toBe(0)
    expect(chooseTile({ x: 0.5, y: 1.0 }, GRID, 1, M)).toBe(4)
  })

  it('does not flicker back and forth on the border', () => {
    let current: number | null = 0
    const path = [0.34, 0.36, 0.34, 0.37, 0.33, 0.36]
    for (const x of path) current = chooseTile({ x, y: 0.3 }, GRID, current, M)
    expect(current).toBe(0)
  })

  it('works for any layout, e.g. one row of long sentence tiles', () => {
    const rows: Rect[] = [0, 1, 2, 3].map((i) => ({ left: 0.05, right: 0.95, top: 0.1 + i * 0.22, bottom: 0.3 + i * 0.22 }))
    expect(chooseTile({ x: 0.5, y: 0.5 }, rows, null)).toBe(1)
    expect(chooseTile({ x: 0.5, y: 0.555 }, rows, 1, M)).toBe(1) // tile 2 starts at 0.54: not far enough in
    expect(chooseTile({ x: 0.5, y: 0.6 }, rows, 1, M)).toBe(2)
  })

  it('uses a 5% margin by default', () => {
    expect(STICKY_MARGIN).toBe(0.05)
    // Tile 1 spans x 0.35..0.65; 5% of its width is 0.015.
    expect(chooseTile({ x: 0.36, y: 0.3 }, GRID, 0)).toBe(0)
    expect(chooseTile({ x: 0.37, y: 0.3 }, GRID, 0)).toBe(1)
  })

  it('a margin of 0 moves as soon as the point is on the new tile', () => {
    expect(chooseTile({ x: 0.351, y: 0.3 }, GRID, 0, 0)).toBe(1)
    expect(chooseTile({ x: 0.335, y: 0.3 }, GRID, 0, 0)).toBe(0) // a gap point nearer the current tile
  })

  it('handles no tiles and a stale current index', () => {
    expect(chooseTile({ x: 0.5, y: 0.5 }, [], 2)).toBeNull()
    expect(chooseTile({ x: 0.9, y: 0.3 }, GRID, 9, M)).toBe(2)
  })
})
