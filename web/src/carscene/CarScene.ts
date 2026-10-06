// The trip screen's 3D car in a normal browser (three.js): a port of the tablet's CarScene.kt (SceneView /
// Filament). A daytime drive through a random world (world.ts): the car stays put facing down the road
// while the world moves past it and wraps around, and the camera makes one slow circle around the car
// every 45 s. The page (board/BoardPage.tsx) drives it through the same calls the tablet shell gets
// (carScene, carEffect, carSpeed, carLayout, carPreview; see carHub.ts), so the browser shows the same
// car, scenery, and motion as the tablet.
//
// The model is public/models/jaguar_i-pace.glb (copied by scripts/car-model.mjs from the tablet's assets,
// not in git). Without it the scene shows the road alone, as the tablet does.

import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import * as Motion from './motion'
import { CarWorld, material } from './world'

export const MODEL_URL = '/models/jaguar_i-pace.glb'

const CRUISE_MPH = 32 // the scenery's DRIVE_SPEED is this speed
const DRIVE_EASE_S = 0.6 // how gently the camera and scenery hold still and resume
const SPEED_EASE_S = 1.2 // how gently the scenery follows a new speed
const STILL_AFTER_MS = 2500 // the car stays stopped this long after Pull over
const CAR_LIFT = 0.006 // tyres on the road's surface (its top is ~0.0045 above ground)
// Lens shifts, measured on the tablet: 0.3 moves the car a quarter of the screen's width.
const SHIFT_TO_NDC = 5 / 3
const CAR_SHIFT = 0.06 // past the page's 288 px telemetry column
const SPLIT_SHIFT = 0.33 // the middle of the right half beside the column and the map
const LAYOUT_EASE_S = 0.4
// The camera circles a little above the car, looking gently down at LOOK_Y below it: a band of sky at
// the top, the horizon near 15% down, and the car at about 17-35% down the screen, above the tiles.
const ORBIT_RADIUS = 2.3
const ORBIT_HEIGHT = 0.2
const LOOK_Y = -0.52
const PUSH_SCALE = 0.9 // the push, as a share of the orbit radius
const FOV_V = 45 // vertical field of view, degrees (the tablet's lens is close to this)
const PREVIEW_RADIUS = 1.9
const PREVIEW_HEIGHT = 0.45

const DAY_SKY = new THREE.Color().setRGB(0.42, 0.64, 0.92, THREE.SRGBColorSpace)
const PLAIN_BACKDROP = new THREE.Color().setRGB(0.8, 0.86, 0.88, THREE.SRGBColorSpace)

const LINE_COLORS: Record<string, [number, number, number]> = {
  window_up: [255, 255, 255], // white against the sky and the paint
  window_down: [255, 255, 255],
  warmer: [255, 140, 60], // warm amber
  cooler: [60, 150, 255], // cool blue
  louder: [150, 110, 255], // violet
  softer: [150, 110, 255],
}

interface Effect {
  action: string
  window: string
  startMs: number
  durationMs: number
  lines: THREE.Mesh[]
}

/** Whether this browser can draw the 3D car (WebGL). */
export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas')
    return !!(canvas.getContext('webgl2') || canvas.getContext('webgl'))
  } catch {
    return false
  }
}

export class CarScene {
  private readonly renderer: THREE.WebGLRenderer
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.PerspectiveCamera(FOV_V, 1, 0.05, 400)
  private readonly stage = new THREE.Group()
  private readonly lineGeometry = new THREE.BoxGeometry(0.01, Motion.LINE_LENGTH, 0.01)
  private readonly lineMaterials = new Map<string, THREE.MeshStandardMaterial>()
  private readonly observer: ResizeObserver | null

  private car: THREE.Object3D | null = null
  private box: Motion.Box = Motion.DEFAULT_BOX
  private loading = false
  private world: CarWorld | null = null
  private effect: Effect | null = null
  private preview = false
  private raf = 0
  private lastMs = 0
  private seed = Math.floor(Math.random() * 0x7fffffff) // a different world every time the page opens

  private seconds = 0 // scene time, for the clouds' own drift
  private travel = 0 // how far the world has moved past the car
  private drive = 1 // 1 = moving, 0 = held still (Pull over's moment); eases between the two
  private speed = 1 // the scenery's speed as a share of cruising, eased
  private speedTarget = 1
  private stillUntilMs = 0
  private orbitDeg = Motion.ORBIT_START_DEG
  private shift = 0 // horizontal lens shift: the car in the right half in the split layout
  private shiftTarget = CAR_SHIFT

  private readonly container: HTMLElement
  private readonly modelUrl: string

