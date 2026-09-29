// The one-target check of a saved eye calibration, ported from the tablet shell
// (kushagra/tablet/.../board/GazeMath.kt validationPasses, BoardActivity validate): one dot at the
// center of a random tile of the board's grid; the check passes when the MEDIAN gaze while it shows
// lands inside that tile, so a few stray samples do not decide it.

export interface Frac {
  x: number
  y: number
}

/** The board's grid: 3 across x 2 down in landscape, 2 x 3 in portrait. */
export function boardGrid(width: number, height: number): { cols: number; rows: number } {
  return height > width ? { cols: 2, rows: 3 } : { cols: 3, rows: 2 }
}

/** The center of tile `index` (row by row), as fractions of the window. */
export function tileCenter(index: number, cols: number, rows: number): Frac {
  return { x: ((index % cols) + 0.5) / cols, y: (Math.floor(index / cols) + 0.5) / rows }
}

function median(v: number[]): number {
  const s = [...v].sort((a, b) => a - b)
  const mid = Math.floor(s.length / 2)
  return s.length % 2 === 1 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

/**
 * Does the gaze land on the tile around `target`? `halfW`, `halfH` = half a tile. Fewer than
 * `minSamples` tracked samples fails: too little to say the calibration is good.
 */
export function validationPasses(samples: Frac[], target: Frac, halfW: number, halfH: number, minSamples = 10): boolean {
  if (samples.length < minSamples) return false
  const mx = median(samples.map((s) => s.x))
  const my = median(samples.map((s) => s.y))
  return Math.abs(mx - target.x) <= halfW && Math.abs(my - target.y) <= halfH
}
