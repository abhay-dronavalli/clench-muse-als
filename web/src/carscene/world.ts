// The trip scene's world, built from worldPlan.ts: grass, the road with its lines, and a random mix of
// round trees, pines, cypresses, bushes, houses, flowers, rocks, clouds and far hills, plus a sun. Each
// frame `update` moves everything past the parked car (far things slower, clouds drifting on their own)
// and re-rolls whatever has just wrapped around behind it. A port of the tablet's CarWorld.kt.

import * as THREE from 'three'
import * as WorldPlan from './worldPlan'
import { Rng } from './worldPlan'
import { wrap } from './motion'

const DASHES = 24
const DASH_GAP = 0.75
const CLOUD_DRIFT = 0.04 // units a second the clouds drift on their own

const LEAVES: [number, number, number][] = [
  [52, 112, 58],
  [66, 130, 60],
  [92, 142, 62],
  [44, 100, 64],
]
const PINES: [number, number, number][] = [
  [30, 86, 56],
  [38, 96, 62],
  [28, 76, 50],
  [46, 104, 66],
]
const WALLS: [number, number, number][] = [
  [238, 226, 204],
  [214, 229, 240],
  [245, 214, 196],
  [222, 238, 214],
  [250, 238, 190],
  [230, 215, 236],
]
const ROOFS: [number, number, number][] = [
  [164, 72, 56],
  [112, 92, 82],
  [82, 88, 98],
]
const FLOWERS: [number, number, number][] = [
  [240, 92, 112],
  [250, 200, 60],
  [255, 255, 255],
  [172, 122, 232],
  [255, 142, 62],
]
const ROCKS: [number, number, number][] = [
  [140, 140, 136],
  [120, 118, 112],
  [160, 156, 148],
]
const HILLS: [number, number, number][] = [
  [110, 172, 92],
  [96, 160, 84],
  [124, 182, 100],
]

/** A colour from sRGB bytes (what the tablet's Color.rgb gives), roughness as the tablet's material. */
export function material(r: number, g: number, b: number, roughness = 0.85): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color().setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace),
    roughness,
    metalness: 0,
  })
}

interface Placed {
  piece: WorldPlan.Piece
  node: THREE.Object3D
  lap: number
}

export class CarWorld {
  readonly root = new THREE.Group()
  private readonly rng: Rng
  private readonly materials = new Map<string, THREE.MeshStandardMaterial>()
  private readonly placed: Placed[] = []
  private readonly dashes: THREE.Object3D[] = []
  private readonly ground: number

  constructor(ground: number, seed: number) {
    this.ground = ground
    this.rng = new Rng(seed)
    const g = ground
    this.add(this.box(90, 0.02, 90, this.color('grass', 104, 170, 84)), 0, g - 0.011, 0)
    this.add(this.box(90, 0.004, WorldPlan.ROAD_WIDTH, this.color('road', 72, 74, 80)), 0, g + 0.001, WorldPlan.ROAD_Z)
    for (const edge of [WorldPlan.ROAD_LEFT + 0.03, WorldPlan.ROAD_RIGHT - 0.03]) {
      this.add(this.box(90, 0.005, 0.018, this.color('paint', 242, 242, 236)), 0, g + 0.002, edge)
    }
    for (let i = 0; i < DASHES; i++) {
      const dash = this.box(0.3, 0.005, 0.022, this.color('paint', 242, 242, 236))
      this.root.add(dash)
      this.dashes.push(dash)
    }
    // The sun: far away, high, off to one side.
    const sun = new THREE.Mesh(new THREE.SphereGeometry(2.4, 20, 16), this.color('sun', 255, 236, 150))
    this.add(sun, 38, 16, -42)
    for (const piece of WorldPlan.plan(seed)) {
      const node = this.build(piece)
      this.root.add(node)
      this.placed.push({ piece, node, lap: WorldPlan.lap(piece, 0) })
      this.place(piece, node, 0, 0)
    }
  }

  /** Move the world for `travel` (how far the car has gone) and `seconds` (clouds drift in time). */
  update(travel: number, seconds: number): void {
    const dashSpan = DASHES * DASH_GAP
    this.dashes.forEach((d, i) => {
      d.position.set(wrap(-dashSpan / 2 + i * DASH_GAP, travel, dashSpan), this.ground + 0.002, WorldPlan.ROAD_Z)
    })
    for (const p of this.placed) {
      const drift = p.piece.kind === 'CLOUD' ? seconds * CLOUD_DRIFT : 0
      const lap = WorldPlan.lap(p.piece, travel + drift / p.piece.parallax)
      if (lap !== p.lap) {
        // Just wrapped around behind the car: a fresh look for its next pass.
        p.lap = lap
        WorldPlan.reroll(p.piece, this.rng)
      }
      this.place(p.piece, p.node, travel, drift)
    }
  }

  dispose(): void {
    this.root.traverse((o) => {
      if (o instanceof THREE.Mesh) o.geometry.dispose()
    })
    this.materials.forEach((m) => m.dispose())
    this.materials.clear()
  }

  private place(p: WorldPlan.Piece, node: THREE.Object3D, travel: number, drift: number): void {
    const x = wrap(p.baseX, travel * p.parallax + drift, p.span)
    const y =
      p.kind === 'CLOUD' ? 2.4 + 0.5 * p.scale : p.kind === 'HILL' ? this.ground - 0.72 * p.scale : this.ground
    node.position.set(x, y, p.z)
    node.scale.setScalar(p.scale)
    const turn =
      p.kind === 'HOUSE' ? (p.z > 0 ? 180 : 0) + ((p.turnDeg % 24) - 12) : p.turnDeg
    node.rotation.set(0, THREE.MathUtils.degToRad(turn), 0)
  }