  constructor(container: HTMLElement, modelUrl = MODEL_URL) {
    this.container = container
    this.modelUrl = modelUrl
    this.renderer = new THREE.WebGLRenderer({ antialias: true })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.domElement.style.display = 'block'
    this.renderer.domElement.style.width = '100%'
    this.renderer.domElement.style.height = '100%'
    container.appendChild(this.renderer.domElement)
    this.scene.background = DAY_SKY
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x6f8f5a, 1.4))
    const sun = new THREE.DirectionalLight(0xffffff, 2.2)
    sun.position.set(0.6, 1, -0.7) // the same side as the sun in the world
    this.scene.add(sun)
    this.scene.add(this.stage)
    this.observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => this.resize())
    this.observer?.observe(container)
    this.placeCamera(0)
    this.applyLook()
  }

  /** Show or hide the scene (the trip screen is up, or not). The first show loads the car and its world. */
  show(on: boolean): void {
    this.container.style.display = on ? 'block' : 'none'
    if (on) {
      this.resize()
      if (!this.car && !this.world && !this.loading) this.load()
      if (!this.raf) {
        this.lastMs = 0
        this.raf = requestAnimationFrame(this.tick)
      }
    } else if (this.raf) {
      cancelAnimationFrame(this.raf)
      this.raf = 0
    }
  }

  /** The page's trip layout: the car recentres in the right half for "split". */
  setLayout(mode: string): void {
    this.shiftTarget = mode === 'split' ? SPLIT_SHIFT : CAR_SHIFT
  }

  /** The onboarding's preview: just the car, on a soft light platform. Off, the car drives its world. */
  setPreview(on: boolean): void {
    this.preview = on
    if (on) this.clearEffect()
    this.applyLook()
  }

  /** The car's speed (CAR_STATE): the road and trees move at it; 0 = stopped. */
  setSpeed(mph: number): void {
    this.speedTarget = Math.min(2, Math.max(0, mph / CRUISE_MPH))
  }

  /** Play a control's effect for `ms` (CAR_ACTION); `window` for the window controls. */
  play(action: string, ms: number, window = 'all'): void {
    this.clearEffect()
    const now = performance.now()
    const which = window || 'all'
    const lines = Motion.lines(action, 0, this.box, which).map(() => this.newLine(action))
    lines.forEach((l) => this.scene.add(l))
    this.effect = { action, window: which, startMs: now, durationMs: ms, lines }
    if (action === 'pull_over') this.stillUntilMs = now + ms + STILL_AFTER_MS
  }

  destroy(): void {
    this.show(false)
    this.clearEffect()
    this.observer?.disconnect()
    this.world?.dispose()
    this.lineGeometry.dispose()
    this.lineMaterials.forEach((m) => m.dispose())
    this.stage.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.geometry.dispose()
        ;(o.material as THREE.Material).dispose()
      }
    })
    this.renderer.dispose()
    this.renderer.domElement.remove()
  }

  // --- the world -------------------------------------------------------------------------

  private get ground(): number {
    return -this.box.halfHeight // the model is centred: its wheels touch y = -halfHeight
  }

  private buildWorld(): void {
    if (this.world) return
    this.world = new CarWorld(this.ground, this.seed)
    this.scene.add(this.world.root)
    this.buildStage()
    this.applyLook()
  }

  /** A soft round platform under the car and a darker oval under its body (preview only). */
  private buildStage(): void {
    const platform = new THREE.Mesh(
      new THREE.CylinderGeometry(1.1, 1.1, 0.01, 64),
      material(226, 232, 234, 0.95),
    )
    platform.position.set(0, this.ground - 0.005, 0)
    const shadow = new THREE.Group()
    shadow.position.set(0, this.ground + 0.0005, 0)
    shadow.scale.set(1, 1, 0.5)
    shadow.add(new THREE.Mesh(new THREE.CylinderGeometry(0.6, 0.6, 0.002, 48), material(188, 196, 200, 0.95)))
    this.stage.add(platform, shadow)
  }

  /** The world for the app, the platform for the preview. */
  private applyLook(): void {
    this.scene.background = this.preview ? PLAIN_BACKDROP : DAY_SKY
    if (this.world) this.world.root.visible = !this.preview
    this.stage.visible = this.preview
  }

  /** Load the car's model; a missing or broken model leaves the road alone (the tablet does the same). */
  private load(): void {
    this.loading = true
    new GLTFLoader().load(
      this.modelUrl,
      (gltf) => {
        this.loading = false
        const model = gltf.scene
        const bounds = new THREE.Box3().setFromObject(model)
        const size = bounds.getSize(new THREE.Vector3())
        const center = bounds.getCenter(new THREE.Vector3())
        model.position.sub(center) // centred on the origin
        const longest = Math.max(size.x, size.y, size.z) || 1
        const holder = new THREE.Group()
        holder.add(model)
        holder.scale.setScalar(1 / longest) // the longest side is 1 unit, as on the tablet
        // Face down the road (+x); a model built along z is turned a quarter. Lifted a hair so the tyres
        // sit on the road's surface instead of sinking into it.
        holder.rotation.y = size.z > size.x ? Math.PI / 2 : 0
        holder.position.y = CAR_LIFT
        this.scene.add(holder)
        this.car = holder
        this.box = Motion.boxFromSize([size.x, size.y, size.z])
        this.buildWorld()
      },
      undefined,
      (err) => {
        this.loading = false
        console.warn(`carscene: no car model at ${this.modelUrl} (${err instanceof Error ? err.message : String(err)}); the road only`)
        this.buildWorld()
      },
    )
  }

  // --- every frame -----------------------------------------------------------------------

  private tick = (now: number): void => {
    this.raf = requestAnimationFrame(this.tick)
    this.frame(now)
  }

  private frame(now: number): void {
    const dt = this.lastMs === 0 ? 0 : Math.min(0.1, Math.max(0, (now - this.lastMs) / 1000))
    this.lastMs = now
    if (this.preview) {
      // The parked world's platform; the camera stays on its three-quarter view (no gaze to turn it here).
      const [x, y, z] = Motion.orbit(Motion.ORBIT_START_DEG, PREVIEW_RADIUS, PREVIEW_HEIGHT)
      this.camera.position.set(x, y, z)
      this.camera.lookAt(0, CAR_LIFT, 0)
      this.applyShift(0)
      this.renderer.render(this.scene, this.camera)
      return
    }
    // Cruising, or stopped for Pull over; always eased, never a jolt.
    const target = now < this.stillUntilMs ? 0 : 1
    this.drive += (target - this.drive) * (1 - Math.exp(-dt / DRIVE_EASE_S))
    this.speed += (this.speedTarget - this.speed) * (1 - Math.exp(-dt / SPEED_EASE_S))
    this.travel += Motion.DRIVE_SPEED * this.speed * this.drive * dt
    this.orbitDeg += (360 / Motion.ORBIT_PERIOD_S) * this.drive * dt
    this.seconds += dt
    this.world?.update(this.travel, this.seconds)
    this.shift += (this.shiftTarget - this.shift) * (1 - Math.exp(-dt / LAYOUT_EASE_S))
    this.applyShift(this.shift)

    let push = 0
    const e = this.effect
    if (e) {
      const progress = Math.min(1, Math.max(0, (now - e.startMs) / e.durationMs))
      push = Motion.push(e.action, progress)
      const current = Motion.lines(e.action, progress, this.box, e.window)
      e.lines.forEach((node, i) => {
        const l = current[i]
        node.position.set(l.x, l.y, l.z)
        node.rotation.set(0, 0, THREE.MathUtils.degToRad(l.tiltDeg))
        node.scale.set(1, Math.max(l.length / Motion.LINE_LENGTH, 0.0001), 1)
        node.visible = l.length > 1e-4
      })
      if (progress >= 1 && e.action !== 'pull_over') this.clearEffect()
    }
    if (e?.action === 'pull_over' && now >= this.stillUntilMs) this.clearEffect()
    this.placeCamera(push)
    this.renderer.render(this.scene, this.camera)
  }

  /** The camera on its circle, pushed `push` toward the car, always looking just below it. */
  private placeCamera(push: number): void {
    const [x, y, z] = Motion.orbit(this.orbitDeg, ORBIT_RADIUS * (1 - PUSH_SCALE * push), ORBIT_HEIGHT)
    this.camera.position.set(x, y, z)
    this.camera.lookAt(0, LOOK_Y, 0)
  }

  /** A horizontal lens shift: the projection's centre moves, the car moves with it (no camera turn). */
  private applyShift(shift: number): void {
    this.camera.updateProjectionMatrix()
    // Shifting the projection's x by d moves the image by -d in clip space; the sign puts the car right.
    this.camera.projectionMatrix.elements[8] -= shift * SHIFT_TO_NDC
  }

  private resize(): void {
    const w = this.container.clientWidth
    const h = this.container.clientHeight
    if (!w || !h) return
    this.renderer.setSize(w, h, false)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
  }

  // --- effects ---------------------------------------------------------------------------

  private newLine(action: string): THREE.Mesh {
    const [r, g, b] = LINE_COLORS[action] ?? [200, 200, 200]
    const key = `line-${action}`
    let m = this.lineMaterials.get(key)
    if (!m) {
      m = material(r, g, b, 0.85)
      this.lineMaterials.set(key, m)
    }
    const mesh = new THREE.Mesh(this.lineGeometry, m)
    mesh.visible = false
    return mesh
  }

  private clearEffect(): void {
    const e = this.effect
    if (!e) return
    this.effect = null
    e.lines.forEach((l) => this.scene.remove(l))
  }
}
