import { useEffect, useRef } from 'react'
import { attachCarTarget } from './carHub'
import { CarScene, webglAvailable } from './CarScene'

/**
 * The 3D car's full-screen canvas, behind the board. Shown only while the car is (the trip screen, Car
 * mode's setup); the board's own background is see-through then. Renders nothing without WebGL: the
 * board keeps its backdrop and says the car is here (see carSceneAvailable in facetrack/native.ts).
 */
export function CarStage() {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el || !webglAvailable()) return
    let scene: CarScene
    try {
      scene = new CarScene(el)
    } catch (e) {
      console.warn('carscene: could not start the 3D car', e)
      return
    }
    const detach = attachCarTarget(scene)
    return () => {
      detach()
      scene.destroy()
    }
  }, [])

  return <div ref={ref} aria-hidden className="pointer-events-none fixed inset-0 z-0" style={{ display: 'none' }} />
}