  // --- the pieces ---------------------------------------------------------------------------

  private build(p: WorldPlan.Piece): THREE.Object3D {
    const n = new THREE.Group()
    const add = (mesh: THREE.Object3D, x = 0, y = 0, z = 0) => {
      mesh.position.set(x, y, z)
      n.add(mesh)
    }
    switch (p.kind) {
      case 'ROUND_TREE':
        add(this.cylinder(0.025, 0.24, 10, this.bark()), 0, 0.12, 0)
        add(this.sphere(0.17, 18, 14, this.leaf(p.shade)), 0, 0.37, 0)
        break
      case 'PINE':
        add(this.cylinder(0.02, 0.1, 8, this.bark()), 0, 0.05, 0)
        // Tiers narrowing upward: a stylised pine.
        for (const [r, y] of [
          [0.16, 0.12],
          [0.12, 0.2],
          [0.085, 0.28],
          [0.045, 0.35],
        ]) {
          add(this.cylinder(r, 0.09, 12, this.pine(p.shade)), 0, y, 0)
        }
        break
      case 'CYPRESS': {
        add(this.cylinder(0.018, 0.08, 8, this.bark()), 0, 0.04, 0)
        const tall = new THREE.Group()
        tall.position.set(0, 0.3, 0)
        tall.scale.set(1, 2.6, 1)
        tall.add(this.sphere(0.09, 16, 12, this.pine(p.shade)))
        n.add(tall)
        break
      }
      case 'BUSH':
        for (const [c, r] of [
          [[0, 0.07, 0], 0.1],
          [[0.09, 0.05, 0.03], 0.075],
          [[-0.08, 0.05, -0.03], 0.08],
        ] as [[number, number, number], number][]) {
          add(this.sphere(r, 14, 12, this.leaf(p.shade)), c[0], c[1], c[2])
        }
        break
      case 'HOUSE': {
        const wall = this.pick('wall', p.shade, WALLS)
        add(this.box(0.5, 0.3, 0.4, wall), 0, 0.15, 0)
        // A pitched roof: a long box turned 45° about its length.
        const roof = new THREE.Group()
        roof.position.set(0, 0.3, 0)
        roof.rotation.set(THREE.MathUtils.degToRad(45), 0, 0)
        roof.add(this.box(0.56, 0.28, 0.28, this.pick('roof', p.shade, ROOFS)))
        n.add(roof)
        add(this.box(0.08, 0.13, 0.01, this.color('door', 110, 76, 56)), 0.08, 0.065, 0.201)
        add(this.box(0.09, 0.07, 0.01, this.color('glass', 190, 220, 240)), -0.12, 0.17, 0.201)
        break
      }
      case 'FLOWER':
        add(this.cylinder(0.003, 0.03, 6, this.color('stem', 70, 130, 60)), 0, 0.015, 0)
        add(this.sphere(0.012, 10, 8, this.pick('flower', p.shade, FLOWERS)), 0, 0.033, 0)
        break
      case 'ROCK': {
        const rock = new THREE.Group()
        rock.scale.set(1.2, 0.6, 1)
        const stone = this.sphere(0.035, 10, 8, this.pick('rock', p.shade, ROCKS))
        stone.position.set(0, 0.01, 0)
        rock.add(stone)
        n.add(rock)
        break
      }
      case 'CLOUD':
        for (const [c, r] of [
          [[0, 0, 0], 0.5],
          [[0.55, -0.1, 0.1], 0.38],
          [[-0.5, -0.12, -0.05], 0.4],
          [[0.2, 0.2, 0], 0.34],
        ] as [[number, number, number], number][]) {
          add(this.sphere(r, 16, 12, this.color('cloud', 255, 255, 255)), c[0], c[1], c[2])
        }
        break
      case 'HILL':
        n.add(this.sphere(1, 24, 18, this.pick('hill', p.shade, HILLS)))
        break
    }
    return n
  }

  // --- helpers ------------------------------------------------------------------------------

  private add(mesh: THREE.Object3D, x: number, y: number, z: number): void {
    mesh.position.set(x, y, z)
    this.root.add(mesh)
  }

  private box(w: number, h: number, d: number, m: THREE.Material): THREE.Mesh {
    return new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m)
  }

  /** Round, standing on its base at the origin (like CylinderNode's centre). */
  private cylinder(radius: number, height: number, sides: number, m: THREE.Material): THREE.Mesh {
    return new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, height, sides), m)
  }

  private sphere(radius: number, slices: number, stacks: number, m: THREE.Material): THREE.Mesh {
    return new THREE.Mesh(new THREE.SphereGeometry(radius, slices, stacks), m)
  }

  private bark() {
    return this.color('bark', 110, 78, 52)
  }

  private leaf(shade: number) {
    return this.pick('leaf', shade, LEAVES)
  }

  private pine(shade: number) {
    return this.pick('pine', shade, PINES)
  }

  private pick(key: string, shade: number, palette: [number, number, number][]) {
    const [r, g, b] = palette[shade % palette.length]
    return this.color(`${key}${shade}`, r, g, b)
  }

  private color(key: string, r: number, g: number, b: number): THREE.MeshStandardMaterial {
    let m = this.materials.get(key)
    if (!m) {
      m = material(r, g, b)
      this.materials.set(key, m)
    }
    return m
  }
}
