// Which tile the head is pointing at: the one whose on-screen box holds the point, or the nearest
// one, with sticky edges so the highlight does not flicker on a border (PRD A3.3a). Works for any
// layout (the 3x2 grid, long sentence tiles), since it only looks at the boxes. Pure, unit tested.

import type { ScreenPoint } from './pose'

/** A tile's box as fractions of the window (0..1), like ScreenPoint. */
export interface Rect {
  left: number
  top: number
  right: number
  bottom: number
}

/** About 15% of a tile's size: how far the point must be inside a new tile before it moves there. */
export const STICKY_MARGIN = 0.15

export function contains(r: Rect, p: ScreenPoint): boolean {
  return p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom
}

/** `r` shrunk by `margin` of its own width and height on every side. */
export function shrink(r: Rect, margin: number): Rect {
  const dx = (r.right - r.left) * margin
  const dy = (r.bottom - r.top) * margin
  return { left: r.left + dx, top: r.top + dy, right: r.right - dx, bottom: r.bottom - dy }
}

/** Distance from `p` to the box (0 inside it). */
export function distance(r: Rect, p: ScreenPoint): number {
  const dx = Math.max(r.left - p.x, 0, p.x - r.right)
  const dy = Math.max(r.top - p.y, 0, p.y - r.bottom)
  return Math.hypot(dx, dy)
}

function nearest(rects: Rect[], p: ScreenPoint): number {
  let best = 0
  for (let i = 1; i < rects.length; i++) if (distance(rects[i], p) < distance(rects[best], p)) best = i
  return best
}

/**
 * The tile to highlight for point `p`, given the one highlighted now (`current`, null = none yet).
 *
 *   - no current tile: the tile holding the point, else the nearest one;
 *   - the point is on another tile: move only once it is clearly inside, past `margin` of that
 *     tile's size from its edges;
 *   - the point is between tiles or off the grid: move to the nearest tile only when even its
 *     shrunk box is closer than the current tile, so a gap never flips the highlight but looking
 *     well past the grid's edge still reaches the edge tile.
 *
 * Null only when there are no tiles.
 */
export function chooseTile(p: ScreenPoint, rects: Rect[], current: number | null, margin = STICKY_MARGIN): number | null {
  if (rects.length === 0) return null
  const inside = rects.findIndex((r) => contains(r, p))
  if (current === null || current < 0 || current >= rects.length) return inside >= 0 ? inside : nearest(rects, p)
  if (inside === current) return current
  if (inside >= 0) return contains(shrink(rects[inside], margin), p) ? inside : current
  const n = nearest(rects, p)
  if (n === current) return current
  return distance(shrink(rects[n], margin), p) < distance(rects[current], p) ? n : current
}
