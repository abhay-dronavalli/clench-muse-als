// How the trip screen's 3D car scene moves, as plain maths (no three.js, unit tested). A port of the
// tablet's CarMotion.kt (kushagra/tablet/app/.../car/CarMotion.kt): the world scrolling past the parked
// car (so it looks like driving), the camera's slow circle around it, the short push toward the car on
// a control, and each control's line particles. CarScene.ts applies it to three.js objects every frame.
//
// Motion rules (some riders have sensory or vestibular sensitivity): everything eases in and out, no
// line ever appears or vanishes at full size (it grows from nothing and shrinks back), nothing flashes,
// and the camera move is small.
//
// Car space: the model is centred on the origin and scaled so its longest side is 1 unit. `Box` gives
// its half sizes; x is along the car (front = +x), z across it, y up.

export interface Box {
  halfLength: number
  halfWidth: number
  halfHeight: number
}

/** One particle line: where its middle is, how long it is (0 = not drawn), and how it leans (degrees). */
export interface Line {
  x: number
  y: number
  z: number
  length: number
  tiltDeg: number
}

export const DRIVE_SPEED = 1.4 // units (car lengths) a second the road and trees move past
export const ORBIT_PERIOD_S = 45 // one slow lap of the camera around the car
export const ORBIT_START_DEG = 35 // a front three-quarter view to begin with
export const LINE_LENGTH = 0.11

/** The car's box before a model is loaded: the tablet's own default when the model is missing. */
export const DEFAULT_BOX: Box = { halfLength: 0.5, halfWidth: 0.22, halfHeight: 0.17 }

/** The car's box from its size (any units): scaled so the longest side is 1 unit, centred on the origin. */
export function boxFromSize(size: [number, number, number]): Box {
  const [sx, sy, sz] = size
  const longest = Math.max(sx, sy, sz) || 1
  return {
    halfLength: (0.5 * Math.max(sx, sz)) / longest,
    halfWidth: (0.5 * Math.min(sx, sz)) / longest,
    halfHeight: (0.5 * sy) / longest,
  }
}

export function clamp01(t: number): number {
  return Math.min(1, Math.max(0, t))
}

export function smooth(t: number): number {
  const x = clamp01(t)
  return x * x * (3 - 2 * x)
}

/** 0 -> 1 -> 0 over u in 0..1, zero slope at both ends (a line grows and shrinks, never pops). */
export function bump(u: number): number {
  if (u <= 0 || u >= 1) return 0
  return 0.5 - 0.5 * Math.cos(2 * Math.PI * u)
}

/**
 * Where a scenery piece placed at `base` is after the world has moved `travel` backwards, wrapped into
 * [-span/2, span/2): pieces leaving behind the car come back in far ahead (out of sight).
 */
export function wrap(base: number, travel: number, span: number): number {
  const x = (base - travel + span / 2) % span
  return (x < 0 ? x + span : x) - span / 2
}

/** The camera on its circle: x, y, z for orbit angle `degrees` (0 = straight out the car's side). */
export function orbit(degrees: number, radius: number, height: number): [number, number, number] {
  const a = (degrees * Math.PI) / 180
  return [radius * Math.sin(a), height, radius * Math.cos(a)]
}

/**
 * How far the camera moves toward the car at `progress` 0..1 of a control's sequence (units; the camera
 * starts about 1 unit away). Routine: in and back out, smoothly. Pull over: a quicker, firmer push that
 * holds (the car is stopping).
 */
export function push(action: string, progress: number): number {
  if (action === 'pull_over') return 0.08 * smooth(progress / 0.35)
  return 0.14 * Math.sin(Math.PI * clamp01(progress))
}

/**
 * The lines for `action` at `progress` 0..1 of its sequence; `window` picks the door for the window
 * controls (front_left, front_right, rear_left, rear_right, all). Pull over, Slow down and Support have
 * none on purpose (the drive itself slows or stops).
 */
export function lines(action: string, progress: number, box: Box, window = 'all'): Line[] {
  switch (action) {
    case 'window_up':
      return doorLines(progress, box, true, window)
    case 'window_down':
      return doorLines(progress, box, false, window)
    case 'warmer':
      return ventLines(progress, box, true)
    case 'cooler':
      return ventLines(progress, box, false)
    case 'louder':
    case 'music':
      return musicLines(progress, box, true)
    case 'softer':
      return musicLines(progress, box, false)
    default:
      return []
  }
}

