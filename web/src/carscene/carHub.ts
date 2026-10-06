// The browser's 3D car, reached the same way as the tablet shell's: the page calls these (facetrack/native.ts
// forwards to them when no tablet shell draws the car). The renderer (CarScene) attaches when the board
// mounts; calls made before that are remembered and replayed on attach, so the car is always in the state
// the page last asked for. Pure TypeScript (no three.js), unit tested.

export interface CarTarget {
  show(on: boolean): void
  setLayout(mode: string): void
  setSpeed(mph: number): void
  play(action: string, ms: number, window: string): void
  setPreview(on: boolean): void
}

interface Remembered {
  shown: boolean
  layout: string
  speed: number
  preview: boolean
}

const remembered: Remembered = { shown: false, layout: 'car', speed: 0, preview: false }
let target: CarTarget | null = null

/** Attach the renderer; returns the detach function (called when the board unmounts). */
export function attachCarTarget(t: CarTarget): () => void {
  target = t
  t.setLayout(remembered.layout)
  t.setSpeed(remembered.speed)
  t.setPreview(remembered.preview)
  t.show(remembered.shown)
  return () => {
    if (target === t) target = null
  }
}

export const browserCar = {
  show(on: boolean): void {
    remembered.shown = on
    target?.show(on)
  },
  setLayout(mode: string): void {
    remembered.layout = mode
    target?.setLayout(mode)
  },
  setSpeed(mph: number): void {
    remembered.speed = mph
    target?.setSpeed(mph)
  },
  setPreview(on: boolean): void {
    remembered.preview = on
    target?.setPreview(on)
  },
  play(action: string, ms: number, window: string): void {
    target?.play(action, ms, window)
  },
}

/** For tests: forget what was remembered and any attached renderer. */
export function resetCarHub(): void {
  target = null
  Object.assign(remembered, { shown: false, layout: 'car', speed: 0, preview: false })
}
