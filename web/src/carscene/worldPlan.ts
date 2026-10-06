// What the trip scene's world is made of and where it goes: plain data and maths, unit tested. A port of
// the tablet's WorldPlan.kt. world.ts turns it into three.js objects.
//
// Random each trip (a new seed), so no two rides look alike, and each piece is re-rolled (its setback,
// size, turn, colour shade) every time it wraps around behind the car, so the road never visibly
// repeats. Rules that keep it calm and readable:
//   - Anything taller than a flower stands beyond the camera's circle (CAMERA_RADIUS): nothing ever
//     comes between the camera and the car.
//   - Only low things (flowers, rocks) sit near the road.
//   - No traffic: a car sweeping past the circling camera would be a jolt.
//   - Far things move slower than near ones (parallax), clouds drift on their own.

export type Kind = 'ROUND_TREE' | 'PINE' | 'CYPRESS' | 'BUSH' | 'HOUSE' | 'FLOWER' | 'ROCK' | 'CLOUD' | 'HILL'

/** One piece: its kind, where it starts along the road, its lateral place, size and shade. */
export interface Piece {
  kind: Kind
  baseX: number
  z: number
  scale: number
  turnDeg: number
  shade: number // index into the kind's colour palette
  span: number // it wraps around over this length of road
  parallax: number // 1 = moves with the road, less = further away
}

/** A seeded random generator (mulberry32): the same seed gives the same world. */
export class Rng {
  private state: number

  constructor(seed: number) {
    this.state = seed >>> 0
  }

  /** A float in [0, 1). */
  nextFloat(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0
    let t = this.state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  nextBoolean(): boolean {
    return this.nextFloat() < 0.5
  }

  /** An int in [0, n). */
  nextInt(n: number): number {
    return Math.floor(this.nextFloat() * n)
  }
}

export const CAMERA_RADIUS = 2.3 // CarScene's orbit
export const ROAD_Z = -0.29
export const ROAD_WIDTH = 1.15
const CLEAR = CAMERA_RADIUS + 0.6 // tall things stand at least this far from the car

export const ROAD_LEFT = ROAD_Z - ROAD_WIDTH / 2
export const ROAD_RIGHT = ROAD_Z + ROAD_WIDTH / 2

export function plan(seed: number): Piece[] {
  const rng = new Rng(seed)
  const out: Piece[] = []
  const add = (kind: Kind, count: number, span: number, parallax: number) => {
    for (let i = 0; i < count; i++) {
      const p: Piece = {
        kind,
        baseX: -span / 2 + (span * (i + rng.nextFloat() * 0.8)) / count,
        z: 0,
        scale: 1,
        turnDeg: 0,
        shade: 0,
        span,
        parallax,
      }
      reroll(p, rng)
      out.push(p)
    }
  }
  add('ROUND_TREE', 14, 26, 1)
  add('PINE', 10, 26, 1)
  add('CYPRESS', 6, 26, 1)
  add('BUSH', 10, 22, 1)
  add('HOUSE', 7, 30, 1)
  add('FLOWER', 44, 14, 1)
  add('ROCK', 14, 16, 1)
  add('CLOUD', 7, 60, 0.12)
  add('HILL', 9, 120, 0.05)
  return out
}

/** New setback, size, turn and shade for a piece (when it is made, and each time it wraps). */
export function reroll(p: Piece, rng: Rng): void {
  const side = rng.nextBoolean() ? 1 : -1
  const beyond = (min: number, extra: number) => side * (min + rng.nextFloat() * extra)
  switch (p.kind) {
    case 'ROUND_TREE':
    case 'PINE':
    case 'CYPRESS':
      p.z = beyond(CLEAR, 3.5)
      p.scale = 0.8 + rng.nextFloat() * 0.6
      p.shade = rng.nextInt(4)
      break
    case 'BUSH':
      p.z = beyond(CLEAR, 2.5)
      p.scale = 0.7 + rng.nextFloat() * 0.6
      p.shade = rng.nextInt(4)
      break
    case 'HOUSE':
      p.z = beyond(CLEAR + 1.2, 3)
      p.scale = 0.8 + rng.nextFloat() * 0.5
      p.shade = rng.nextInt(6)
      break
    case 'FLOWER': // in the grass beside the road, on either side
      p.z = side > 0 ? ROAD_RIGHT + 0.12 + rng.nextFloat() * 1.6 : ROAD_LEFT - 0.12 - rng.nextFloat() * 1.6
      p.scale = 0.7 + rng.nextFloat() * 0.6
      p.shade = rng.nextInt(5)
      break
    case 'ROCK':
      p.z = side > 0 ? ROAD_RIGHT + 0.15 + rng.nextFloat() * 2 : ROAD_LEFT - 0.15 - rng.nextFloat() * 2
      p.scale = 0.6 + rng.nextFloat() * 0.8
      p.shade = rng.nextInt(3)
      break
    case 'CLOUD':
      p.z = beyond(6, 10)
      p.scale = 0.8 + rng.nextFloat() * 0.9
      p.shade = 0
      break
    case 'HILL':
      p.z = beyond(18, 14)
      p.scale = 3 + rng.nextFloat() * 4
      p.shade = rng.nextInt(3)
      break
  }
  p.turnDeg = rng.nextFloat() * 360
}

/** How tall a piece stands above the ground (units), for the keep-clear rule. */
export function height(kind: Kind): number {
  if (kind === 'FLOWER') return 0.03
  if (kind === 'ROCK') return 0.04
  return 0.3
}

/** True when nothing tall of `pieces` stands inside the camera's circle (a test and a guard). */
export function keepsClear(pieces: Piece[]): boolean {
  return pieces.filter((p) => height(p.kind) > 0.1).every((p) => Math.abs(p.z) > CAMERA_RADIUS + 0.3)
}

/** Which lap a piece is on at `travel` (it changes when the piece wraps around: time to re-roll). */
export function lap(p: Piece, travel: number): number {
  return Math.floor((p.baseX - travel * p.parallax + p.span / 2) / p.span)
}