/** Line `i` of `n`, starting a little after the one before: its own 0..1 time, or <0 / >1 outside it. */
function local(progress: number, i: number, n: number, life = 0.6): number {
  const start = ((1 - life) * i) / Math.max(n - 1, 1)
  return (progress - start) / life
}

/** A fixed, even spread in -1..1 for line `i` (no randomness: the same every time). */
function spread(i: number, n: number): number {
  return n <= 1 ? 0 : -1 + (2 * i) / (n - 1)
}

interface Door {
  from: number
  to: number
  side: number
}

/**
 * Windows: lines trace that window's door glass, rising (up) or falling (down). The car faces +x with
 * +z on its right, so front = +x, left = -z. "all" traces both sides along the whole cabin.
 */
function doorLines(progress: number, box: Box, up: boolean, window: string): Line[] {
  const bottom = 0.05 * box.halfHeight
  const top = 0.95 * box.halfHeight
  // (x range as a share of halfLength, side) for each door this window control covers.
  const doors: Door[] = (
    {
      front_left: [{ from: 0.05, to: 0.45, side: -1 }],
      front_right: [{ from: 0.05, to: 0.45, side: 1 }],
      rear_left: [{ from: -0.45, to: -0.05, side: -1 }],
      rear_right: [{ from: -0.45, to: -0.05, side: 1 }],
    } as Record<string, Door[]>
  )[window] ?? [
    { from: -0.5, to: 0.5, side: -1 },
    { from: -0.5, to: 0.5, side: 1 },
  ]
  const perDoor = doors.length === 1 ? 5 : 4
  const n = perDoor * doors.length
  return doors.flatMap((door, d) =>
    Array.from({ length: perDoor }, (_, k) => {
      const i = d * perDoor + k
      const u = local(progress, i, n)
      const travel = smooth(u)
      const y = up ? bottom + (top - bottom) * travel : top - (top - bottom) * travel
      return {
        x: box.halfLength * (door.from + ((door.to - door.from) * (k + 0.5)) / perDoor),
        y,
        z: door.side * box.halfWidth * 1.06, // just outside that door
        length: LINE_LENGTH * bump(u),
        tiltDeg: 0,
      }
    }),
  )
}

/** Heat: warm lines rise and spread outward from the vents; cool lines drift down and settle. */
function ventLines(progress: number, box: Box, warm: boolean): Line[] {
  const n = 10
  return Array.from({ length: n }, (_, i) => {
    const u = local(progress, i, n, 0.55)
    const t = smooth(u)
    const across = spread(i, n)
    const roof = box.halfHeight * 1.05
    const y = warm ? roof + 0.28 * t : roof + 0.32 * (1 - t) + 0.02
    const outward = warm ? 1 + 0.35 * t : 1 + 0.1 * (1 - t)
    return {
      x: 0.15 * box.halfLength + 0.08 * spread((i * 3) % n, n),
      y,
      z: 0.8 * box.halfWidth * across * outward,
      length: LINE_LENGTH * 0.9 * bump(u),
      tiltDeg: 18 * across * (warm ? t : 1 - t),
    }
  })
}

/** Music: a ring of short lines above the car pulsing in three soft beats, out (louder) or in (softer). */
function musicLines(progress: number, box: Box, louder: boolean): Line[] {
  const n = 12
  const beats = 3
  const scaled = clamp01(progress) * beats
  const beat = scaled - Math.floor(scaled)
  const pulse = bump(beat) // one smooth swell per beat
  // The whole ring grows in over the first fifth and shrinks away over the last (from and to nothing).
  const envelope = smooth(progress / 0.2) * smooth((1 - progress) / 0.2)
  const radius = louder ? 0.32 + 0.12 * pulse : 0.36 - 0.12 * pulse
  return Array.from({ length: n }, (_, i) => {
    const angle = (2 * Math.PI * i) / n
    return {
      x: radius * Math.cos(angle),
      y: box.halfHeight * 1.1 + 0.06,
      z: radius * Math.sin(angle) * 0.7,
      length: LINE_LENGTH * (0.35 + 0.65 * pulse) * envelope,
      tiltDeg: 90 - (angle * 180) / Math.PI,
    }
  })
}
